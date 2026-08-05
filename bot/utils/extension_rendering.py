"""Shared unified page/route rendering helpers for extension events."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram.types import CallbackQuery, Message

from bot.utils.page_renderer import render_page
from database.requests import get_page, get_page_route


logger = logging.getLogger(__name__)


async def render_extension_page(
    target: CallbackQuery | Message,
    page_key: str,
    extra_context: Mapping[str, Any],
    *,
    force_new_for_message: bool = False,
) -> tuple[bool, bool]:
    """Renders one stored page requested by an extension event."""
    if not get_page(page_key):
        logger.warning("Extension requested missing page '%s'", page_key)
        return False, False

    context = {'telegram_id': _target_telegram_id(target), 'page_key': page_key}
    context.update(dict(extra_context))
    rendered = await render_page(
        target,
        page_key=page_key,
        context=context,
        force_new=force_new_for_message and isinstance(target, Message),
    )
    return True, rendered is None


async def render_extension_route(
    target: CallbackQuery | Message,
    route_key: str,
    extra_context: Mapping[str, Any],
    *,
    force_new_for_message: bool = False,
) -> tuple[bool, bool]:
    """Renders one stored page route requested by an extension event."""
    route = get_page_route(route_key)
    if not route or not route.get('is_enabled'):
        return False, False

    page_key = str(route.get('page_key') or '').strip()
    if not page_key or not get_page(page_key):
        logger.warning(
            "Extension route '%s' points to missing page '%s'",
            route_key,
            page_key,
        )
        return False, False

    context = {
        'telegram_id': _target_telegram_id(target),
        'route_key': route_key,
        'page_key': page_key,
    }
    context.update(dict(extra_context))
    rendered = await render_page(
        target,
        page_key=page_key,
        route_key=route_key,
        context=context,
        force_new=force_new_for_message and isinstance(target, Message),
    )
    return True, rendered is None


def _target_telegram_id(target: CallbackQuery | Message) -> int | None:
    user = getattr(target, 'from_user', None)
    if user is not None and not getattr(user, 'is_bot', False):
        return getattr(user, 'id', None)
    message = getattr(target, 'message', None)
    user = getattr(message, 'from_user', None)
    if user is not None and not getattr(user, 'is_bot', False):
        return getattr(user, 'id', None)
    return None


__all__ = [
    'render_extension_page',
    'render_extension_route',
]
