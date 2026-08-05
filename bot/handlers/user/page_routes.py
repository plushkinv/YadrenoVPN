"""Universal handler for data-driven user-page routes."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.utils.page_renderer import render_page
from bot.utils.page_routes import PAGE_ROUTE_CALLBACK_PREFIX, extract_page_route_key
from database.requests import get_page_route, is_user_banned


logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith(PAGE_ROUTE_CALLBACK_PREFIX))
async def page_route_handler(callback: CallbackQuery):
    """Opens a stored page through its named route and unified page flow."""
    telegram_id = callback.from_user.id
    if is_user_banned(telegram_id):
        from bot.utils.user_pages import render_access_blocked_page

        await render_access_blocked_page(callback)
        await callback.answer()
        return

    route_key = extract_page_route_key(callback.data)
    if not route_key:
        rendered = await render_page(callback, 'screen_unavailable')
        if rendered is not None:
            await callback.answer()
        return

    route = get_page_route(route_key)
    if not route or not route.get('is_enabled'):
        rendered = await render_page(callback, 'screen_unavailable')
        if rendered is not None:
            await callback.answer()
        return

    page_key = str(route.get('page_key') or '').strip()
    if not page_key:
        logger.warning("Route '%s' has no page_key", route_key)
        rendered = await render_page(callback, 'screen_unavailable')
        if rendered is not None:
            await callback.answer()
        return

    rendered = await render_page(
        callback,
        page_key=page_key,
        route_key=route_key,
        context={'telegram_id': telegram_id},
    )
    if rendered is not None:
        await callback.answer()
