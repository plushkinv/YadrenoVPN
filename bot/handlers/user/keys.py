import logging
import uuid
import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_all_servers, get_setting, is_referral_enabled, get_user_by_referral_code, set_user_referrer
from bot.states.user_states import RenameKey, ReplaceKey
from bot.utils.action_dispatcher import (
    CoreActionRequest,
    dispatch_core_action,
    register_core_action_executor,
)
from bot.utils.user_pages import render_access_blocked_page
from bot.utils.user_ui_texts import render_ui_text
from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.utils.panel_email import is_managed_panel_email

logger = logging.getLogger(__name__)

router = Router()

@router.message(Command('mykeys', 'my_keys'))
async def cmd_mykeys(message: Message, state: FSMContext):
    """Command handler /mykeys - calls the logic of the 'My Keys' button."""
    if is_user_banned(message.from_user.id):
        await render_access_blocked_page(message, force_new=True)
        return
    await state.clear()
    await show_my_keys(message.from_user.id, message, is_callback=False)

async def _build_my_keys_render_data(telegram_id: int):
    """Prepares list text and dynamic key buttons."""
    from bot.utils.page_dynamic_data import build_my_keys_render_data

    return await build_my_keys_render_data(telegram_id)


async def _render_my_keys_page(
    target,
    telegram_id: int,
    force_new: bool = False,
    *,
    route_key: str | None = None,
) -> None:
    """Renders the “My Keys” page from the pages table."""
    from bot.utils.page_renderer import render_page

    keys, keys_list_text, key_button_items = await _build_my_keys_render_data(telegram_id)
    context = {
        'telegram_id': telegram_id,
        'key_button_items': key_button_items,
        'keys_list_html': keys_list_text,
    }

    if not keys:
        await render_page(
            target,
            page_key='my_keys_empty',
            route_key=route_key,
            context=context,
            force_new=force_new,
        )
        return

    await render_page(
        target,
        page_key='my_keys',
        route_key=route_key,
        context=context,
        force_new=force_new,
    )


async def rerender_my_keys_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved “My Keys” screen after editing via /yaa."""
    context = page_context.base_context or page_context.context or {}
    telegram_id = context.get('telegram_id') or viewer_id
    await _render_my_keys_page(
        page_context.message,
        int(telegram_id),
        route_key=getattr(page_context, 'route_key', None),
    )
    return True


async def rerender_key_details_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved key card after editing via /yaa."""
    context = page_context.base_context or page_context.context or {}
    key_id = context.get('key_id')
    if not key_id:
        return False
    telegram_id = context.get('telegram_id') or viewer_id
    await show_key_details(
        int(telegram_id),
        int(key_id),
        page_context.message,
        route_key=getattr(page_context, 'route_key', None),
    )
    return True


async def rerender_key_devices_page_context(page_context, viewer_id: int) -> bool:
    """Redraw the live device list after editing its page via /yaa."""
    context = page_context.base_context or page_context.context or {}
    key_id = context.get('key_id')
    if not key_id:
        return False
    telegram_id = context.get('telegram_id') or viewer_id
    await show_key_devices(
        int(telegram_id),
        int(key_id),
        page_context.message,
        route_key=getattr(page_context, 'route_key', None),
    )
    return True


async def show_my_keys(telegram_id: int, target, is_callback: bool = True):
    """
    General logic for displaying a list of keys.

    Args:
        telegram_id: Telegram user ID
        target: Message or CallbackQuery to send/edit
        is_callback: True if called from a callback (edit), False if called from a command (send a new one)
    """
    await _render_my_keys_page(target, telegram_id, force_new=not is_callback)

@router.callback_query(F.data == 'my_keys')
async def my_keys_handler(callback: CallbackQuery):
    """List of user VPN keys."""
    telegram_id = callback.from_user.id
    await show_my_keys(telegram_id, callback)
    await callback.answer()

