"""Authenticated transfer of original Hub images and request-local delivery receipts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from weakref import WeakValueDictionary

import aiohttp

from database.requests import get_setting, list_yadreno_admin_pending_images, set_setting

CAPABILITY = "image_files_v1"
EXTENSIONS = {"image/png": (".png",), "image/jpeg": (".jpg", ".jpeg"), "image/webp": (".webp",)}
logger = logging.getLogger(__name__)
_delivery_locks = WeakValueDictionary()


class ImageError(ValueError):
    """An image cannot be delivered or installed."""


class HubEvent(dict):
    """Attach the actual trusted response origin outside the wire data."""

    def __init__(self, data, origin):
        super().__init__(data)
        self.origin = origin


@dataclass(frozen=True)
class HubImage:
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    download_path: str

    @classmethod
    def parse(cls, data, request_id):
        if not isinstance(data, dict):
            raise ImageError("Invalid image descriptor")
        file_id, mime, size = (data.get(k) for k in ("file_id", "mime_type", "size_bytes"))
        if not isinstance(file_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", file_id):
            raise ImageError("Invalid image identifier")
        path = f"/api/v1/satellite/requests/{request_id}/files/{file_id}"
        digest = data.get("sha256")
        if (data.get("download_path") != path or not isinstance(mime, str) or mime not in EXTENSIONS
                or type(size) is not int or size <= 0 or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise ImageError("Invalid image metadata or request binding")
        name = data.get("filename")
        if (not isinstance(name, str) or not name.strip() or len(name) > 240
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(c in name for c in '/\\:"') or Path(name).suffix.lower() not in EXTENSIONS[mime]):
            name = file_id + EXTENSIONS[mime][0]
        return cls(file_id, name, mime, size, digest.lower(), path)


def _key(actor, topic):
    return f"yadreno_admin_image_request:{actor}:{topic}"


def _key_digest(api_key):
    return hashlib.sha256(api_key.strip().encode()).hexdigest()


def _state(key):
    try:
        value = json.loads(get_setting(key) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def pending_deliveries(api_key):
    return list_yadreno_admin_pending_images(_key_digest(api_key))


def bind_request(actor, topic, request_id, api_key, *, destination=None):
    """Persist the negotiation before polling; retain only the latest binding per lane."""
    key = _key(actor, topic)
    digest = _key_digest(api_key)
    state = _state(key)
    if (state.get("request"), state.get("key")) != (request_id, digest):
        set_setting(key, json.dumps({"request": request_id, "key": digest, "sent": [], "destination": destination}))


@dataclass(frozen=True)
class ImageRequest:
    request_id: int
    state_key: str
    key_digest: str
    origin: str

    @classmethod
    def from_event(cls, event, actor, topic, request_id, api_key):
        if (not isinstance(event, HubEvent) or type(event.get("request_id")) is not int
                or event["request_id"] != request_id):
            return None
        key = _key(actor, topic)
        digest = _key_digest(api_key)
        state = _state(key)
        if (state.get("request"), state.get("key")) != (request_id, digest):
            return None
        return cls(request_id, key, digest, event.origin)

    def delivery_lock(self):
        return _delivery_locks.setdefault((self.state_key, self.request_id), asyncio.Lock())

    def destination(self, default_chat):
        state = _state(self.state_key)
        value = state.get("destination")
        if ((state.get("request"), state.get("key")) == (self.request_id, self.key_digest)
                and isinstance(value, list) and len(value) == 2 and type(value[0]) is int
                and (value[1] is None or type(value[1]) is int)):
            return value[0], value[1]
        return default_chat, None

    def mark_pending(self, pending):
        state = _state(self.state_key)
        if ((state.get("request"), state.get("key")) == (self.request_id, self.key_digest)
                and bool(state.get("pending")) != pending):
            state["pending"] = pending
            set_setting(self.state_key, json.dumps(state))

    def sent(self, chat_id, file_id, *, confirm=False):
        state = _state(self.state_key)
        if (state.get("request"), state.get("key")) != (self.request_id, self.key_digest):
            return False
        sent = state.get("sent") or []
        item = [chat_id, file_id]
        if item in sent:
            return True
        if confirm:
            state["sent"] = [*sent, item]
            set_setting(self.state_key, json.dumps(state))
        return False

    @asynccontextmanager
    async def download(self, session, api_key, image):
        """Yield a verified private original. Never follow redirects with credentials."""
        from bot.services import yadreno_admin as satellite
        if (self.key_digest != _key_digest(api_key) or self.origin not in satellite.HUB_URLS
                or image.download_path != f'/api/v1/satellite/requests/{self.request_id}/files/{image.file_id}'):
            raise ImageError("Image download authorization changed")
        headers = satellite._hub_headers(api_key)
        directory = satellite.TMP_DIR
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            target = Path(temporary) / "original"
            delays = satellite.RETRY_CONFIG.get("delays") or [1]
            for attempt in range(satellite._hub_attempt_count()):
                try:
                    async with session.get(self.origin + image.download_path, headers=headers,
                                           allow_redirects=False) as response:
                        if response.status != 200:
                            if response.status in {408, 429} or response.status >= 500:
                                raise aiohttp.ClientConnectionError(f"HTTP {response.status}")
                            raise ImageError(f"Image download returned HTTP {response.status}")
                        digest, size = hashlib.sha256(), 0
                        with target.open("wb") as stream:
                            async for chunk in response.content.iter_chunked(65536):
                                size += len(chunk)
                                if size > image.size_bytes:
                                    raise ImageError("Image size mismatch")
                                stream.write(chunk)
                                digest.update(chunk)
                        if size != image.size_bytes or digest.hexdigest() != image.sha256:
                            raise ImageError("Image integrity check failed")
                    break
                except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                    if attempt + 1 == satellite._hub_attempt_count():
                        raise ImageError("Image download failed") from error
                    await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
            yield target


async def telegram_photo_id(request, session, api_key, image, admin_id):
    """Obtain a reusable photo ID from this installation's bot, before committing a mutation."""
    from aiogram.exceptions import TelegramAPIError
    from aiogram.types import FSInputFile
    from runtime.delivery import get_bot

    bot = get_bot()
    if not bot or admin_id <= 0:
        raise ImageError("Telegram administrator photo upload unavailable")
    async with request.download(session, api_key, image) as original:
        try:
            message = await bot.send_photo(chat_id=admin_id, photo=FSInputFile(original, filename=image.filename),
                                           disable_notification=True)
        except TelegramAPIError as error:
            raise ImageError("Telegram rejected the draft photo") from error
        try:
            if not message.photo:
                raise ImageError("Telegram did not return a photo ID")
            return message.photo[-1].file_id
        finally:
            try:
                await bot.delete_message(chat_id=admin_id, message_id=message.message_id)
            except TelegramAPIError as error:
                logger.warning("Draft photo cleanup failed: %s", type(error).__name__)


def uses_image(tool, args):
    if not isinstance(args, dict):
        return False
    if tool == "satellite_broadcast_editor":
        return args.get("action") == "stage_message" and args.get("media_action") == "replace"
    if tool == "satellite_write_file":
        return "image" in args
    return (tool == "satellite_customization_apply" and args.get("operation") == "page.patch"
            and isinstance(args.get("changes"), list)
            and any(isinstance(c, dict) and isinstance(c.get("image"), dict) for c in args["changes"]))


async def resolve_page_images(args, request, session, api_key, admin_id):
    """Prepare every requested photo before passing the complete patch to atomic page validation."""
    changes = []
    photo_ids = {}
    for change in args["changes"]:
        if isinstance(change, dict) and isinstance(change.get("image"), dict):
            if (set(change) != {"type", "media_type", "image"} or change["type"] != "media.set"
                    or change["media_type"] != "photo"):
                raise ImageError("Generated images require media.set with media_type=photo")
            image = HubImage.parse(change["image"], request.request_id)
            if image not in photo_ids:
                photo_ids[image] = await telegram_photo_id(request, session, api_key, image, admin_id)
            change = {**change, "image": photo_ids[image]}
        changes.append(change)
    return {**args, "changes": changes}
