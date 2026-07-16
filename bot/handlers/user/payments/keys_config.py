import logging
from types import SimpleNamespace
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html
from bot.services.panel_sync_coordinator import regular_panel_operation
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

router = Router()


def _is_callback_target(target) -> bool:
    return isinstance(target, CallbackQuery) or (
        not isinstance(target, Message)
        and getattr(target, 'message', None) is not None
        and getattr(target, 'from_user', None) is not None
    )


def _target_message(target):
    return target.message if _is_callback_target(target) else target


def _target_user(target):
    return target.from_user


def _target_with_message(target, message: Message, from_user=None):
    if _is_callback_target(target):
        return target
    return SimpleNamespace(message=message, from_user=from_user or _target_user(target))


def _owner_from_order(order: dict | None) -> tuple[int | None, str | None]:
    """Determines the owner of the new key based on the internal user of the order."""
    if not order or not order.get('user_id'):
        return None, None

    try:
        from database.requests import get_user_by_id

        user = get_user_by_id(order['user_id'])
    except Exception as e:
        logger.warning(
            "Не удалось получить владельца заказа %s по user_id=%s: %s",
            order.get('order_id') if order else None,
            order.get('user_id') if order else None,
            e,
        )
        return None, None

    if not user:
        return None, None
    return user.get('telegram_id'), user.get('username')


def _resolve_new_key_owner(
    target,
    order: dict | None,
    *,
    owner_telegram_id: int | None = None,
    owner_username: str | None = None,
    state_data: dict | None = None,
) -> tuple[int | None, str | None]:
    """Selects the key owner: explicit parameter/FSM, then order."""
    state_data = state_data or {}
    telegram_id = owner_telegram_id or state_data.get('new_key_owner_telegram_id')
    username = owner_username if owner_username is not None else state_data.get('new_key_owner_username')

    if telegram_id:
        return telegram_id, username

    order_telegram_id, order_username = _owner_from_order(order)
    if order_telegram_id:
        return order_telegram_id, order_username

    return None, None


def _owner_user_stub(telegram_id: int | None, username: str | None):
    """Minimum user object for rendering key issuance."""
    if not telegram_id:
        return None
    return SimpleNamespace(id=telegram_id, username=username)


async def _safe_edit_target(target, text: str, **kwargs):
    from bot.utils.key_status_page import render_key_status_page

    force_new = kwargs.pop('force_new', False)
    if not _is_callback_target(target):
        force_new = True

    title_html, body_text = _build_key_status_parts(text)
    return await render_key_status_page(
        _target_message(target),
        title_html=title_html,
        body_text=body_text,
        force_new=force_new,
        **kwargs,
    )


def _build_key_status_parts(text: str) -> tuple[str, str]:
    """Divides a short plain-text status into an HTML header and a plain body."""
    plain = str(text or '').strip()
    first_line, separator, rest = plain.partition('\n')
    indicator = ''
    for prefix in ('❌', '⚠️', '⏳', '📊', '✅'):
        if first_line.startswith(prefix):
            indicator = prefix
            first_line = first_line[len(prefix):].strip()
            break

    title_text = first_line or 'Статус ключа'
    title_prefix = f'{indicator} ' if indicator else ''
    title_html = f'{title_prefix}<b>{escape_html(title_text)}</b>'
    body_text = rest.strip() if separator else ''
    return title_html, body_text


async def _show_target_error(target, text: str) -> None:
    if _is_callback_target(target):
        await target.answer(text, show_alert=True)
        return
    await _safe_edit_target(target, text)


async def _answer_callback_if_needed(target, *args, **kwargs) -> None:
    if _is_callback_target(target):
        await target.answer(*args, **kwargs)


async def _continue_new_key_config(target, state: FSMContext, server: dict, *, force_new: bool = False):
    """Continues key configuration after manual or automatic server selection."""
    from bot.services.vpn_api import get_client, VPNAPIError, is_subscription_mode
    from bot.keyboards.user import new_key_inbound_list_kb
    from bot.states.user_states import NewKeyConfig
    from bot.utils.key_pages import build_server_screen_data, keyboard_rows
    from bot.utils.page_renderer import render_page

    server_id = server['id']
    await state.update_data(new_key_server_id=server_id)

    # Subscription mode: selecting inbound is not needed - we create a key in all inbounds at once.
    if is_subscription_mode():
        await process_new_key_subscription_final(target, state, server_id)
        return

    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        if not inbounds:
            await _show_target_error(target, '❌ На сервере нет доступных протоколов')
            return
        if len(inbounds) == 1:
            await process_new_key_final(target, state, server_id, inbounds[0]['id'])
            return
        await state.set_state(NewKeyConfig.waiting_for_inbound)
        screen_data = build_server_screen_data(server)
        await render_page(
            target,
            page_key='new_key_inbound_select',
            text_replacements={
                '%screen_data%': screen_data,
                '%экран_данные%': screen_data,
            },
            prepend_buttons=keyboard_rows(new_key_inbound_list_kb(inbounds)),
            force_new=force_new,
        )
        await _answer_callback_if_needed(target)
    except VPNAPIError as e:
        await _show_target_error(target, f'❌ Ошибка подключения: {e}')