async def show_key_details(
    telegram_id: int,
    key_id: int,
    message,
    is_callback: bool = True,
    *,
    route_key: str | None = None,
):
    """General logic for displaying key details."""
    from database.requests import (
        get_device_limit_mode,
        get_key_details_for_user,
        get_key_payments_history,
        is_key_active,
        is_traffic_exhausted,
    )
    from bot.services.vpn_api import format_traffic
    from bot.utils.key_pages import build_key_history_block, build_key_page_context
    from bot.utils.page_renderer import render_page
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(message, 'key_not_found', force_new=not is_callback)
        return
    traffic_exhausted = is_traffic_exhausted(key)
    key_active = is_key_active(key)
    if traffic_exhausted:
        status = render_ui_text('key.status.traffic_exhausted')
    elif key_active:
        status = render_ui_text('key.status.active')
    else:
        status = render_ui_text('key.status.expired')
    is_unconfigured = not key.get('server_id')
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if is_unconfigured:
        traffic_info = render_ui_text('key.traffic.needs_setup')
    elif traffic_limit > 0:
        used_str = format_traffic(traffic_used)
        limit_str = format_traffic(traffic_limit)
        percent = traffic_used / traffic_limit * 100 if traffic_limit > 0 else 0
        traffic_info = render_ui_text(
            'key.traffic.limited',
            used=used_str,
            limit=limit_str,
            percent=f'{percent:.1f}',
        )
    elif traffic_used > 0:
        traffic_info = render_ui_text(
            'key.traffic.used_unlimited',
            used=format_traffic(traffic_used),
        )
    else:
        traffic_info = render_ui_text('key.traffic.unlimited')
    payments = get_key_payments_history(key_id)
    key_page_context = build_key_page_context(
        key,
        status=status,
        traffic=traffic_info,
    )
    await render_page(
        message,
        page_key='key_details',
        route_key=route_key,
        context={
            'telegram_id': telegram_id,
            'key_id': key_id,
            'key_active': key_active,
            'is_unconfigured': is_unconfigured,
            'traffic_exhausted': traffic_exhausted,
            'has_sub_id': bool(key.get('sub_id')),
            'device_limit_mode': get_device_limit_mode(),
            'key_history_html': build_key_history_block(payments),
            **key_page_context,
        },
        force_new=not is_callback,
    )


def _format_device_seen_at(value: int | None, unknown: str) -> str:
    """Format a panel epoch value in the configured display time zone."""
    if value is None:
        return unknown
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return unknown
    if seconds > 10_000_000_000:
        seconds /= 1000
    try:
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return unknown
    from bot.utils.datetime_format import format_datetime_for_display

    return format_datetime_for_display(parsed, fallback=unknown)


def _normalize_device_metadata(value, unknown: str, *, limit: int) -> str:
    """Collapse panel-controlled whitespace and bound one display value."""
    normalized = ' '.join(str(value or '').split())
    if not normalized:
        return unknown
    if len(normalized) > limit:
        return normalized[:limit - 1].rstrip() + '…'
    return normalized


def _build_key_devices_render_data(
    devices,
    *,
    key_id: int,
) -> tuple[str, list[dict]]:
    """Build DB-template-backed rows and ephemeral delete callbacks."""
    unknown = render_ui_text('key.devices.unknown')
    if not devices:
        return render_ui_text('key.devices.empty'), []
    rows: list[str] = []
    button_items: list[dict] = []
    for index, device in enumerate(devices, start=1):
        model = _normalize_device_metadata(device.device_model, unknown, limit=80)
        os_name = _normalize_device_metadata(device.device_os, '', limit=60)
        os_version = _normalize_device_metadata(device.os_version, '', limit=40)
        os_text = ' '.join(value for value in (os_name, os_version) if value) or unknown
        user_agent = _normalize_device_metadata(device.user_agent, unknown, limit=180)
        rows.append(render_ui_text(
            'key.devices.item',
            index=index,
            model=model,
            os=os_text,
            user_agent=user_agent,
            first_seen=_format_device_seen_at(device.first_seen, unknown),
            last_seen=_format_device_seen_at(device.last_seen, unknown),
        ))
        item_name = f'{index}. {model}'
        if len(item_name) > 48:
            item_name = item_name[:47].rstrip() + '…'
        callback_data = f'kd:{key_id}:{device.id}'
        if len(callback_data.encode('utf-8')) > 64:
            logger.warning("Device delete callback is too long key_id=%s", key_id)
            continue
        button_items.append({
            'callback_data': callback_data,
            'data': {'item_name': item_name},
        })
    return '\n\n'.join(rows), button_items


