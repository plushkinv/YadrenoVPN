"""Register incoming Telegram senders before routing and access checks."""
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

from bot.services.referral_attribution import attribute_start_referral
from database.requests import get_or_create_user

logger = logging.getLogger(__name__)


class UserRegistrationMiddleware(BaseMiddleware):
    """Persist the sender and first-entry referral data before any handler."""

    def __init__(self) -> None:
        self._start_command = Command('start')

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        sender = data.get('event_from_user')
        if sender is None or sender.is_bot:
            return await handler(event, data)

        user, is_new = get_or_create_user(
            sender.id,
            sender.username,
            first_name=sender.first_name,
            last_name=sender.last_name,
        )
        if isinstance(event, Message):
            command_data = await self._start_command(event, data['bot'])
            if command_data and attribute_start_referral(
                user, is_new=is_new, args=command_data['command'].args,
            ):
                try:
                    from bot.services.notifications import notify_referrers_new_referral

                    await notify_referrers_new_referral(data['bot'], user['id'])
                except Exception:
                    logger.warning('Failed to notify referrers for user %s', user['id'], exc_info=True)

        return await handler(event, data)
