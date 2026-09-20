"""Optional host-key selection after a component subscription is delivered."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.utils.page_button_items import build_subscription_host_button_items


logger = logging.getLogger(__name__)
router = Router()

HOST_SELECT_PAGE = 'key_subscription_host_select'
HOST_BIND_ERROR_PAGE = 'key_operation_failed'
HOST_BIND_SOURCE_NAMESPACE = 'core.group_parent'
MAX_HOST_SELECTOR_ITEMS = 50


def _host_id(host):
    from bot.services.subscription_host_flow import host_id
    return host_id(host)


def _load_default_hosts(component_key_id):
    from bot.services.subscription_host_flow import load_default_hosts
    return load_default_hosts(component_key_id)


async def _bind_host(*, host_key_id, component_key_id):
    from bot.services.subscription_host_flow import bind_default_host
    return await bind_default_host(host_key_id=host_key_id, component_key_id=component_key_id)


def _viewer_id(target: Any, explicit_telegram_id: int | None) -> int | None:
    if explicit_telegram_id is not None:
        return int(explicit_telegram_id)
    user = getattr(target, 'from_user', None)
    if user is not None and not getattr(user, 'is_bot', False):
        return int(user.id)
    message = getattr(target, 'message', None)
    chat = getattr(message, 'chat', None) or getattr(target, 'chat', None)
    if chat is not None and getattr(chat, 'type', None) == 'private':
        return int(chat.id)
    return None


def _component_owned_by_actor(component_key_id: int, telegram_id: int) -> bool:
    """Revalidate callback ownership against the database, not callback data."""
    from database.requests import get_vpn_key_by_id

    component = get_vpn_key_by_id(int(component_key_id))
    if not component:
        return False
    owner_telegram_id = component.get('telegram_id')
    if isinstance(owner_telegram_id, bool):
        return False
    try:
        return int(owner_telegram_id) == int(telegram_id)
    except (TypeError, ValueError):
        return False


def _bind_error_code(result: Mapping[str, Any]) -> str:
    return str(
        result.get('error_code')
        or result.get('status')
        or 'bind_failed'
    )


async def _render_stored_page(
    target: Any,
    page_key: str,
    *,
    context: dict[str, Any],
) -> Message | None:
    """Send a new stored page for interactive and background key flows."""
    if getattr(target, 'is_background_delivery', False):
        from bot.utils.background_page_delivery import send_background_page

        return await send_background_page(
            target.bot,
            telegram_id=int(target.telegram_id),
            page_key=page_key,
            context=context,
        )

    render_target = (
        target
        if isinstance(target, (Message, CallbackQuery))
        else getattr(target, 'message', None)
    )
    if render_target is None:
        return None
    from bot.utils.page_renderer import render_page

    return await render_page(
        render_target,
        page_key=page_key,
        context=context,
        force_new=True,
    )


def _selector_context(
    *,
    component_key_id: int,
    telegram_id: int | None,
    hosts: list[dict[str, Any]],
) -> dict[str, Any]:
    shown_hosts = hosts[:MAX_HOST_SELECTOR_ITEMS]
    context: dict[str, Any] = {
        'component_key_id': int(component_key_id),
        'key_id': int(component_key_id),
        'subscription_host_button_items': build_subscription_host_button_items(
            shown_hosts,
            component_key_id=int(component_key_id),
        ),
        'subscription_host_count': len(hosts),
        'subscription_host_shown_count': len(shown_hosts),
    }
    if telegram_id is not None:
        context['telegram_id'] = int(telegram_id)
    return context


async def _render_selector(
    target: Any,
    *,
    component_key_id: int,
    telegram_id: int | None,
    hosts: list[dict[str, Any]],
) -> Message | None:
    return await _render_stored_page(
        target,
        HOST_SELECT_PAGE,
        context=_selector_context(
            component_key_id=component_key_id,
            telegram_id=telegram_id,
            hosts=hosts,
        ),
    )


async def _render_bind_error(
    target: Any,
    *,
    component_key_id: int,
    telegram_id: int | None,
    error_code: str,
) -> Message | None:
    context: dict[str, Any] = {
        'key_id': int(component_key_id),
        'component_key_id': int(component_key_id),
        'error_code': str(error_code),
    }
    if telegram_id is not None:
        context['telegram_id'] = int(telegram_id)
    return await _render_stored_page(
        target,
        HOST_BIND_ERROR_PAGE,
        context=context,
    )


async def offer_default_subscription_host(
    target: Any | None,
    *,
    component_key_id: int,
    telegram_id: int | None = None,
) -> dict[str, Any]:
    """Apply 0/1/N defaults after configuration, with optional UI delivery."""
    component_id = int(component_key_id)
    viewer_id = _viewer_id(target, telegram_id)
    from bot.services.subscription_host_flow import resolve_default_host
    result, hosts = await resolve_default_host(component_id, _load=_load_default_hosts, _bind=_bind_host)
    if target is None:
        return result
    if result.get('status') == 'selection_required':
        try:
            rendered = await _render_selector(target, component_key_id=component_id, telegram_id=viewer_id, hosts=hosts)
            return {**result, 'ok': rendered is not None}
        except Exception:
            logger.exception('Could not render subscription host selector for key %s', component_id)
            return {'ok': False, 'status': 'selection_delivery_failed'}
    if not result.get('ok'):
        try:
            await _render_bind_error(target, component_key_id=component_id, telegram_id=viewer_id,
                                     error_code=_bind_error_code(result))
            if hosts and result.get('status') != 'invalid_host':
                await _render_selector(target, component_key_id=component_id, telegram_id=viewer_id, hosts=hosts)
        except Exception:
            logger.exception('Could not render subscription host failure for key %s', component_id)
    return result


async def _dismiss_prompt(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except Exception:
        logger.warning(
            'Could not remove subscription host prompt message=%s',
            getattr(callback.message, 'message_id', None),
            exc_info=True,
        )


@router.callback_query(F.data.startswith('subscription_host_select:'))
async def subscription_host_select_handler(callback: CallbackQuery) -> None:
    """Revalidate the actor and delegate the selected pair to domain policy."""
    await callback.answer()
    parts = str(callback.data or '').split(':')
    if len(parts) != 3:
        return
    try:
        component_key_id = int(parts[1])
        host_key_id = int(parts[2])
    except (TypeError, ValueError):
        return
    if component_key_id <= 0 or host_key_id <= 0:
        return
    telegram_id = int(callback.from_user.id)
    if not _component_owned_by_actor(component_key_id, telegram_id):
        logger.warning(
            'Rejected subscription host callback for foreign component '
            'component=%s actor=%s',
            component_key_id,
            telegram_id,
        )
        return

    try:
        hosts = _load_default_hosts(component_key_id)
        eligible_ids = {_host_id(host) for host in hosts}
        if host_key_id not in eligible_ids:
            logger.info(
                'Subscription host selection is stale; domain will resolve it '
                'idempotently component=%s host=%s',
                component_key_id,
                host_key_id,
            )
        result = await _bind_host(
            host_key_id=host_key_id,
            component_key_id=component_key_id,
        )
    except Exception as error:
        logger.warning(
            'Subscription host selection failed component=%s host=%s user=%s: %s',
            component_key_id,
            host_key_id,
            telegram_id,
            error,
            exc_info=True,
        )
        result = {'ok': False, 'status': 'bind_failed'}

    if (
        result.get('ok') is True
        or result.get('error_code') == 'component_already_bound'
    ):
        await _dismiss_prompt(callback)
        return
    try:
        await _render_bind_error(
            callback,
            component_key_id=component_key_id,
            telegram_id=telegram_id,
            error_code=_bind_error_code(result),
        )
    except Exception:
        logger.exception(
            'Could not render host-binding failure component=%s host=%s',
            component_key_id,
            host_key_id,
        )


@router.callback_query(F.data.startswith('subscription_host_skip:'))
async def subscription_host_skip_handler(callback: CallbackQuery) -> None:
    """Keep the delivered component key standalone and close the prompt."""
    await callback.answer()
    parts = str(callback.data or '').split(':')
    if len(parts) != 2:
        return
    try:
        component_key_id = int(parts[1])
        telegram_id = int(callback.from_user.id)
    except (TypeError, ValueError):
        return
    if component_key_id <= 0 or not _component_owned_by_actor(
        component_key_id,
        telegram_id,
    ):
        logger.warning(
            'Rejected subscription host skip for foreign component '
            'component=%s actor=%s',
            component_key_id,
            telegram_id,
        )
        return
    await _dismiss_prompt(callback)


__all__ = [
    'HOST_BIND_SOURCE_NAMESPACE',
    'HOST_SELECT_PAGE',
    'MAX_HOST_SELECTOR_ITEMS',
    'offer_default_subscription_host',
    'router',
]