async def show_key_devices(
    telegram_id: int,
    key_id: int,
    target,
    *,
    route_key: str | None = None,
) -> None:
    """Fetch and render the current device collection without local storage."""
    from bot.services.vpn_api import get_key_devices_for_user
    from bot.utils.page_renderer import render_page
    from database.requests import (
        DEVICE_LIMIT_MODE_HWID,
        get_device_limit_mode,
        get_key_details_for_user,
    )

    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(target, 'key_not_found')
        return
    if (
        get_device_limit_mode() != DEVICE_LIMIT_MODE_HWID
        or not all((key.get('server_id'), key.get('panel_email'), key.get('sub_id')))
    ):
        await _render_key_action_page(target, 'key_operation_unavailable', key=key)
        return
    try:
        devices = await get_key_devices_for_user(
            key_id=key_id,
            telegram_id=telegram_id,
        )
        devices_html, button_items = _build_key_devices_render_data(
            devices,
            key_id=key_id,
        )
    except Exception:
        logger.exception(
            "Could not load client devices key_id=%s user_id=%s",
            key_id,
            telegram_id,
        )
        devices_html = render_ui_text('key.devices.load_error')
        button_items = []
    await render_page(
        target,
        page_key='key_devices',
        route_key=route_key,
        context={
            'telegram_id': telegram_id,
            'key_id': key_id,
            'devices_list_html': devices_html,
            'key_device_button_items': button_items,
        },
    )


@router.callback_query(F.data.startswith('key_devices:'))
async def key_devices_handler(callback: CallbackQuery):
    """Open the registered devices of an owned key."""
    raw_key_id = str(callback.data or '').split(':', 1)[-1]
    if not raw_key_id.isdecimal() or int(raw_key_id) <= 0:
        await _render_key_action_page(callback, 'key_not_found')
        await callback.answer()
        return
    await show_key_devices(callback.from_user.id, int(raw_key_id), callback)
    await callback.answer()


@router.callback_query(F.data.startswith('kd:'))
async def key_device_delete_handler(callback: CallbackQuery):
    """Delete one registered device immediately, then reload the live list."""
    from bot.services.vpn_api import delete_key_device_for_user

    parts = str(callback.data or '').split(':', 2)
    if (
        len(parts) != 3
        or not parts[1].isdecimal()
        or int(parts[1]) <= 0
        or not parts[2].strip().isascii()
        or not parts[2].strip().isdecimal()
        or int(parts[2].strip()) <= 0
    ):
        await _render_key_action_page(callback, 'key_operation_unavailable')
        await callback.answer()
        return
    key_id = int(parts[1])
    notice_key = 'key.devices.deleted'
    try:
        deleted = await delete_key_device_for_user(
            key_id=key_id,
            telegram_id=callback.from_user.id,
            device_id=parts[2],
        )
        if not deleted:
            notice_key = 'key.devices.delete_error'
    except Exception:
        notice_key = 'key.devices.delete_error'
        logger.exception(
            "Could not delete client device key_id=%s user_id=%s",
            key_id,
            callback.from_user.id,
        )
    await show_key_devices(callback.from_user.id, key_id, callback)
    await callback.answer(
        render_ui_text(notice_key),
        show_alert=notice_key == 'key.devices.delete_error',
    )

@router.callback_query(F.data.startswith('key_delete:'))
async def key_delete_handler(callback: CallbackQuery, state: FSMContext):
    """Removing an expired key by the user."""
    key_id = int(callback.data.split(':')[1])
    await dispatch_core_action(
        callback,
        'key.delete',
        {'key_id': key_id},
        source='callback',
        state=state,
    )