async def start_new_key_config(
    message: Message,
    state: FSMContext,
    order_id: str,
    key_id: int = None,
    owner_telegram_id: int | None = None,
    owner_username: str | None = None,
):
    """
    Starts the process of setting up a new key (server selection).
    Used for both Stars and Crypto.
    """
    from database.requests import get_active_servers, find_order_by_order_id
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    from bot.utils.key_pages import build_new_key_server_select_data, keyboard_rows
    from bot.utils.groups import get_servers_for_key
    from bot.utils.page_renderer import render_page
    order = find_order_by_order_id(order_id)
    owner_telegram_id, owner_username = _resolve_new_key_owner(
        message,
        order,
        owner_telegram_id=owner_telegram_id,
        owner_username=owner_username,
    )
    tariff_id = order.get('tariff_id') if order else None
    if tariff_id:
        servers = get_servers_for_key(tariff_id)
    else:
        servers = get_active_servers()
    if not servers:
        logger.error(f'Нет активных серверов для создания ключа (Order: {order_id})')
        await render_page(message, page_key='new_key_no_servers', force_new=True)
        return
    await state.set_state(NewKeyConfig.waiting_for_server)
    await state.update_data(
        new_key_order_id=order_id,
        new_key_id=key_id,
        new_key_owner_telegram_id=owner_telegram_id,
        new_key_owner_username=owner_username,
    )
    if len(servers) == 1:
        logger.info(
            f"Автовыбор единственного сервера {servers[0]['id']} "
            f"для нового ключа (Order: {order_id})"
        )
        await _continue_new_key_config(message, state, servers[0], force_new=True)
        return
    screen_data = build_new_key_server_select_data()
    await render_page(
        message,
        page_key='new_key_server_select',
        text_replacements={
            '%screen_data%': screen_data,
            '%экран_данные%': screen_data,
        },
        prepend_buttons=keyboard_rows(new_key_server_list_kb(servers)),
        force_new=True,
    )

@router.callback_query(F.data.startswith('new_key_server:'))
async def process_new_key_server_selection(callback: CallbackQuery, state: FSMContext):
    """Selecting a server for a new key."""
    from database.requests import get_server_by_id
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await _continue_new_key_config(callback, state, server)


