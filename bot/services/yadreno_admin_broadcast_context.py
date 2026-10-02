"""Request-local bot identity and snapshots of the existing broadcast editor."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from aiogram.exceptions import TelegramAPIError

from bot.services.broadcast_editor import get_broadcast_editor_state
from bot.utils.telegram_links import build_telegram_link
from runtime.delivery import get_bot

logger = logging.getLogger(__name__)


class BroadcastRuntimeContextError(RuntimeError):
    """A complete fresh broadcast editor snapshot could not be read."""


async def load_broadcast_bot_identity() -> dict[str, str | None]:
    """Read public identity once per request, without retaining another cache."""
    identity: dict[str, str | None] = {"name": None, "username": None, "url": None}
    bot = get_bot()
    if bot is None:
        return identity
    try:
        info = await bot.get_me()
    except (TelegramAPIError, asyncio.TimeoutError) as error:
        logger.warning("Broadcast bot identity unavailable: %s", type(error).__name__)
        return identity
    name = info.full_name
    username = info.username
    if isinstance(name, str):
        identity["name"] = name.strip() or None
    if isinstance(username, str):
        identity["username"] = username.strip().lstrip("@") or None
    if identity["username"] is not None:
        identity["url"] = build_telegram_link(identity["username"])
    return identity


def build_broadcast_runtime_context(
    telegram_id: int,
    bot_identity: dict[str, str | None] | None,
) -> dict[str, Any]:
    """Reuse the canonical aggregate state, never recipient data or media ids."""
    try:
        state = get_broadcast_editor_state(telegram_id)
    except (sqlite3.Error, OSError, TypeError, ValueError) as error:
        raise BroadcastRuntimeContextError("Broadcast editor snapshot unavailable") from error
    return {
        "surface": "admin.broadcast",
        "task_format": "telegram_html",
        "bot": dict(bot_identity or {"name": None, "username": None, "url": None}),
        "broadcast": state,
    }