@regular_panel_operation
async def _execute_key_delete(request: CoreActionRequest) -> None:
    """Run the original key deletion after action policy resolution."""
    target = request.target
    key_id = request.params['key_id']
    telegram_id = request.telegram_id
    from database.requests import get_key_details_for_user, delete_vpn_key
    from bot.services.vpn_api import get_client
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(target, 'key_not_found')
        return
    if key['is_active']:
        await _render_key_action_page(target, 'key_operation_unavailable', key=key)
        return
    reconcile_host_ids: tuple[int, ...] = ()
    try:
        from bot.services.subscription_composition import (
            list_key_subscription_reconcile_host_ids,
        )

        reconcile_host_ids = list_key_subscription_reconcile_host_ids(
            key_id=key_id,
            include_self=False,
        )
    except Exception as error:
        logger.warning(
            'Could not capture subscription reconcile targets before key '
            'deletion key=%s type=%s',
            key_id,
            type(error).__name__,
        )
    if (
        key.get('server_id')
        and is_managed_panel_email(key.get('panel_email'))
    ):
        try:
            client = await get_client(key['server_id'])
            await client.delete_client(key['panel_email'])
            logger.info(
                "Logical client for key %s was deleted from server %s",
                key_id,
                key['server_id'],
            )
        except Exception as e:
            logger.warning(f"Не удалось удалить клиента {key.get('panel_email', 'unknown')} с сервера 3X-UI: {e}")
    elif key.get('server_id'):
        logger.warning(
            "User key deletion skipped panel mutation for key %s with "
            "unmanaged panel_email=%r",
            key.get('id'),
            key.get('panel_email'),
        )
    success = delete_vpn_key(key_id)
    if success:
        if reconcile_host_ids:
            try:
                from bot.services.subscription_composition import (
                    schedule_subscription_host_reconciles,
                )

                schedule_subscription_host_reconciles(
                    host_key_ids=reconcile_host_ids,
                )
            except Exception as error:
                logger.warning(
                    'Could not immediately schedule subscription cleanup after '
                    'key deletion key=%s type=%s',
                    key_id,
                    type(error).__name__,
                )
        await _render_key_action_page(target, 'my_keys_key_deleted', key=key)
    else:
        logger.error('Failed to delete VPN key %s from the database', key_id)
        await _render_key_action_page(target, 'key_operation_failed', key=key)

@router.callback_query(F.data.startswith('key:'))
async def key_details_handler(callback: CallbackQuery):
    """Detailed information about the key with improved statistics."""
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    await show_key_details(telegram_id, key_id, callback.message)
    await callback.answer()

@router.callback_query(F.data.startswith('key_show:'))
async def key_show_handler(callback: CallbackQuery):
    """Show the subscription URL and QR code."""
    from database.requests import get_key_details_for_user
    from bot.utils.key_sender import build_key_delivery_target, send_key_with_qr
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(callback, 'key_not_found')
        return
    if not all((key.get('server_id'), key.get('panel_email'), key.get('sub_id'))):
        from bot.utils.page_renderer import render_page

        await render_page(callback, page_key='key_show_unconfigured')
        await callback.answer()
        return
    delivery_target = callback
    try:
        status_message = await _render_key_action_page(callback, 'key_progress', key=key)
        delivery_target = build_key_delivery_target(callback, status_message)
    except Exception:
        pass
    await send_key_with_qr(delivery_target, key)
    await callback.answer()


async def show_renew_payment_page(target, key: dict, key_id: int, force_new: bool = False):
    """Shows the tariff-first renewal shell from editable pages."""
    from bot.services.payment_provider_adapters import has_available_payment_method
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_renderer import render_page
    from bot.utils.page_button_items import build_tariff_button_items
    from bot.utils.groups import get_tariffs_for_renewal
    from database.requests import get_user_internal_id

    telegram_id = target.from_user.id
    user_id = get_user_internal_id(telegram_id)
    context = {
        'key_id': key_id,
        'telegram_id': telegram_id,
        'tariff_button_items': build_tariff_button_items(
            get_tariffs_for_renewal(int(key.get('tariff_id') or 0)),
            'key_renewal',
            key_id=key_id,
            user_id=user_id,
        ),
        'tariff_back_callback': f'key:{key_id}',
        **build_key_page_context(key),
    }
    has_payment_method = has_available_payment_method(
        'key_renewal',
        telegram_id=telegram_id,
        user_id=user_id,
        key_id=key_id,
    )

    if not has_payment_method:
        await render_page(
            target,
            page_key='renew_payment_unavailable',
            context=context,
            force_new=force_new,
        )
        return

    if not context['tariff_button_items']:
        await render_page(
            target,
            page_key='renew_payment_unavailable',
            context=context,
            force_new=force_new,
        )
        return

    await render_page(
        target,
        page_key='renew_payment',
        context=context,
        force_new=force_new,
    )


@router.callback_query(F.data.startswith('key_renew:'))
async def key_renew_select_payment(callback: CallbackQuery, state: FSMContext):
    """Selecting a payment method for renewal (immediately, without tariff)."""
    key_id = int(callback.data.split(':')[1])
    await dispatch_core_action(
        callback,
        'key.renew.start',
        {'key_id': key_id},
        source='callback',
        state=state,
    )


