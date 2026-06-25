import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

router = Router()

async def start_new_key_config(message: Message, state: FSMContext, order_id: str, key_id: int=None):
    """
    Запускает процесс настройки нового ключа (выбор сервера).
    Используется как для Stars, так и для Crypto.
    """
    from database.requests import get_active_servers, find_order_by_order_id
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    from bot.utils.key_pages import build_new_key_server_select_data, keyboard_rows
    from bot.utils.groups import get_servers_for_key
    from bot.utils.page_renderer import render_page
    order = find_order_by_order_id(order_id)
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
    await state.update_data(new_key_order_id=order_id, new_key_id=key_id)
    await render_page(
        message,
        page_key='new_key_server_select',
        text_replacements={'%данныеэкрана%': build_new_key_server_select_data()},
        prepend_buttons=keyboard_rows(new_key_server_list_kb(servers, include_home=False)),
        force_new=True,
    )

@router.callback_query(F.data.startswith('new_key_server:'))
async def process_new_key_server_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для нового ключа."""
    from database.requests import get_server_by_id
    from bot.services.vpn_api import get_client, VPNAPIError, is_subscription_mode
    from bot.keyboards.user import new_key_inbound_list_kb
    from bot.states.user_states import NewKeyConfig
    from bot.utils.key_pages import build_server_screen_data, keyboard_rows
    from bot.utils.page_renderer import render_page
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await state.update_data(new_key_server_id=server_id)

    # Subscription mode: выбор inbound не нужен — создаём ключ во всех inbound сразу
    if is_subscription_mode():
        await process_new_key_subscription_final(callback, state, server_id)
        return

    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        if not inbounds:
            await callback.answer('❌ На сервере нет доступных протоколов', show_alert=True)
            return
        if len(inbounds) == 1:
            await process_new_key_final(callback, state, server_id, inbounds[0]['id'])
            return
        await state.set_state(NewKeyConfig.waiting_for_inbound)
        await render_page(
            callback,
            page_key='new_key_inbound_select',
            text_replacements={'%данныеэкрана%': build_server_screen_data(server)},
            prepend_buttons=keyboard_rows(new_key_inbound_list_kb(inbounds)),
        )
    except VPNAPIError as e:
        await callback.answer(f'❌ Ошибка подключения: {e}', show_alert=True)
    await callback.answer()


async def process_new_key_subscription_final(callback: CallbackQuery, state: FSMContext, server_id: int):
    """
    Финальный этап создания ключа в режиме Subscription.

    Создаёт клиента во ВСЕХ inbound сервера с одним subId и одним email.
    В БД сохраняется только одна запись vpn_keys с panel_inbound_id=min_id
    и sub_id, который объединяет всех клиентов на панели в одну подписку.
    """
    import uuid as _uuid
    from database.requests import (
        find_order_by_order_id, update_payment_key_id,
        get_key_details_for_user, create_initial_vpn_key,
        get_tariff_by_id, update_vpn_key_config,
    )
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb

    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    if not order_id:
        await safe_edit_or_send(callback.message, '❌ Ошибка: потерян номер заказа.')
        await state.clear()
        return
    order = find_order_by_order_id(order_id)
    if not order:
        await safe_edit_or_send(callback.message, '❌ Ошибка: заказ не найден.')
        await state.clear()
        return
    if not key_id:
        if order['vpn_key_id']:
            key_id = order['vpn_key_id']
        else:
            days = order.get('period_days') or order.get('duration_days') or 30
            _tariff = get_tariff_by_id(order['tariff_id'])
            traffic_limit_bytes = (_tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3 if _tariff else 0
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days, traffic_limit=traffic_limit_bytes)
            update_payment_key_id(order_id, key_id)

    await safe_edit_or_send(callback.message, '⏳ Настраиваем вашу подписку...')

    try:
        telegram_id = callback.from_user.id
        username = callback.from_user.username
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        sub_id = _uuid.uuid4().hex

        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
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

        await state.clear()
        new_key = get_key_details_for_user(key_id, telegram_id)
        await send_key_with_qr(callback, new_key, key_issued_kb(), is_new=True)
    except Exception as e:
        logger.error(f'Ошибка настройки subscription-ключа (id={key_id}): {e}')
        await safe_edit_or_send(callback.message,
            f'❌ Ошибка настройки ключа: {escape_html(str(e))}\n'
            f'Обратитесь в поддержку, указав Order ID: {order_id}')

@router.callback_query(F.data.startswith('new_key_inbound:'))
async def process_new_key_inbound_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор протокола (inbound) для нового ключа."""
    inbound_id = int(callback.data.split(':')[1])
    data = await state.get_data()
    server_id = data.get('new_key_server_id')
    await process_new_key_final(callback, state, server_id, inbound_id)

async def process_new_key_final(callback: CallbackQuery, state: FSMContext, server_id: int, inbound_id: int):
    """Финальный этап создания ключа."""
    from database.requests import get_server_by_id, update_vpn_key_config, update_payment_key_id, find_order_by_order_id, get_user_internal_id, get_key_details_for_user, create_initial_vpn_key
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    if not order_id:
        await safe_edit_or_send(callback.message, '❌ Ошибка: потерян номер заказа.')
        await state.clear()
        return
    order = find_order_by_order_id(order_id)
    if not order:
        await safe_edit_or_send(callback.message, '❌ Ошибка: заказ не найден.')
        await state.clear()
        return
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
    await safe_edit_or_send(callback.message, '⏳ Настраиваем ваш ключ...')
    try:
        user_id = order['user_id']
        telegram_id = callback.from_user.id
        username = callback.from_user.username
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        client = await get_client(server_id)
        days = order.get('period_days') or order.get('duration_days') or 30
        # Лимит трафика из тарифа (0 = безлимит на панели)
        from database.requests import get_tariff_by_id as _get_tariff_for_limit
        _tariff_data = _get_tariff_for_limit(order['tariff_id'])
        limit_gb = (_tariff_data.get('traffic_limit_gb', 0) or 0) if _tariff_data else 0
        flow = await client.get_inbound_flow(inbound_id)
        res = await client.add_client(inbound_id=inbound_id, email=panel_email, total_gb=limit_gb, expire_days=days, limit_ip=_tariff_data.get('max_ips', 1) if _tariff_data else 1, enable=True, tg_id=str(telegram_id), flow=flow)
        client_uuid = res['uuid']
        update_vpn_key_config(key_id=key_id, server_id=server_id, panel_inbound_id=inbound_id, panel_email=panel_email, client_uuid=client_uuid)
        update_payment_key_id(order_id, key_id)
        await state.clear()
        new_key = get_key_details_for_user(key_id, telegram_id)
        await send_key_with_qr(callback, new_key, key_issued_kb(), is_new=True)
    except Exception as e:
        logger.error(f'Ошибка настройки ключа (id={key_id}): {e}')
        await safe_edit_or_send(callback.message, f'❌ Ошибка настройки ключа: {escape_html(str(e))}\nОбратитесь в поддержку, указав Order ID: ' + str(order_id))

@router.callback_query(F.data == 'back_to_server_select')
async def back_to_server_select(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору сервера."""
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
    await render_page(
        callback,
        page_key='new_key_server_select',
        text_replacements={'%данныеэкрана%': build_new_key_server_back_data()},
        prepend_buttons=keyboard_rows(new_key_server_list_kb(servers, include_home=False)),
    )
