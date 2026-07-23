"""User-router for declarative callbacks custom extensions."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.utils.extension_callbacks import (
    EXT_CALLBACK_PREFIX,
    dispatch_extension_callback,
    parse_extension_callback_data,
)
from bot.utils.extension_rendering import render_extension_page, render_extension_route
from bot.utils.page_renderer import render_page
from bot.utils.user_pages import render_access_blocked_page
from database.requests import is_user_banned

router = Router()


@router.callback_query(F.data.startswith(EXT_CALLBACK_PREFIX))
async def extension_callback_handler(callback: CallbackQuery) -> None:
    """Executes a registered extension callback without passing the raw Telegram API."""
    telegram_id = callback.from_user.id
    if is_user_banned(telegram_id):
        await render_access_blocked_page(callback)
        await callback.answer()
        return

    parsed = parse_extension_callback_data(callback.data)
    if not parsed:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return

    context = {
        **parsed,
        'telegram_id': telegram_id,
    }
    result = await dispatch_extension_callback(context, bot=callback.bot)

    render_context = {
        'telegram_id': telegram_id,
        'extension_id': parsed['extension_id'],
        'extension_action': parsed['action_name'],
        'extension_payload': parsed['payload'],
    }
    if isinstance(result.get('context'), Mapping):
        render_context.update(dict(result['context']))

    if result.get('page_key'):
        rendered, answered = await render_extension_page(callback, str(result['page_key']), render_context)
        if not answered:
            if not rendered:
                await render_page(callback, 'screen_unavailable')
            await _answer_callback(callback, result, default_text=None)
        return

    if result.get('route_key'):
        rendered, answered = await render_extension_route(callback, str(result['route_key']), render_context)
        if not answered:
            if not rendered:
                await render_page(callback, 'screen_unavailable')
            await _answer_callback(callback, result, default_text=None)
        return

    await _answer_callback(callback, result, default_text=None)


async def _answer_callback(
    callback: CallbackQuery,
    result: Mapping[str, Any],
    *,
    default_text: str | None,
) -> None:
    text = result.get('answer_text')
    if text is None:
        text = default_text
    if text:
        await callback.answer(str(text), show_alert=bool(result.get('show_alert', False)))
    else:
        await callback.answer()