async def _execute_key_renew_start(request: CoreActionRequest) -> None:
    """Run the original renewal-page flow after action policy resolution."""
    from database.requests import get_key_details_for_user

    key_id = request.params['key_id']
    key = get_key_details_for_user(key_id, request.telegram_id)
    if not key:
        await _render_key_action_page(request.target, 'key_not_found')
        return
    await show_renew_payment_page(
        request.target,
        key,
        key_id,
        force_new=isinstance(request.target, Message),
    )
    if isinstance(request.target, CallbackQuery):
        await request.target.answer()

@router.callback_query(F.data.startswith('key_replace:'))
async def key_replace_start_handler(callback: CallbackQuery, state: FSMContext):
    """Beginning of the key replacement procedure."""
    from database.requests import get_key_details_for_user

    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await _render_key_action_page(callback, 'key_not_found')
        return
    action = 'key.configure.start' if not key.get('server_id') else 'key.replace.start'
    await dispatch_core_action(
        callback,
        action,
        {'key_id': key_id},
        source='callback',
        state=state,
    )


async def _execute_key_replace_or_configure(request: CoreActionRequest) -> None:
    """Run the original configure/replace entry flow after policy resolution."""
    from database.requests import (
        find_latest_paid_order_for_key,
        get_active_servers,
        get_key_details_for_user,
        is_traffic_exhausted,
    )
    from bot.utils.page_button_items import build_server_button_items
    from bot.utils.groups import get_servers_for_key
    from bot.utils.page_renderer import render_page

    callback = request.target
    state = request.state
    key_id = request.params['key_id']
    telegram_id = request.telegram_id
    if state is None:
        await _render_key_action_page(callback, 'key_operation_unavailable')
        return
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(callback, 'key_not_found')
        return
    is_unconfigured = not key.get('server_id')
    if request.action == 'key.configure.start' and not is_unconfigured:
        await _render_key_action_page(callback, 'key_operation_unavailable', key=key)
        return
    if request.action == 'key.replace.start' and is_unconfigured:
        await _render_key_action_page(callback, 'key_operation_unavailable', key=key)
        return
    if not key['is_active']:
        await _render_key_action_page(callback, 'key_operation_unavailable', key=key)
        return
    if is_traffic_exhausted(key):
        await _render_key_action_page(callback, 'key_operation_unavailable', key=key)
        return
    if request.action == 'key.configure.start':
        paid_order = find_latest_paid_order_for_key(key_id)
        if paid_order:
            from bot.handlers.user.payments.keys_config import run_new_key_setup_flow

            await run_new_key_setup_flow(
                callback,
                str(paid_order['order_id']),
                state=state,
                owner_telegram_id=telegram_id,
            )
            if isinstance(callback, CallbackQuery):
                await callback.answer()
            return
    tariff_id = key.get('tariff_id')
    servers = get_servers_for_key(tariff_id) if tariff_id else get_active_servers()
    if not servers:
        await _render_key_action_page(callback, 'key_operation_unavailable', key=key)
        return
    await state.set_state(ReplaceKey.users_server)
    await state.update_data(replace_key_id=key_id)
    await render_page(
        callback,
        page_key='key_replace_server_select',
        context={
            'telegram_id': telegram_id,
            'key_id': key_id,
            'server_button_items': build_server_button_items(
                servers,
                callback_prefix='replace_server',
            ),
            'key_flow_back_callback': f'key:{key_id}',
        },
    )
    if isinstance(callback, CallbackQuery):
        await callback.answer()

