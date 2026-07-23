"""Universal handler of data-driven routes of user pages."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.utils.page_flow import (
    build_page_flow_context,
    parse_registry_names,
    run_page_guards,
    run_page_hooks,
)
from bot.utils.page_renderer import render_page
from bot.utils.page_routes import PAGE_ROUTE_CALLBACK_PREFIX, extract_page_route_key
from database.requests import get_page, get_page_route, is_user_banned


logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith(PAGE_ROUTE_CALLBACK_PREFIX))
async def page_route_handler(callback: CallbackQuery):
    """Opens a page via a named route from the database."""
    telegram_id = callback.from_user.id
    if is_user_banned(telegram_id):
        from bot.utils.user_pages import render_access_blocked_page

        await render_access_blocked_page(callback)
        await callback.answer()
        return

    route_key = extract_page_route_key(callback.data)
    if not route_key:
        await render_page(callback, 'screen_unavailable')
        await callback.answer()
        return

    route = get_page_route(route_key)
    if not route or not route.get('is_enabled'):
        await render_page(callback, 'screen_unavailable')
        await callback.answer()
        return

    page_key = route.get('page_key')
    page = get_page(page_key) if page_key else None
    if not page_key or not page:
        logger.warning("Route '%s' указывает на отсутствующую страницу '%s'", route_key, page_key)
        await render_page(callback, 'screen_unavailable')
        await callback.answer()
        return

    context = build_page_flow_context(
        callback,
        telegram_id=telegram_id,
        route_key=route_key,
        page_key=page_key,
    )

    guard_result = await run_page_guards(
        parse_registry_names(route.get('guard_names')),
        callback,
        context,
    )
    if not guard_result.allowed:
        if guard_result.message:
            await callback.answer(guard_result.message, show_alert=guard_result.show_alert)
        else:
            await render_page(callback, 'action_unavailable')
            await callback.answer()
        return

    page_guard_result = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        callback,
        context,
    )
    if not page_guard_result.allowed:
        if page_guard_result.message:
            await callback.answer(
                page_guard_result.message,
                show_alert=page_guard_result.show_alert,
            )
        else:
            await render_page(callback, 'action_unavailable')
            await callback.answer()
        return

    page_hook_result = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        callback,
        context,
    )
    context.update(page_hook_result.context)

    route_hook_result = await run_page_hooks(
        parse_registry_names(route.get('hook_names')),
        callback,
        context,
    )
    context.update(route_hook_result.context)

    visibility = {}
    visibility.update(page_hook_result.visibility)
    visibility.update(route_hook_result.visibility)

    text_replacements = {}
    text_replacements.update(page_hook_result.text_replacements)
    text_replacements.update(route_hook_result.text_replacements)

    prepend_buttons = []
    if page_hook_result.prepend_buttons:
        prepend_buttons.extend(page_hook_result.prepend_buttons)
    if route_hook_result.prepend_buttons:
        prepend_buttons.extend(route_hook_result.prepend_buttons)

    append_buttons = []
    if page_hook_result.append_buttons:
        append_buttons.extend(page_hook_result.append_buttons)
    if route_hook_result.append_buttons:
        append_buttons.extend(route_hook_result.append_buttons)

    await render_page(
        callback,
        page_key=page_key,
        visibility=visibility or None,
        context=context,
        text_replacements=text_replacements or None,
        prepend_buttons=prepend_buttons or None,
        append_buttons=append_buttons or None,
    )
    await callback.answer()
