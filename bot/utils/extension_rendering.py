"""Shared page/route rendering helpers for extension events."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram.types import CallbackQuery, Message

from bot.utils.page_flow import (
    build_page_flow_context,
    parse_registry_names,
    run_page_guards,
    run_page_hooks,
)
from bot.utils.page_renderer import render_page
from bot.utils.text import safe_edit_or_send
from database.requests import get_page, get_page_route

logger = logging.getLogger(__name__)


async def render_extension_page(
    target: CallbackQuery | Message,
    page_key: str,
    extra_context: Mapping[str, Any],
    *,
    force_new_for_message: bool = False,
) -> tuple[bool, bool]:
    """Renders one page requested by an extension event."""
    page = get_page(page_key)
    if not page:
        logger.warning("Extension requested missing page '%s'", page_key)
        return False, False

    context = build_page_flow_context(
        target,
        telegram_id=_target_telegram_id(target),
        page_key=page_key,
    )
    context.update(dict(extra_context))

    guard_result = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        target,
        context,
    )
    if not guard_result.allowed:
        await _handle_guard_denied(
            target,
            guard_result.message or "⚠️ Страница временно недоступна",
            show_alert=guard_result.show_alert,
        )
        return True, True

    hook_result = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        target,
        context,
    )
    context.update(hook_result.context)

    await render_page(
        target,
        page_key=page_key,
        visibility=hook_result.visibility or None,
        context=context,
        text_replacements=hook_result.text_replacements or None,
        prepend_buttons=hook_result.prepend_buttons,
        append_buttons=hook_result.append_buttons,
        force_new=force_new_for_message and isinstance(target, Message),
    )
    return True, False


async def render_extension_route(
    target: CallbackQuery | Message,
    route_key: str,
    extra_context: Mapping[str, Any],
    *,
    force_new_for_message: bool = False,
) -> tuple[bool, bool]:
    """Renders one page route requested by an extension event."""
    route = get_page_route(route_key)
    if not route or not route.get('is_enabled'):
        return False, False

    page_key = route.get('page_key')
    page = get_page(page_key) if page_key else None
    if not page_key or not page:
        logger.warning("Extension route '%s' points to missing page '%s'", route_key, page_key)
        return False, False

    context = build_page_flow_context(
        target,
        telegram_id=_target_telegram_id(target),
        route_key=route_key,
        page_key=page_key,
    )
    context.update(dict(extra_context))

    route_guard = await run_page_guards(
        parse_registry_names(route.get('guard_names')),
        target,
        context,
    )
    if not route_guard.allowed:
        await _handle_guard_denied(
            target,
            route_guard.message or "⚠️ Страница временно недоступна",
            show_alert=route_guard.show_alert,
        )
        return True, True

    page_guard = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        target,
        context,
    )
    if not page_guard.allowed:
        await _handle_guard_denied(
            target,
            page_guard.message or "⚠️ Страница временно недоступна",
            show_alert=page_guard.show_alert,
        )
        return True, True

    page_hook = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        target,
        context,
    )
    context.update(page_hook.context)

    route_hook = await run_page_hooks(
        parse_registry_names(route.get('hook_names')),
        target,
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
        target,
        page_key=page_key,
        visibility=visibility or None,
        context=context,
        text_replacements=text_replacements or None,
        prepend_buttons=prepend_buttons or None,
        append_buttons=append_buttons or None,
        force_new=force_new_for_message and isinstance(target, Message),
    )
    return True, False


def _target_telegram_id(target: CallbackQuery | Message) -> int | None:
    user = getattr(target, 'from_user', None)
    if user is not None and not getattr(user, 'is_bot', False):
        return getattr(user, 'id', None)
    message = getattr(target, 'message', None)
    user = getattr(message, 'from_user', None)
    if user is not None and not getattr(user, 'is_bot', False):
        return getattr(user, 'id', None)
    return None


async def _handle_guard_denied(target: CallbackQuery | Message, message: str, *, show_alert: bool) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(message, show_alert=show_alert)
        return
    await safe_edit_or_send(target, message, force_new=True)


__all__ = [
    'render_extension_page',
    'render_extension_route',
]