@router.callback_query(ReplaceKey.users_server, F.data.startswith('replace_server:'))
async def key_replace_server_handler(callback: CallbackQuery, state: FSMContext):
    """Validate the target server and proceed directly to confirmation."""
    from database.requests import get_server_by_id, get_key_details_for_user
    from bot.services.vpn_api import (
        get_client,
        get_client_inbound_descriptors,
        VPNAPIError,
    )
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_renderer import render_page

    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        logger.warning('Replacement target server %s was not found', server_id)
        await _render_key_action_page(callback, 'key_operation_unavailable')
        return
    await state.update_data(replace_server_id=server_id)
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await _render_key_action_page(callback, 'key_not_found')
        return
    try:
        client = await get_client(server_id)
        descriptors = await get_client_inbound_descriptors(
            client,
        )
        if not any(descriptor.available for descriptor in descriptors):
            await _render_key_action_page(
                callback,
                'key_replace_server_unavailable',
                key=key,
            )
            return
    except VPNAPIError as e:
        logger.warning('Failed to inspect replacement server %s: %s', server_id, e)
        await _render_key_action_page(callback, 'key_operation_failed', key=key)
        return
    await state.set_state(ReplaceKey.confirm)
    await render_page(
        callback,
        page_key='key_replace_confirm',
        context={
            'telegram_id': callback.from_user.id,
            'key_id': key_id,
            'key_flow_confirm_callback': 'replace_confirm',
            'key_flow_back_callback': f'key:{key_id}',
            'selected_server_name': server.get('name'),
            **build_key_page_context(key),
        },
    )
    await callback.answer()

@router.callback_query(ReplaceKey.confirm, F.data == 'replace_confirm')
@regular_panel_operation
async def key_replace_execute(callback: CallbackQuery, state: FSMContext):
    """Serialize replacement attempts for one key owner."""
    from bot.services.user_locks import user_locks
    from database.requests import get_key_details_for_user

    data = await state.get_data()
    key_id = data.get('replace_key_id')
    current_key = get_key_details_for_user(key_id, callback.from_user.id)
    if not current_key:
        logger.warning(
            'Replacement state is stale before locking (user=%s, key=%s)',
            callback.from_user.id,
            key_id,
        )
        await _render_key_action_page(callback, 'key_operation_unavailable')
        return

    lock_id = int(current_key.get('user_id') or callback.from_user.id)
    async with user_locks[lock_id]:
        await _key_replace_execute_locked(callback, state)