@regular_panel_operation
async def process_new_key_subscription_final(target, state: FSMContext, server_id: int):
    """
    The final stage of creating a key in Subscription mode.

    Creates a client in ALL inbound servers with one subId and one email.
    Only one entry vpn_keys with panel_inbound_id=min_id is saved in the database
    and sub_id, which combines all clients on the panel into one subscription.
    """
    import uuid as _uuid
    from database.requests import (
        find_order_by_order_id, update_payment_key_id,
        get_key_details_for_user, create_initial_vpn_key,
        get_tariff_by_id, update_vpn_key_config,
    )
    from bot.services.vpn_api import get_client, get_client_subscription_inbounds
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr

    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    if not order_id:
        await _safe_edit_target(target, '❌ Ошибка: потерян номер заказа.')
        await state.clear()
        return
    order = find_order_by_order_id(order_id)
    if not order:
        await _safe_edit_target(target, '❌ Ошибка: заказ не найден.')
        await state.clear()
        return
    owner_telegram_id, owner_username = _resolve_new_key_owner(
        target,
        order,
        state_data=data,
    )
    if not owner_telegram_id:
        await _safe_edit_target(target, '❌ Ошибка: не удалось определить владельца ключа.')
        await state.clear()
        return
    await state.update_data(
        new_key_owner_telegram_id=owner_telegram_id,
        new_key_owner_username=owner_username,
    )
    created_key = False
    if not key_id:
        if order['vpn_key_id']:
            key_id = order['vpn_key_id']
        else:
            days = order.get('period_days') or order.get('duration_days') or 30
            _tariff = get_tariff_by_id(order['tariff_id'])
            traffic_limit_bytes = (_tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3 if _tariff else 0
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days, traffic_limit=traffic_limit_bytes)
            update_payment_key_id(order_id, key_id)
            created_key = True
            from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

            await emit_key_lifecycle_event_safe(
                'key_created',
                {
                    'key_id': key_id,
                    'user_id': order['user_id'],
                    'tariff_id': order['tariff_id'],
                    'days': days,
                    'traffic_limit': traffic_limit_bytes,
                    'order_id': order_id,
                    'payment_type': order.get('payment_type'),
                    'source': 'key_config_fallback',
                },
            )

    progress_message = await _safe_edit_target(target, '⏳ Настраиваем вашу подписку...')

    try:
        telegram_id = owner_telegram_id
        username = owner_username
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        sub_id = _uuid.uuid4().hex

        client = await get_client(server_id)
        inbounds = await get_client_subscription_inbounds(client)
        if not inbounds:
            raise RuntimeError('На сервере нет доступных inbound')

        days = order.get('period_days') or order.get('duration_days') or 30
        _tariff_data = get_tariff_by_id(order['tariff_id'])
        limit_gb = (_tariff_data.get('traffic_limit_gb', 0) or 0) if _tariff_data else 0

        first_uuid = None
        first_inbound_id = None
        ready_count = 0
        for inb in inbounds:
            try:
                flow = await client.get_inbound_flow(inb['id'])
                res = await client.add_client(
                    inbound_id=inb['id'],
                    email=panel_email,
                    total_gb=limit_gb,
                    expire_days=days,
                    limit_ip=_tariff_data.get('max_ips', 1) if _tariff_data else 1,
                    enable=True,
                    tg_id=str(telegram_id),
                    flow=flow,
                    sub_id=sub_id,
                )
                if first_uuid is None or inb['id'] < first_inbound_id:
                    first_uuid = res['uuid']
                    first_inbound_id = inb['id']
                ready_count += 1
            except Exception as e:
                logger.warning(
                    f"subscription_final: не удалось создать клиента в inbound {inb['id']} "
                    f"(key_id={key_id}): {e}. Допустимо — синхронизатор доберёт позже."
                )

        if ready_count == 0 or first_uuid is None or first_inbound_id is None:
            raise RuntimeError('Не удалось создать ни одного клиента на сервере')

        update_vpn_key_config(
            key_id=key_id,
            server_id=server_id,
            panel_inbound_id=first_inbound_id,
            panel_email=panel_email,
            client_uuid=first_uuid,
            sub_id=sub_id,
        )
        update_payment_key_id(order_id, key_id)
        from bot.services.vpn_api import sync_key_to_panel_state
        sync_stats = await sync_key_to_panel_state(key_id)
        if not sync_stats.get('ok'):
            logger.warning(f"subscription_final: ключ {key_id} синхронизирован не полностью: {sync_stats}")
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

        await emit_key_lifecycle_event_safe(
            'key_configured',
            {
                'key_id': key_id,
                'user_id': order['user_id'],
                'tariff_id': order['tariff_id'],
                'order_id': order_id,
                'server_id': server_id,
                'panel_inbound_id': first_inbound_id,
                'panel_email': panel_email,
                'sub_id': sub_id,
                'subscription_mode': True,
                'sync_stats': sync_stats,
                'created_in_this_flow': created_key,
            },
        )

        await state.clear()
        new_key = get_key_details_for_user(key_id, telegram_id)
        await send_key_with_qr(
            _target_with_message(
                target,
                progress_message,
                from_user=_owner_user_stub(telegram_id, username),
            ),
            new_key,
            is_new=True,
        )
    except Exception as e:
        logger.error(f'Ошибка настройки subscription-ключа (id={key_id}): {e}')
        await _safe_edit_target(target,
            f'❌ Ошибка настройки ключа: {e}\n'
            f'Обратитесь в поддержку, указав Order ID: {order_id}')

@router.callback_query(F.data.startswith('new_key_inbound:'))
async def process_new_key_inbound_selection(callback: CallbackQuery, state: FSMContext):
    """Selecting a protocol (inbound) for the new key."""
    inbound_id = int(callback.data.split(':')[1])
    data = await state.get_data()
    server_id = data.get('new_key_server_id')
    await process_new_key_final(callback, state, server_id, inbound_id)

