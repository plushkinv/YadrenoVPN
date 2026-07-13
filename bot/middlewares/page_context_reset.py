"""Resetting the /yaa context when navigating through the user part of the bot."""
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.page_context import clear_page_context
from config import ADMIN_IDS


class ResetAdminPageContextMiddleware(BaseMiddleware):
    """
    Before any new action in the user part, the old context is cleared.

    If the handler renders the supported page again via render_page(),
    the new context will be written after the actual rendering. Therefore /yaa
    will not be able to accidentally work on a page from which the administrator has already left.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user and user.id in ADMIN_IDS:
            clear_page_context(user.id)
        return await handler(event, data)