async def _key_replace_execute_locked(callback: CallbackQuery, state: FSMContext):
    """Replace a logical subscription without risking the current binding."""
    from database.requests import (
        get_key_details_for_user,
        get_server_by_id,
        update_key_traffic,
        update_vpn_key_binding,
    )
    from bot.services.vpn_api import (
        calculate_panel_total_for_key,
        get_client,
        get_key_expiry_time_ms,
        get_key_traffic_snapshot,
        provision_client_on_server,
        resolve_key_panel_limits,
        VPNAPIError,
    )
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import build_key_delivery_target, send_key_with_qr
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    new_server_id = data.get('replace_server_id')
    telegram_id = callback.from_user.id
    current_key = get_key_details_for_user(key_id, telegram_id)
    new_server_data = get_server_by_id(new_server_id)
    if not current_key or not new_server_data:
        logger.warning(
            'Replacement state is stale (user=%s, key=%s, server=%s)',
            telegram_id,
            key_id,
            new_server_id,
        )
        await _render_key_action_page(callback, 'key_operation_unavailable')
        return
    delivery_target = callback
    status_message = await _render_key_action_page(callback, 'key_progress', key=current_key)
    delivery_target = build_key_delivery_target(callback, status_message)

    candidate_email = None
    candidate_client = None
    binding_swapped = False

    try:
        traffic_limit = current_key.get('traffic_limit', 0) or 0
        traffic_used = current_key.get('traffic_used', 0) or 0
        old_client = None
        old_panel_email_managed = is_managed_panel_email(
            current_key.get('panel_email')
        )

        # === 1. Recording current traffic from the old client ===
        if (
            current_key.get('server_id')
            and current_key.get('server_active')
            and old_panel_email_managed
        ):
            old_client = await get_client(current_key['server_id'])
            if traffic_limit > 0:
                try:
                    snapshot = await get_key_traffic_snapshot(
                        old_client,
                        current_key,
                    )
                    if not snapshot:
                        raise VPNAPIError('панель не вернула счётчики трафика старого ключа')
                    traffic_used = snapshot['traffic_used']
                    update_key_traffic(key_id, traffic_used)
                    current_key['traffic_used'] = traffic_used
                    logger.info(
                        f"Перед заменой ключа {key_id} зафиксирован трафик: "
                        f"{traffic_used / 1024 ** 3:.1f} ГБ"
                    )
                except Exception as e:
                    raise VPNAPIError(
                        f'Не удалось обновить трафик старого ключа перед заменой: {e}'
                    )
        elif current_key.get('server_id') and current_key.get('server_active'):
            logger.warning(
                "Key replacement skipped previous logical client for key %s with "
                "unmanaged panel_email=%r",
                key_id,
                current_key.get('panel_email'),
            )

        if traffic_limit > 0 and traffic_used >= traffic_limit:
            await _render_key_action_page(
                delivery_target,
                'key_operation_unavailable',
                key=current_key,
            )
            return

        # === 2. Calculate the remaining entitlement ===
        user_fake_dict = {'telegram_id': telegram_id, 'username': current_key.get('username')}
        candidate_email = generate_unique_email(user_fake_dict)
        remaining_bytes = (
            calculate_panel_total_for_key(current_key, 0)
            if traffic_limit > 0
            else 0
        )
        exact_expiry_time_ms = get_key_expiry_time_ms(current_key)
        panel_limits = resolve_key_panel_limits(current_key)

        # === 3. Create the candidate before changing the database binding ===
        candidate_sub_id = uuid.uuid4().hex
        candidate_client = await get_client(new_server_id)
        provisioned = await provision_client_on_server(
            server_id=new_server_id,
            email=candidate_email,
            total_gb_bytes=remaining_bytes,
            expiry_time_ms=exact_expiry_time_ms,
            limit_ip=panel_limits.limit_ip,
            limit_hwid=panel_limits.limit_hwid,
            enable=True,
            tg_id=str(telegram_id),
            sub_id=candidate_sub_id,
            client=candidate_client,
        )
        candidate_sub_id = provisioned.sub_id
        if not provisioned.attached_inbound_ids or not candidate_sub_id:
            raise VPNAPIError('Панель не создала пригодную подписку')

        # === 4. Atomically switch ownership in the database ===
        if not update_vpn_key_binding(
            key_id,
            new_server_id,
            candidate_email,
            candidate_sub_id,
        ):
            raise VPNAPIError('Не удалось сохранить новую привязку ключа')
        binding_swapped = True
        try:
            from bot.services.subscription_composition import (
                schedule_key_subscription_reconciles,
            )

            schedule_key_subscription_reconciles(key_id=int(key_id))
        except Exception as error:
            logger.warning(
                'Could not immediately schedule subscription composition after '
                'key replacement key=%s type=%s',
                key_id,
                type(error).__name__,
            )

        # === 5. Remove the old logical client only after the DB switch ===
        if (
            current_key.get('server_id')
            and current_key.get('server_active')
            and old_panel_email_managed
        ):
            try:
                if old_client is None:
                    old_client = await get_client(current_key['server_id'])
                await old_client.delete_client(current_key['panel_email'])
            except Exception:
                logger.exception(
                    'Old logical client cleanup failed after key replacement '
                    '(key_id=%s, server_id=%s, email=%s)',
                    key_id,
                    current_key.get('server_id'),
                    current_key.get('panel_email'),
                )

        # === 6. Traffic transfer and partial-placement repair ===
        if traffic_limit > 0:
            logger.info(
                f'Перенос трафика ключа {key_id}: остаток {remaining_bytes / 1024 ** 3:.1f} ГБ, '
                f'полный тариф {traffic_limit / 1024 ** 3:.1f} ГБ, '
                f'использовано {traffic_used / 1024 ** 3:.1f} ГБ'
            )
        if not provisioned.complete:
            from bot.services.vpn_api import sync_key_to_panel_state
            sync_kwargs = (
                {'panel_snapshot': provisioned.snapshot}
                if provisioned.snapshot is not None
                else {}
            )
            sync_stats = await sync_key_to_panel_state(key_id, **sync_kwargs)
            if not sync_stats.get('ok'):
                logger.warning(f"replace_execute: subscription-ключ {key_id} синхронизирован не полностью: {sync_stats}")

        await state.clear()
        updated_key = get_key_details_for_user(key_id, telegram_id)
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

        await emit_key_lifecycle_event_safe(
            'key_replaced',
            {
                'key_id': key_id,
                'user_id': current_key.get('user_id'),
                'telegram_id': telegram_id,
                'old_key': dict(current_key),
                'new_key': dict(updated_key or {}),
                'old_server_id': current_key.get('server_id'),
                'new_server_id': new_server_id,
                'traffic_limit': traffic_limit,
                'traffic_used': traffic_used,
                'remaining_bytes': remaining_bytes,
            },
        )
        await send_key_with_qr(delivery_target, updated_key, is_new=True)
        try:
            from bot.handlers.user.subscription_hosts import (
                offer_default_subscription_host,
            )

            await offer_default_subscription_host(
                callback,
                component_key_id=int(key_id),
                telegram_id=telegram_id,
            )
        except Exception:
            logger.exception(
                'Post-delivery subscription host flow failed after replacement key=%s',
                key_id,
            )
    except Exception as e:
        if candidate_client is not None and candidate_email and not binding_swapped:
            try:
                await candidate_client.delete_client(candidate_email)
            except Exception:
                logger.exception(
                    'Failed to clean replacement candidate key_id=%s email=%s',
                    key_id,
                    candidate_email,
                )
        logger.exception(
            'Key replacement failed user=%s key_id=%s: %s',
            callback.from_user.id,
            key_id,
            e,
        )
        await _render_key_action_page(delivery_target, 'key_operation_failed', key=current_key)

