"""Safe Telegram callback acknowledgement helpers."""
from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

_EXPIRED_CALLBACK_MARKERS = (
    'query is too old',
    'response timeout expired',
    'query id is invalid',
)


def is_expired_callback_error(error: BaseException) -> bool:
    """Recognizes only harmless expired/invalid callback-query responses."""
    if not isinstance(error, TelegramBadRequest):
        return False
    text = str(error).casefold()
    return any(marker in text for marker in _EXPIRED_CALLBACK_MARKERS)


async def safe_answer_callback(
    callback: Any,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> bool:
    """Answers a callback and downgrades only an expired query to a warning."""
    try:
        await callback.answer(text=text, show_alert=show_alert)
        return True
    except TelegramBadRequest as error:
        if not is_expired_callback_error(error):
            raise
        data = str(getattr(callback, 'data', '') or '')[:120]
        user = getattr(getattr(callback, 'from_user', None), 'id', None)
        logger.warning(
            "Telegram callback expired: user=%s data=%r error=%s",
            user,
            data,
            error,
        )
        return False


__all__ = ['is_expired_callback_error', 'safe_answer_callback']
