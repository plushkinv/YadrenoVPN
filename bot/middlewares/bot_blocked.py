"""Resetting the bot blocking flag when a new user accesses it."""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database.requests import mark_user_bot_unblocked

logger = logging.getLogger(__name__)


class BotBlockedResetMiddleware(BaseMiddleware):
    """Clears the bulk sends stop flag when the user writes to the bot again."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user:
            try:
                mark_user_bot_unblocked(user.id)
            except Exception as e:
                logger.warning("Не удалось снять флаг блокировки бота для пользователя %s: %s", user.id, e)
        return await handler(event, data)
