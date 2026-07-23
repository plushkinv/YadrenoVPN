"""Middleware for global user access guards."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.text import safe_edit_or_send
from bot.utils.user_access import UserAccessGuardResult, has_user_access_guards, run_user_access_guards
from config import ADMIN_IDS


class UserAccessGuardMiddleware(BaseMiddleware):
    """Runs registered user access guards before user-router handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not has_user_access_guards():
            return await handler(event, data)

        user = data.get('event_from_user')
        if user is None or getattr(user, 'is_bot', False):
            return await handler(event, data)
        if user.id in ADMIN_IDS:
            return await handler(event, data)
        if _is_bypassed_extension_callback(event):
            return await handler(event, data)

        result = await run_user_access_guards(event, _build_guard_context(event, data))
        if result.allowed:
            return await handler(event, data)

        await _handle_denied(event, result)
        return None


def _is_bypassed_extension_callback(event: TelegramObject) -> bool:
    if not isinstance(event, CallbackQuery):
        return False
    from bot.utils.extension_callbacks import is_extension_access_check_callback

    return is_extension_access_check_callback(event.data)


def _build_guard_context(event: TelegramObject, data: Dict[str, Any]) -> dict[str, Any]:
    user = data.get('event_from_user')
    bot = data.get('bot')
    context: dict[str, Any] = {
        'telegram_id': getattr(user, 'id', None),
        'is_admin': getattr(user, 'id', None) in ADMIN_IDS if user else False,
        'event_type': 'callback' if isinstance(event, CallbackQuery) else 'message',
    }
    if isinstance(event, CallbackQuery):
        context['callback_data'] = event.data or ''
        context['message_text'] = ''
    elif isinstance(event, Message):
        context['callback_data'] = ''
        context['message_text'] = event.text or event.caption or ''

    bot_username = getattr(bot, 'my_username', None) or getattr(bot, 'username', None) or ''
    if bot_username:
        context['bot_username'] = bot_username
    return context


async def _handle_denied(event: TelegramObject, result: UserAccessGuardResult) -> None:
    if result.page_key:
        from bot.utils.page_renderer import render_page

        await render_page(
            event,
            page_key=result.page_key,
            context=result.context,
            force_new=isinstance(event, Message),
        )
        if isinstance(event, CallbackQuery):
            await event.answer(result.message or None, show_alert=bool(result.message and result.show_alert))
        return

    if not result.message:
        from bot.utils.page_renderer import render_page

        await render_page(
            event,
            page_key='action_unavailable',
            force_new=isinstance(event, Message),
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    text = result.message
    reply_markup = _build_reply_markup(result)
    if isinstance(event, CallbackQuery):
        if reply_markup and event.message:
            await safe_edit_or_send(event.message, text, reply_markup=reply_markup)
            await event.answer()
        else:
            await event.answer(text, show_alert=result.show_alert)
        return

    if isinstance(event, Message):
        await safe_edit_or_send(event, text, reply_markup=reply_markup, force_new=True)


def _build_reply_markup(result: UserAccessGuardResult):
    if not result.buttons:
        return None
    builder = InlineKeyboardBuilder()
    for row in result.buttons:
        buttons = []
        for button in row:
            if button.get('url'):
                buttons.append(InlineKeyboardButton(text=button['label'], url=button['url']))
            else:
                buttons.append(InlineKeyboardButton(text=button['label'], callback_data=button['callback_data']))
        if buttons:
            builder.row(*buttons)
    return builder.as_markup()


__all__ = ['UserAccessGuardMiddleware']
