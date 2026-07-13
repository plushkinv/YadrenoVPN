"""User-router for declarative custom extension commands."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram import Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.utils.extension_commands import (
    dispatch_extension_command,
    get_extension_command_definition,
    parse_extension_command,
)
from bot.utils.extension_rendering import render_extension_page, render_extension_route
from bot.utils.text import safe_edit_or_send
from bot.utils.user_pages import render_access_blocked_page
from database.requests import is_user_banned

router = Router()


class ExtensionCommandFilter(BaseFilter):
    """Passes only commands that are currently registered by extensions."""

    async def __call__(self, message: Message, bot: Any = None) -> dict[str, Any] | bool:
        bot_username = (
            getattr(bot, 'my_username', None)
            or getattr(bot, 'username', None)
            or getattr(getattr(message, 'bot', None), 'my_username', None)
            or ''
        )
        parsed = parse_extension_command(message.text or '', bot_username=bot_username)
        if not parsed:
            return False
        definition = get_extension_command_definition(parsed['command'])
        if definition is None:
            return False
        return {
            'extension_command': {
                **parsed,
                'extension_id': definition.extension_id,
                'action_key': definition.action_key,
            }
        }


@router.message(ExtensionCommandFilter(), StateFilter('*'))
async def extension_command_handler(
    message: Message,
    state: FSMContext,
    extension_command: Mapping[str, str],
) -> None:
    """Executes a registered extension command without exposing raw Telegram API."""
    telegram_id = message.from_user.id
    if is_user_banned(telegram_id):
        await render_access_blocked_page(message, force_new=True)
        return

    await state.clear()

    context = {
        **dict(extension_command),
        'telegram_id': telegram_id,
        'bot_username': (
            getattr(message.bot, 'my_username', None)
            or getattr(message.bot, 'username', None)
            or ''
        ),
    }
    result = await dispatch_extension_command(context, bot=message.bot)

    render_context = {
        'telegram_id': telegram_id,
        'extension_id': extension_command['extension_id'],
        'extension_command': extension_command['command'],
        'extension_argument': extension_command.get('argument', ''),
    }
    if isinstance(result.get('context'), Mapping):
        render_context.update(dict(result['context']))

    if result.get('page_key'):
        rendered, handled = await render_extension_page(
            message,
            str(result['page_key']),
            render_context,
            force_new_for_message=True,
        )
        if not handled and not rendered:
            await safe_edit_or_send(message, "⚠️ Страница недоступна", force_new=True)
        return

    if result.get('route_key'):
        rendered, handled = await render_extension_route(
            message,
            str(result['route_key']),
            render_context,
            force_new_for_message=True,
        )
        if not handled and not rendered:
            await safe_edit_or_send(message, "⚠️ Маршрут недоступен", force_new=True)
        return

    if result.get('answer_text'):
        await safe_edit_or_send(message, result['answer_text'], force_new=True)


__all__ = ['router']