@regular_panel_operation
async def process_new_key_final(target, state: FSMContext, server_id: int, inbound_id: int):
    """The final stage of key creation."""
    from database.requests import get_server_by_id, update_vpn_key_config, update_payment_key_id, find_order_by_order_id, get_user_internal_id, get_key_details_for_user, create_initial_vpn_key
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    if not order_id:
        await _safe_edit_target(target, '❌ Ошибка: потерян номер заказа.')
        await state.clear()
        return
    order = find_order_by_order_id(order_id)
    if not order:
        await _safe_edit_target(target, '❌ Ошибка: заказ не найден.')
        await state.clear()
        return
    owner_telegram_id, owner_username = _resolve_new_key_owner(
        target,
        order,
        state_data=data,
    )
    if not owner_telegram_id:
        await _safe_edit_target(target, '❌ Ошибка: не удалось определить владельца ключа.')
        await state.clear()
        return
    await state.update_data(
        new_key_owner_telegram_id=owner_telegram_id,
        new_key_owner_username=owner_username,
    )
    created_key = False
    if not key_id:
        if order['vpn_key_id']:
            key_id = order['vpn_key_id']
        else:
            days = order.get('period_days') or order.get('duration_days') or 30
            from database.requests import get_tariff_by_id as _get_tariff
            _tariff = _get_tariff(order['tariff_id'])
            traffic_limit_bytes = (_tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3 if _tariff else 0
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days, traffic_limit=traffic_limit_bytes)
            update_payment_key_id(order_id, key_id)
            created_key = True
            from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

            await emit_key_lifecycle_event_safe(
                'key_created',
                {
                    'key_id': key_id,
                    'user_id': order['user_id'],
                    'tariff_id': order['tariff_id'],
                    'days': days,
                    'traffic_limit': traffic_limit_bytes,
                    'order_id': order_id,
                    'payment_type': order.get('payment_type'),
                    'source': 'key_config_fallback',
                },
            )
    progress_message = await _safe_edit_target(target, '⏳ Настраиваем ваш ключ...')
    try:
        telegram_id = owner_telegram_id
        username = owner_username
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        client = await get_client(server_id)
        days = order.get('period_days') or order.get('duration_days') or 30
        # Traffic limit from the tariff (0 = unlimited on the panel)
        from database.requests import get_tariff_by_id as _get_tariff_for_limit
        _tariff_data = _get_tariff_for_limit(order['tariff_id'])
        limit_gb = (_tariff_data.get('traffic_limit_gb', 0) or 0) if _tariff_data else 0
        flow = await client.get_inbound_flow(inbound_id)
        res = await client.add_client(inbound_id=inbound_id, email=panel_email, total_gb=limit_gb, expire_days=days, limit_ip=_tariff_data.get('max_ips', 1) if _tariff_data else 1, enable=True, tg_id=str(telegram_id), flow=flow)
        client_uuid = res['uuid']
        update_vpn_key_config(key_id=key_id, server_id=server_id, panel_inbound_id=inbound_id, panel_email=panel_email, client_uuid=client_uuid)
        update_payment_key_id(order_id, key_id)
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

        await emit_key_lifecycle_event_safe(
            'key_configured',
            {
                'key_id': key_id,
                'user_id': order['user_id'],
                'tariff_id': order['tariff_id'],
                'order_id': order_id,
                'server_id': server_id,
                'panel_inbound_id': inbound_id,
                'panel_email': panel_email,
                'client_uuid': client_uuid,
                'subscription_mode': False,
                'created_in_this_flow': created_key,
            },
        )
        await state.clear()
        new_key = get_key_details_for_user(key_id, telegram_id)
        await send_key_with_qr(
            _target_with_message(
                target,
                progress_message,
                from_user=_owner_user_stub(telegram_id, username),
            ),
            new_key,
            is_new=True,
        )
    except Exception as e:
        logger.error(f'Ошибка настройки ключа (id={key_id}): {e}')
        await _safe_edit_target(target, f'❌ Ошибка настройки ключа: {e}\nОбратитесь в поддержку, указав Order ID: ' + str(order_id))

@router.callback_query(F.data == 'back_to_server_select')
async def back_to_server_select(callback: CallbackQuery, state: FSMContext):
    """Return to server selection."""
    from database.requests import get_active_servers, find_order_by_order_id
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    from bot.utils.key_pages import build_new_key_server_back_data, keyboard_rows
    from bot.utils.groups import get_servers_for_key
    from bot.utils.page_renderer import render_page
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    tariff_id = None
    if order_id:
        order = find_order_by_order_id(order_id)
        tariff_id = order.get('tariff_id') if order else None
    servers = get_servers_for_key(tariff_id) if tariff_id else get_active_servers()
    await state.set_state(NewKeyConfig.waiting_for_server)
    screen_data = build_new_key_server_back_data()
    await render_page(
        callback,
        page_key='new_key_server_select',
        text_replacements={
            '%screen_data%': screen_data,
            '%экран_данные%': screen_data,
        },
        prepend_buttons=keyboard_rows(new_key_server_list_kb(servers)),
    )