@router.callback_query(F.data.startswith('key_rename:'))
async def key_rename_start_handler(callback: CallbackQuery, state: FSMContext):
    """Start renaming the key."""
    key_id = int(callback.data.split(':')[1])
    await dispatch_core_action(
        callback,
        'key.rename.start',
        {'key_id': key_id},
        source='callback',
        state=state,
    )


async def _execute_key_rename_start(request: CoreActionRequest) -> None:
    """Run the original rename entry flow after action policy resolution."""
    from database.requests import get_key_details_for_user
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_renderer import render_page

    callback = request.target
    state = request.state
    key_id = request.params['key_id']
    telegram_id = request.telegram_id
    if state is None:
        await _render_key_action_page(callback, 'key_operation_unavailable')
        return
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await _render_key_action_page(callback, 'key_not_found')
        return
    await state.set_state(RenameKey.waiting_for_name)
    await state.update_data(key_id=key_id)
    await render_page(
        callback,
        page_key='key_rename_prompt',
        context={
            'telegram_id': telegram_id,
            'key_id': key_id,
            'key_flow_back_callback': f'key:{key_id}',
            **build_key_page_context(key),
        },
    )
    if isinstance(callback, CallbackQuery):
        await callback.answer()

@router.message(RenameKey.waiting_for_name)
async def key_rename_submit_handler(message: Message, state: FSMContext):
    """Processing the entry of a new key name."""
    from database.requests import update_key_custom_name
    from bot.utils.text import get_message_text_for_storage
    data = await state.get_data()
    key_id = data.get('key_id')
    new_name = get_message_text_for_storage(message, 'plain')
    if not key_id:
        await state.clear()
        await _render_key_action_page(message, 'key_operation_unavailable', force_new=True)
        return
    if not new_name or len(new_name) > 30:
        await _render_key_action_page(message, 'key_rename_invalid', force_new=True)
        return
    success = update_key_custom_name(key_id, message.from_user.id, new_name)
    await state.clear()
    if not success:
        logger.warning('Failed to rename key %s for user %s', key_id, message.from_user.id)
        await _render_key_action_page(message, 'key_operation_failed', force_new=True)
        return
    await show_key_details(message.from_user.id, key_id, message, is_callback=False)


async def _render_key_action_page(
    target,
    page_key: str,
    *,
    key: dict | None = None,
    force_new: bool = False,
):
    """Render a database-backed key state and keep callback alerts empty."""
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_renderer import render_page

    context = {
        'telegram_id': getattr(getattr(target, 'from_user', None), 'id', None),
    }
    if key:
        context.update(build_key_page_context(key))
        context['key_id'] = key.get('id')
    rendered = await render_page(
        target,
        page_key=page_key,
        context=context,
        force_new=force_new,
    )
    if isinstance(target, CallbackQuery):
        await target.answer()
    return rendered


register_core_action_executor('key.renew.start', _execute_key_renew_start, replace=True)
register_core_action_executor('key.configure.start', _execute_key_replace_or_configure, replace=True)
register_core_action_executor('key.replace.start', _execute_key_replace_or_configure, replace=True)
register_core_action_executor('key.rename.start', _execute_key_rename_start, replace=True)
register_core_action_executor('key.delete', _execute_key_delete, replace=True)
