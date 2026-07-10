"""User-router для декларативных callbacks custom extensions."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.utils.extension_callbacks import (
    EXT_CALLBACK_PREFIX,
    dispatch_extension_callback,
    parse_extension_callback_data,
)
from bot.utils.page_flow import (
    build_page_flow_context,
    parse_registry_names,
    run_page_guards,
    run_page_hooks,
)
from bot.utils.page_renderer import render_page
from database.requests import get_page, get_page_route, is_user_banned


logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith(EXT_CALLBACK_PREFIX))
async def extension_callback_handler(callback: CallbackQuery) -> None:
    """Выполняет зарегистрированный extension callback без передачи raw Telegram API."""
    telegram_id = callback.from_user.id
    if is_user_banned(telegram_id):
        await callback.answer("⛔ Доступ заблокирован", show_alert=True)
        return

    parsed = parse_extension_callback_data(callback.data)
    if not parsed:
        await callback.answer("⚠️ Действие расширения недоступно", show_alert=True)
        return

    context = {
        **parsed,
        'telegram_id': telegram_id,
    }
    result = await dispatch_extension_callback(context)

    render_context = {
        'telegram_id': telegram_id,
        'extension_id': parsed['extension_id'],
        'extension_action': parsed['action_name'],
        'extension_payload': parsed['payload'],
    }
    if isinstance(result.get('context'), Mapping):
        render_context.update(dict(result['context']))

    if result.get('page_key'):
        rendered, answered = await _render_extension_page(callback, str(result['page_key']), render_context)
        if not answered:
            await _answer_callback(callback, result, default_text=None if rendered else "⚠️ Страница недоступна")
        return

    if result.get('route_key'):
        rendered, answered = await _render_extension_route(callback, str(result['route_key']), render_context)
        if not answered:
            await _answer_callback(callback, result, default_text=None if rendered else "⚠️ Маршрут недоступен")
        return

    await _answer_callback(callback, result, default_text=None)


async def _render_extension_page(
    callback: CallbackQuery,
    page_key: str,
    extra_context: Mapping[str, Any],
) -> tuple[bool, bool]:
    page = get_page(page_key)
    if not page:
        logger.warning("Extension callback requested missing page '%s'", page_key)
        return False, False

    context = build_page_flow_context(
        callback,
        telegram_id=callback.from_user.id,
        page_key=page_key,
    )
    context.update(dict(extra_context))

    guard_result = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        callback,
        context,
    )
    if not guard_result.allowed:
        await callback.answer(
            guard_result.message or "⚠️ Страница временно недоступна",
            show_alert=guard_result.show_alert,
        )
        return True, True

    hook_result = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        callback,
        context,
    )
    context.update(hook_result.context)

    await render_page(
        callback,
        page_key=page_key,
        visibility=hook_result.visibility or None,
        context=context,
        text_replacements=hook_result.text_replacements or None,
        prepend_buttons=hook_result.prepend_buttons,
        append_buttons=hook_result.append_buttons,
    )
    return True, False


async def _render_extension_route(
    callback: CallbackQuery,
    route_key: str,
    extra_context: Mapping[str, Any],
) -> tuple[bool, bool]:
    route = get_page_route(route_key)
    if not route or not route.get('is_enabled'):
        return False, False

    page_key = route.get('page_key')
    page = get_page(page_key) if page_key else None
    if not page_key or not page:
        logger.warning("Extension route '%s' points to missing page '%s'", route_key, page_key)
        return False, False

    context = build_page_flow_context(
        callback,
        telegram_id=callback.from_user.id,
        route_key=route_key,
        page_key=page_key,
    )
    context.update(dict(extra_context))

    route_guard = await run_page_guards(
        parse_registry_names(route.get('guard_names')),
        callback,
        context,
    )
    if not route_guard.allowed:
        await callback.answer(
            route_guard.message or "⚠️ Страница временно недоступна",
            show_alert=route_guard.show_alert,
        )
        return True, True

    page_guard = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        callback,
        context,
    )
    if not page_guard.allowed:
        await callback.answer(
            page_guard.message or "⚠️ Страница временно недоступна",
            show_alert=page_guard.show_alert,
        )
        return True, True

    page_hook = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        callback,
        context,
    )
    context.update(page_hook.context)

    route_hook = await run_page_hooks(
        parse_registry_names(route.get('hook_names')),
        callback,
        context,
    )
    context.update(route_hook.context)

    visibility = {}
    visibility.update(page_hook.visibility)
    visibility.update(route_hook.visibility)

    text_replacements = {}
    text_replacements.update(page_hook.text_replacements)
    text_replacements.update(route_hook.text_replacements)

    prepend_buttons = []
    if page_hook.prepend_buttons:
        prepend_buttons.extend(page_hook.prepend_buttons)
    if route_hook.prepend_buttons:
        prepend_buttons.extend(route_hook.prepend_buttons)

    append_buttons = []
    if page_hook.append_buttons:
        append_buttons.extend(page_hook.append_buttons)
    if route_hook.append_buttons:
        append_buttons.extend(route_hook.append_buttons)

    await render_page(
        callback,
        page_key=page_key,
        visibility=visibility or None,
        context=context,
        text_replacements=text_replacements or None,
        prepend_buttons=prepend_buttons or None,
        append_buttons=append_buttons or None,
    )
    return True, False


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
