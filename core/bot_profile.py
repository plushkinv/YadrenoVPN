"""Installation bot branding; one atomic cache, independent of UI publications."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re

from web_tools.paths import atomic_write, canonical, local_path

REFRESH_SECONDS = 300
logger = logging.getLogger(__name__)


def _cache_file():
    from web_tools.paths import PROJECT_ROOT
    return local_path(PROJECT_ROOT / 'web_runtime', 'bot-profile.json')


def _load():
    from config import BOT_TOKEN
    try:
        value = json.loads(_cache_file().read_bytes())
        if value['bot_id'] != int(BOT_TOKEN.partition(':')[0]):
            return {}
        if not isinstance(value['name'], str) or not value['name'].strip():
            return {}
        photo = base64.b64decode(value['photo'], validate=True) if value['photo'] else None
        if photo and hashlib.sha256(photo).hexdigest() != value['photo_hash']:
            return {}
        return {**value, 'photo': photo}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def presentation():
    value = _load()
    return {'title': value.get('name', 'Личный кабинет'),
            'logo': '/ui/bot-avatar/' + value['photo_hash'] + '.jpg' if value.get('photo') else None}


def avatar(content_hash):
    if not re.fullmatch(r'[a-f0-9]{64}', content_hash):
        return None
    value = _load()
    return value.get('photo') if value.get('photo_hash') == content_hash else None


async def refresh(bot):
    """Commit only a complete profile; transport failure retains the last snapshot."""
    from config import BOT_TOKEN
    from PIL import Image

    async with asyncio.timeout(15):
        info = await bot.get_me()
        if info.id != int(BOT_TOKEN.partition(':')[0]) or not info.first_name.strip():
            raise ValueError('unexpected bot identity')
        photos = await bot.get_user_profile_photos(info.id, limit=1)
        previous = _load()
        photo, unique_id = None, None
        if photos.photos:
            sizes = photos.photos[0]
            candidates = [item for item in sizes if min(item.width, item.height) >= 84]
            selected = min(candidates, key=lambda item: item.width * item.height) if candidates else sizes[-1]
            unique_id = selected.file_unique_id
            if previous.get('photo_unique_id') == unique_id and previous.get('photo'):
                photo = previous['photo']
            else:
                file = await bot.get_file(selected.file_id)
                if not file.file_path:
                    raise ValueError('missing avatar file')
                with io.BytesIO() as stream:
                    await bot.download_file(file.file_path, destination=stream, timeout=10)
                    photo = stream.getvalue()
                with Image.open(io.BytesIO(photo)) as image:
                    if image.format != 'JPEG':
                        raise ValueError('unexpected avatar format')
                    image.verify()
        value = {'bot_id': info.id, 'name': info.first_name.strip(), 'photo_unique_id': unique_id,
                 'photo_hash': hashlib.sha256(photo).hexdigest() if photo else None,
                 'photo': base64.b64encode(photo).decode('ascii') if photo else None}
        if value != {**previous, 'photo': base64.b64encode(previous['photo']).decode('ascii') if previous.get('photo') else None}:
            atomic_write(_cache_file(), canonical(value))


async def run_profile_refresh(bot):
    """The owning runtime cancels this task on shutdown; HTTP never awaits Telegram."""
    from database.requests import get_setting
    initial = True
    while True:
        try:
            if initial or get_setting('web_enabled', '0') == '1':
                await refresh(bot)
        except Exception as error:
            logger.warning('Bot profile refresh unavailable type=%s', type(error).__name__)
        initial = False
        await asyncio.sleep(REFRESH_SECONDS)
