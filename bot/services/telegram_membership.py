"""Safe Telegram chat membership checks for extension core facade."""
from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

_MEMBER_STATUSES = {'member', 'administrator', 'creator'}
_LEFT_STATUSES = {'left', 'kicked'}


async def check_telegram_chat_member(bot: Any, *, chat_id: Any, telegram_id: Any) -> dict[str, Any]:
    """Checks a Telegram user's membership without exposing Telegram objects."""
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_telegram_id = _normalize_telegram_id(telegram_id)
    if bot is None or not callable(getattr(bot, 'get_chat_member', None)):
        return _result(
            ok=False,
            is_member=False,
            status='',
            chat_id=normalized_chat_id,
            telegram_id=normalized_telegram_id,
            reason='bot_context_unavailable',
        )

    try:
        member = await bot.get_chat_member(chat_id=normalized_chat_id, user_id=normalized_telegram_id)
    except TelegramForbiddenError as exc:
        logger.warning("Telegram membership check forbidden for chat %s: %s", normalized_chat_id, exc)
        return _result(
            ok=False,
            is_member=False,
            status='',
            chat_id=normalized_chat_id,
            telegram_id=normalized_telegram_id,
            reason='bot_has_no_access',
        )
    except TelegramBadRequest as exc:
        logger.warning("Telegram membership check bad request for chat %s: %s", normalized_chat_id, exc)
        return _result(
            ok=False,
            is_member=False,
            status='',
            chat_id=normalized_chat_id,
            telegram_id=normalized_telegram_id,
            reason='bad_request',
        )
    except TelegramAPIError as exc:
        logger.warning("Telegram membership check Telegram API error for chat %s: %s", normalized_chat_id, exc)
        return _result(
            ok=False,
            is_member=False,
            status='',
            chat_id=normalized_chat_id,
            telegram_id=normalized_telegram_id,
            reason='telegram_api_error',
        )
    except Exception as exc:
        logger.exception("Unexpected Telegram membership check error for chat %s: %s", normalized_chat_id, exc)
        return _result(
            ok=False,
            is_member=False,
            status='',
            chat_id=normalized_chat_id,
            telegram_id=normalized_telegram_id,
            reason='unexpected_error',
        )

    status = _member_status(member)
    is_member = _is_member_status(status, member)
    return _result(
        ok=True,
        is_member=is_member,
        status=status,
        chat_id=normalized_chat_id,
        telegram_id=normalized_telegram_id,
        reason='',
    )


def _member_status(member: Any) -> str:
    status = getattr(member, 'status', '')
    value = getattr(status, 'value', status)
    return str(value or '').casefold()


def _is_member_status(status: str, member: Any) -> bool:
    if status in _MEMBER_STATUSES:
        return True
    if status in _LEFT_STATUSES:
        return False
    if status == 'restricted':
        return bool(getattr(member, 'is_member', False))
    return False


def _normalize_chat_id(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ValueError('chat_id must be a string or integer')
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError('chat_id must be a string or integer')
    text = value.strip()
    if not text:
        raise ValueError('chat_id must not be empty')
    return text


def _normalize_telegram_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('telegram_id must be a positive integer')
    return value


def _result(
    *,
    ok: bool,
    is_member: bool,
    status: str,
    chat_id: int | str,
    telegram_id: int,
    reason: str,
) -> dict[str, Any]:
    return {
        'ok': ok,
        'is_member': is_member,
        'status': status,
        'chat_id': chat_id,
        'telegram_id': telegram_id,
        'reason': reason,
    }


__all__ = ['check_telegram_chat_member']
