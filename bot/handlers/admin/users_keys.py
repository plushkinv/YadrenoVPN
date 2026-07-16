import logging
import uuid
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, create_vpn_key_admin, get_active_servers, get_all_tariffs, get_user_balance, get_user_referral_coefficient, add_to_balance, deduct_from_balance, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.datetime_format import format_datetime_for_display
from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.panel_email import get_panel_email_prefix
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.vpn_api import (
    get_client_from_server_data,
    VPNAPIError,
    format_traffic,
    get_client_subscription_inbounds,
)
from bot.handlers.admin.users_manage import format_user_display, _show_user_view_edit
from bot.handlers.admin.users_list import show_users_menu
from bot.services.panel_sync_coordinator import regular_panel_operation

logger = logging.getLogger(__name__)

router = Router()
USERS_PER_PAGE = 20

def generate_unique_email(user: dict) -> str:
    """
    Generates a unique email for the 3X-UI panel.
    Format: user_{username/id}_{random_suffix}
    """
    suffix = uuid.uuid4().hex[:5]
    return f'{get_panel_email_prefix(user)}{suffix}'

@router.callback_query(F.data.startswith('admin_key_view:'))
async def show_key_view(callback: CallbackQuery, state: FSMContext):
    """Shows the key management screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    if key.get('custom_name'):
        key_name = key['custom_name']
    else:
        uuid = key.get('client_uuid') or ''
        if len(uuid) >= 8:
            key_name = f'{uuid[:4]}...{uuid[-4:]}'
        else:
            key_name = uuid or f'Ключ #{key_id}'
    server_name = key.get('server_name', 'Неизвестный сервер')
    tariff_name = key.get('tariff_name', 'Неизвестный тариф')
    expires_at = format_datetime_for_display(key.get('expires_at'), fallback='?')
    created_at = format_datetime_for_display(key.get('created_at'), fallback='?')
    panel_email = key.get('panel_email')
    if panel_email:
        panel_email_line = f'📧 E-mail в панели: <code>{escape_html(panel_email)}</code>'
    else:
        panel_email_line = '📧 E-mail в панели: <i>не указан</i>'
    text = f'🔑 <b>{key_name}</b>\n\n🖥️ Сервер: {server_name}\n📋 Тариф: {tariff_name}\n{panel_email_line}\n📅 Создан: {created_at}\n⏰ Истекает: {expires_at}\n'
    from database.requests import is_key_active, is_traffic_exhausted
    if not is_key_active(key):
        if is_traffic_exhausted(key):
            text += '\n❌ <b>Трафик исчерпан</b>\n'
        else:
            text += '\n⏳ <b>Срок действия истёк</b>\n'
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0:
        remaining = max(0, traffic_limit - traffic_used)
        text += f'\n📊 <b>Трафик:</b>\n  ✅ Использовано: {format_traffic(traffic_used)}\n  🎯 Лимит: {format_traffic(traffic_limit)}\n  💾 Остаток: {format_traffic(remaining)}\n'
    else:
        text += f'\n📊 <b>Трафик:</b>\n  ✅ Использовано: {format_traffic(traffic_used)}\n  ∞ Без лимита\n'
    from database.requests import get_key_payments_history
    payments_history = get_key_payments_history(key_id)
    if payments_history:
        text += '\n📜 <b>История операций:</b>\n'
        for p in payments_history:
            dt = format_datetime_for_display(p.get('paid_at'), fallback='?')
            if p.get('history_type') == 'key_operation':
                delta_days = int(p.get('delta_days') or 0)
                reason_safe = escape_html(p.get('reason') or 'Начисление дней')
                if delta_days > 0:
                    text += f'• <code>{dt}</code>: {reason_safe} (+{delta_days} дн.)\n'
                else:
                    text += f'• <code>{dt}</code>: {reason_safe}\n'
                continue
            amount = ''
            if p.get('payment_type') == 'crypto':
                usd = p['amount_cents'] / 100
                usd_str = f'{usd:g}'.replace('.', ',')
                amount = f'${usd_str}'
            elif p.get('payment_type') == 'stars':
                amount = f"{p['amount_stars']} ⭐"
            elif p.get('payment_type') in ('cards', 'yookassa_qr', 'wata', 'platega', 'cardlink', 'balance'):
                rub = p.get('price_rub') or 0
                rub_str = f'{rub:g}'.replace('.', ',')
                amount = f'{rub_str} ₽'
            else:
                amount = '?'
            tariff_safe = escape_html(p['tariff_name'] or 'Неизвестно')
            text += f'• <code>{dt}</code>: {amount} — {tariff_safe}\n'
    else:
        text += '\n📜 <b>История операций:</b> пусто\n'
    user_telegram_id = key.get('telegram_id')
    await safe_edit_or_send(callback.message, text, reply_markup=key_view_kb(key_id, user_telegram_id))
    await callback.answer()

@router.callback_query(F.data.startswith('admin_key_extend:'))
async def start_key_extend(callback: CallbackQuery, state: FSMContext):
    """Start of key renewal."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    await state.set_state(AdminStates.key_extend_days)
    await state.update_data(current_key_id=key_id)
    await safe_edit_or_send(callback.message, '📅 <b>Изменение срока действия ключа</b>\n\nВведите количество дней (можно отрицательное, чтобы уменьшить срок):', reply_markup=key_action_cancel_kb(key_id, 0))
    await callback.answer()

@router.message(AdminStates.key_extend_days, F.text, ~F.text.startswith('/'))
async def process_key_extend(message: Message, state: FSMContext):
    """Processing the entry of days for extension."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.lstrip('-').isdigit() or int(text) < -99999 or int(text) > 99999 or int(text) == 0:
        await safe_edit_or_send(message, '❌ Введите число от -99999 до 99999 (кроме 0)')
        return
    days = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    from bot.services.key_lifecycle import renew_key_access
    result = await renew_key_access(key_id, days, reset_traffic=True)
    if result['db_updated']:
        action_text = f'уменьшен на {abs(days)}' if days < 0 else f'продлён на {days}'
        result_text = f'✅ Срок действия ключа {action_text} дней!'
        if not result['panel_synced']:
            result_text += '\n\n⚠️ БД обновлена, но панель синхронизирована не полностью. Повторная синхронизация сможет дожать состояние.'
        await safe_edit_or_send(message, result_text, force_new=True)
        key = get_vpn_key_by_id(key_id)
        if key:
            await state.set_state(AdminStates.key_view)
    else:
        await safe_edit_or_send(message, '❌ Ошибка продления ключа')

@router.callback_query(F.data.startswith('admin_key_reset_traffic:'))
@regular_panel_operation
async def reset_key_traffic(callback: CallbackQuery, state: FSMContext):
    """Reset key traffic."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    if not key.get('server_active'):
        await callback.answer('❌ Сервер неактивен', show_alert=True)
        return
    try:
        # Resetting traffic_used and notification thresholds in the database
        from database.requests import reset_key_traffic_notification
        reset_key_traffic_notification(key_id)
        # Synchronize all key clients with the panel
        from bot.services.vpn_api import sync_key_to_panel_state
        stats = await sync_key_to_panel_state(key_id, reset_traffic=True)
        if not stats.get('ok'):
            await callback.answer('⚠️ БД обновлена, но панель синхронизирована не полностью', show_alert=True)
            return
        await callback.answer('✅ Трафик успешно сброшен!', show_alert=True)
    except VPNAPIError as e:
        logger.error(f'Ошибка сброса трафика: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка при сбросе трафика: {e}')
        await callback.answer('❌ Ошибка при сбросе трафика', show_alert=True)

@router.callback_query(F.data.startswith('admin_key_change_traffic:'))
async def start_change_traffic_limit(callback: CallbackQuery, state: FSMContext):
    """Start of changing the traffic limit."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    if not key.get('server_active'):
        await callback.answer('❌ Сервер неактивен', show_alert=True)
        return
    await state.set_state(AdminStates.key_change_traffic)
    await state.update_data(current_key_id=key_id)
    user_telegram_id = key.get('telegram_id')
    await state.update_data(current_user_telegram_id=user_telegram_id)
    await safe_edit_or_send(callback.message, '📊 <b>Изменение лимита трафика</b>\n\nВведите новый лимит в ГБ (0 = без лимита):', reply_markup=key_action_cancel_kb(key_id, user_telegram_id))
    await callback.answer()

@router.message(AdminStates.key_change_traffic, F.text, ~F.text.startswith('/'))
@regular_panel_operation
async def process_change_traffic_limit(message: Message, state: FSMContext):
    """Processing the entry of a new traffic limit."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit():
        await safe_edit_or_send(message, '❌ Введите число (0 = без лимита)')
        return
    traffic_gb = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    key = get_vpn_key_by_id(key_id)
    if not key:
        await safe_edit_or_send(message, '❌ Ключ не найден')
        return
    try:
        # First we update the limit in the database
        from database.requests import update_key_traffic_limit
        update_key_traffic_limit(key_id, traffic_gb * (1024**3))
        # Synchronize all key clients with the panel
        from bot.services.vpn_api import sync_key_to_panel_state
        stats = await sync_key_to_panel_state(key_id)
        traffic_text = f'{traffic_gb} ГБ' if traffic_gb > 0 else 'без лимита'
        result_text = f'✅ Лимит трафика успешно обновлён: {traffic_text}!'
        if not stats.get('ok'):
            result_text += '\n\n⚠️ БД обновлена, но панель синхронизирована не полностью.'
        await safe_edit_or_send(message, result_text, force_new=True)
        await state.set_state(AdminStates.key_view)
    except VPNAPIError as e:
        logger.error(f'Ошибка обновления лимита трафика: {e}')
        await safe_edit_or_send(message, f'❌ Ошибка: {e}')
    except Exception as e:
        logger.error(f'Неожиданная ошибка при обновлении лимита трафика: {e}')
        await safe_edit_or_send(message, '❌ Ошибка при обновлении лимита трафика')

@router.callback_query(F.data.startswith('admin_user_add_key:'))
async def start_add_key(callback: CallbackQuery, state: FSMContext):
    """Start adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    servers = get_active_servers()
    if not servers:
        await callback.answer('❌ Нет активных серверов', show_alert=True)
        return
    await state.set_state(AdminStates.add_key_server)
    await state.update_data(add_key_user_id=user['id'], add_key_user_telegram_id=telegram_id)
    await safe_edit_or_send(callback.message, f'➕ <b>Добавление ключа для {format_user_display(user)}</b>\n\nВыберите сервер:', reply_markup=add_key_server_kb(servers))
    await callback.answer()

@router.callback_query(F.data.startswith('admin_add_key_server:'))
async def select_add_key_server(callback: CallbackQuery, state: FSMContext):
    """Selecting a server for a new key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    from database.requests import get_server_by_id
    from bot.services.vpn_api import is_subscription_mode
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await state.update_data(add_key_server_id=server_id)

    # Subscription mode: skip the inbound selection - the key is created in all
    if is_subscription_mode():
        try:
            client = get_client_from_server_data(server)
            inbounds = await get_client_subscription_inbounds(client)
            if not inbounds:
                await callback.answer('❌ На сервере нет inbound', show_alert=True)
                return
        except VPNAPIError as e:
            await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
            return
        await state.update_data(add_key_inbound_id=None)
        await state.set_state(AdminStates.add_key_traffic)
        await safe_edit_or_send(callback.message,
            '📊 <b>Лимит трафика</b>\n\nВведите лимит в ГБ (0 = без лимита):',
            reply_markup=add_key_step_kb(2))
        await callback.answer()
        return

    try:
        client = get_client_from_server_data(server)
        inbounds = await client.get_inbounds()
        if not inbounds:
            await callback.answer('❌ На сервере нет inbound', show_alert=True)
            return
        await state.set_state(AdminStates.add_key_inbound)
        await safe_edit_or_send(callback.message, f"🖥️ <b>Сервер:</b> <code>{server['name']}</code>\n\nВыберите протокол (inbound):", reply_markup=add_key_inbound_kb(inbounds))
    except VPNAPIError as e:
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    await callback.answer()

@router.callback_query(F.data.startswith('admin_add_key_inbound:'))
async def select_add_key_inbound(callback: CallbackQuery, state: FSMContext):
    """Selecting inbound for the new key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    inbound_id = int(callback.data.split(':')[1])
    await state.update_data(add_key_inbound_id=inbound_id)
    await state.set_state(AdminStates.add_key_traffic)
    await safe_edit_or_send(callback.message, '📊 <b>Лимит трафика</b>\n\nВведите лимит в ГБ (0 = без лимита):', reply_markup=add_key_step_kb(2))
    await callback.answer()

@router.message(AdminStates.add_key_traffic, F.text, ~F.text.startswith('/'))
async def process_add_key_traffic(message: Message, state: FSMContext):
    """Processing the entry of a traffic limit."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit():
        await safe_edit_or_send(message, '❌ Введите число (0 = без лимита)')
        return
    traffic_gb = int(text)
    await state.update_data(add_key_traffic_gb=traffic_gb)
    await state.set_state(AdminStates.add_key_days)
    await safe_edit_or_send(message, '📅 <b>Срок действия</b>\n\nВведите количество дней:', reply_markup=add_key_step_kb(3), force_new=True)

@router.message(AdminStates.add_key_days, F.text, ~F.text.startswith('/'))
async def process_add_key_days(message: Message, state: FSMContext):
    """Processing expiration date input."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit() or int(text) < 1 or int(text) > 99999:
        await safe_edit_or_send(message, '❌ Введите число от 1 до 99999')
        return
    days = int(text)
    await state.update_data(add_key_days=days)
    await state.set_state(AdminStates.add_key_confirm)
    data = await state.get_data()
    from database.requests import get_server_by_id
    server = get_server_by_id(data['add_key_server_id'])
    traffic_text = f"{data.get('add_key_traffic_gb', 0)} ГБ" if data.get('add_key_traffic_gb', 0) > 0 else 'без лимита'
    await safe_edit_or_send(message, f"✅ <b>Подтверждение создания ключа</b>\n\n🖥️ Сервер: {(server['name'] if server else '?')}\n📊 Трафик: {traffic_text}\n📅 Срок: {days} дней\n", reply_markup=add_key_confirm_kb(), force_new=True)

@router.callback_query(F.data == 'admin_add_key_confirm')
@regular_panel_operation
async def confirm_add_key(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Confirmation and key creation."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_id = data.get('add_key_user_id')
    user_telegram_id = data.get('add_key_user_telegram_id')
    server_id = data.get('add_key_server_id')
    inbound_id = data.get('add_key_inbound_id')
    traffic_gb = data.get('add_key_traffic_gb', 0)
    days = data.get('add_key_days', 30)
    from database.requests import get_server_by_id, get_admin_tariff
    from database.db_keys import create_vpn_key_subscription_admin
    from bot.services.vpn_api import is_subscription_mode
    import uuid as _uuid
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    user = get_user_by_telegram_id(user_telegram_id)
    email = generate_unique_email(user)
    traffic_limit_bytes = (traffic_gb or 0) * 1024 ** 3
    subscription_mode = is_subscription_mode() and inbound_id is None
    try:
        client = get_client_from_server_data(server)
        admin_tariff = get_admin_tariff()
        tariff_id = admin_tariff['id']

        if subscription_mode:
            inbounds = await get_client_subscription_inbounds(client)
            if not inbounds:
                await callback.answer('❌ На сервере нет inbound', show_alert=True)
                return
            sub_id = _uuid.uuid4().hex
            first_inbound_id = None
            first_uuid = None
            created = 0
            for inb in inbounds:
                try:
                    flow = await client.get_inbound_flow(inb['id'])
                    res = await client.add_client(
                        inbound_id=inb['id'], email=email,
                        total_gb=traffic_gb, expire_days=days,
                        limit_ip=admin_tariff.get('max_ips', 1), tg_id=str(user_telegram_id),
                        flow=flow, sub_id=sub_id,
                    )
                    if first_inbound_id is None or inb['id'] < first_inbound_id:
                        first_inbound_id = inb['id']
                        first_uuid = res['uuid']
                    created += 1
                except Exception as e:
                    logger.warning(
                        f"admin_add_key (subscription): не удалось создать клиента "
                        f"в inbound {inb['id']}: {e}"
                    )
            if not first_uuid or first_inbound_id is None or created == 0:
                raise RuntimeError('Не удалось создать ни одного клиента на сервере')
            key_id = create_vpn_key_subscription_admin(
                user_id=user_id, server_id=server_id, tariff_id=tariff_id,
                panel_inbound_id=first_inbound_id, panel_email=email,
                client_uuid=first_uuid, sub_id=sub_id,
                days=days, traffic_limit=traffic_limit_bytes,
            )
            from bot.services.vpn_api import sync_key_to_panel_state
            sync_stats = await sync_key_to_panel_state(key_id)
            if not sync_stats.get('ok'):
                logger.warning(f"admin_add_key: subscription-ключ {key_id} синхронизирован не полностью: {sync_stats}")
        else:
            flow = await client.get_inbound_flow(inbound_id)
            result = await client.add_client(
                inbound_id=inbound_id, email=email, total_gb=traffic_gb,
                expire_days=days, limit_ip=admin_tariff.get('max_ips', 1),
                tg_id=str(user_telegram_id), flow=flow,
            )
            client_uuid = result['uuid']
            key_id = create_vpn_key_admin(
                user_id=user_id, server_id=server_id, tariff_id=tariff_id,
                panel_inbound_id=inbound_id, panel_email=email,
                client_uuid=client_uuid, days=days,
                traffic_limit=traffic_limit_bytes,
            )

        await callback.answer('✅ Ключ успешно создан!', show_alert=True)
        await _show_user_view_edit(callback, state, user_telegram_id)
    except VPNAPIError as e:
        logger.error(f'Ошибка создания ключа: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка: {e}')
        await callback.answer('❌ Ошибка при создании ключа', show_alert=True)

@router.callback_query(F.data == 'admin_user_add_key_cancel')
async def cancel_add_key(callback: CallbackQuery, state: FSMContext):
    """Cancel adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_telegram_id = data.get('add_key_user_telegram_id') or data.get('current_user_telegram_id')
    if user_telegram_id:
        await _show_user_view_edit(callback, state, user_telegram_id)
    else:
        await show_users_menu(callback, state)

@router.callback_query(F.data == 'admin_add_key_back')
async def add_key_back(callback: CallbackQuery, state: FSMContext):
    """Step back when adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    from bot.services.vpn_api import is_subscription_mode
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == AdminStates.add_key_inbound.state:
        servers = get_active_servers()
        await state.set_state(AdminStates.add_key_server)
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await safe_edit_or_send(callback.message, f"➕ *Добавление ключа для {(format_user_display(user) if user else '?')}*\n\nВыберите сервер:", reply_markup=add_key_server_kb(servers))
    elif (current_state == AdminStates.add_key_traffic.state
          and is_subscription_mode()
          and data.get('add_key_inbound_id') is None):
        # There is no inbound step in subscription - we go straight back to choosing a server
        servers = get_active_servers()
        await state.set_state(AdminStates.add_key_server)
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await safe_edit_or_send(callback.message, f"➕ <b>Добавление ключа для {(format_user_display(user) if user else '?')}</b>\n\nВыберите сервер:", reply_markup=add_key_server_kb(servers))
    else:
        await cancel_add_key(callback, state)

def _manual_sync_plan_text(plan, *, preview: bool) -> str:
    direction_title = (
        'БД → Панель'
        if plan.direction == 'db_to_panel'
        else 'Панель → БД'
    )
    heading = (
        '🔎 <b>Предпросмотр синхронизации</b>'
        if preview
        else '✅ <b>Синхронизация завершена</b>'
    )
    lines = [heading, '', f'Направление: <b>{direction_title}</b>', '']

    if not plan.reports:
        lines.append('✅ Нет ключей для проверки.')
    for report in plan.reports:
        server_name = escape_html(report.server_name)
        if report.error:
            lines.append(
                f'❌ <b>{server_name}</b>: '
                f'{escape_html(str(report.error)[:180])}'
            )
            continue

        if plan.direction == 'db_to_panel':
            stats = report.stats
            action_word = 'изменится' if preview else 'применено'
            lines.append(
                f'🖥 <b>{server_name}</b>: проверено {report.checked}, '
                f'{action_word} {report.changed}, пропущено {report.skipped}'
            )
            details = []
            if stats.get('created'):
                details.append(f"создать/подключить {stats['created']}")
            if stats.get('updated'):
                details.append(f"обновить {stats['updated']}")
            if stats.get('deleted'):
                details.append(f"отключить {stats['deleted']}")
            if stats.get('enabled'):
                details.append(f"включить {stats['enabled']}")
            if stats.get('disabled'):
                details.append(f"выключить {stats['disabled']}")
            if stats.get('reset'):
                details.append(f"сбросить трафик {stats['reset']}")
            if details:
                lines.append('  • ' + ', '.join(details))
            if stats.get('errors'):
                lines.append(f"  • ошибок: {stats['errors']}")
        else:
            stats = report.stats
            applied = stats.get('applied')
            action_word = (
                f'применено {applied}'
                if applied is not None
                else f'изменится {report.changed}'
            )
            lines.append(
                f'🖥 <b>{server_name}</b>: проверено {report.checked}, '
                f'{action_word}, пропущено {report.skipped}'
            )
            details = []
            if stats.get('expiry'):
                details.append(f"срок {stats['expiry']}")
            if stats.get('traffic'):
                details.append(f"трафик {stats['traffic']}")
            if stats.get('revived'):
                details.append(f"восстановить истёкших {stats['revived']}")
            if details:
                lines.append('  • ' + ', '.join(details))

    lines.extend([
        '',
        f'🔑 Ключей с изменениями: <b>{len(plan.candidate_key_ids)}</b>',
    ])
    if plan.errors:
        lines.append(f'❌ Ошибок: <b>{plan.errors}</b>')
    if preview and plan.has_changes:
        lines.extend([
            '',
            'Запись ещё не выполнялась. После подтверждения данные '
            'будут скачаны и проверены повторно.',
        ])
    elif not plan.has_changes:
        lines.extend(['', '✅ Всё уже актуально, применять нечего.'])
    return '\n'.join(lines)


async def _manual_sync_keys(direction: str):
    from database.requests import (
        get_all_active_keys_with_server,
        get_all_panel_sync_keys,
    )

    if direction == 'db_to_panel':
        return get_all_active_keys_with_server()
    return get_all_panel_sync_keys()


async def _show_manual_sync_preview(
    callback: CallbackQuery,
    state: FSMContext,
    direction: str,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    from database.requests import get_all_servers
    from bot.keyboards.admin import manual_sync_preview_kb
    from bot.services.panel_sync import (
        build_panel_to_db_plan,
        run_db_to_panel_sync,
    )
    from bot.services.panel_sync_coordinator import panel_sync_coordinator

    if panel_sync_coordinator.manual_pending:
        await callback.answer(
            '⏳ Другая ручная синхронизация уже выполняется',
            show_alert=True,
        )
        return

    await callback.answer('🔎 Составляю предпросмотр…')
    await safe_edit_or_send(
        callback.message,
        '⏳ <b>Проверяю БД и панели…</b>\n\n'
        'На этом этапе ничего не изменяется.',
    )

    keys = await _manual_sync_keys(direction)
    servers = get_all_servers()
    if direction == 'db_to_panel':
        plan = await run_db_to_panel_sync(keys, servers, apply=False)
    else:
        plan = await build_panel_to_db_plan(keys, servers)

    token = uuid.uuid4().hex[:12]
    preview_data = {
        'token': token,
        'direction': direction,
        'candidate_key_ids': list(plan.candidate_key_ids),
        'server_ids': list(plan.successful_server_ids),
    }
    await state.update_data(manual_sync_preview=preview_data)

    markup = (
        manual_sync_preview_kb(direction, token)
        if plan.has_changes
        else back_and_home_kb('admin_users')
    )
    await safe_edit_or_send(
        callback.message,
        _manual_sync_plan_text(plan, preview=True),
        reply_markup=markup,
    )


@router.callback_query(F.data == 'admin_sync_db_to_panel')
async def sync_db_to_panel(callback: CallbackQuery, state: FSMContext):
    """Build a read-only DB -> Panel synchronization preview."""
    await _show_manual_sync_preview(callback, state, 'db_to_panel')


@router.callback_query(F.data == 'admin_sync_panel_to_db')
async def sync_panel_to_db(callback: CallbackQuery, state: FSMContext):
    """Build a read-only Panel -> DB synchronization preview."""
    await _show_manual_sync_preview(callback, state, 'panel_to_db')


@router.callback_query(F.data.startswith('admin_sync_cancel:'))
async def cancel_manual_sync(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    token = callback.data.split(':', 1)[1]
    data = await state.get_data()
    preview = data.get('manual_sync_preview') or {}
    if preview.get('token') == token:
        data.pop('manual_sync_preview', None)
        await state.set_data(data)
    await callback.answer('Синхронизация отменена')
    await safe_edit_or_send(
        callback.message,
        '❌ <b>Ручная синхронизация отменена</b>\n\n'
        'Никакие данные не изменялись.',
        reply_markup=back_and_home_kb('admin_users'),
    )


@router.callback_query(F.data.startswith('admin_sync_apply:'))
async def apply_manual_sync(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    parts = callback.data.split(':', 2)
    if len(parts) != 3:
        await callback.answer('Предпросмотр повреждён', show_alert=True)
        return
    _, direction, token = parts
    data = await state.get_data()
    preview = data.get('manual_sync_preview') or {}
    if (
        preview.get('token') != token
        or preview.get('direction') != direction
        or direction not in {'db_to_panel', 'panel_to_db'}
    ):
        await callback.answer(
            'Предпросмотр устарел. Выполните проверку заново.',
            show_alert=True,
        )
        return

    from database.requests import get_all_servers
    from bot.keyboards.admin import manual_sync_preview_kb
    from bot.services.panel_sync import (
        apply_panel_to_db_plan,
        build_panel_to_db_plan,
        run_db_to_panel_sync,
    )
    from bot.services.panel_sync_coordinator import panel_sync_coordinator

    await callback.answer('⏳ Применяю изменения…')
    await safe_edit_or_send(
        callback.message,
        '⏳ <b>Применяю ручную синхронизацию…</b>\n\n'
        'Новые изменения VPN-ключей временно ожидают завершения.',
    )

    try:
        async with panel_sync_coordinator.try_manual() as acquired:
            if not acquired:
                await safe_edit_or_send(
                    callback.message,
                    '⏳ <b>Уже выполняется другая ручная синхронизация</b>\n\n'
                    'Этот предпросмотр сохранён — повторите применение после её завершения.',
                    reply_markup=manual_sync_preview_kb(direction, token),
                )
                return

            keys = await _manual_sync_keys(direction)
            servers = get_all_servers()
            candidate_ids = preview.get('candidate_key_ids') or []
            server_ids = preview.get('server_ids') or []

            if direction == 'db_to_panel':
                plan = await run_db_to_panel_sync(
                    keys,
                    servers,
                    apply=True,
                    candidate_key_ids=candidate_ids,
                    allowed_server_ids=server_ids,
                )
            else:
                plan = await build_panel_to_db_plan(
                    keys,
                    servers,
                    candidate_key_ids=candidate_ids,
                    allowed_server_ids=server_ids,
                )
                plan = await apply_panel_to_db_plan(plan)
    except Exception as exc:
        logger.exception('Manual synchronization failed')
        await safe_edit_or_send(
            callback.message,
            '❌ <b>Не удалось завершить синхронизацию</b>\n\n'
            f'{escape_html(str(exc)[:500])}',
            reply_markup=manual_sync_preview_kb(direction, token),
        )
        return

    data = await state.get_data()
    current_preview = data.get('manual_sync_preview') or {}
    if current_preview.get('token') == token:
        data.pop('manual_sync_preview', None)
        await state.set_data(data)

    await safe_edit_or_send(
        callback.message,
        _manual_sync_plan_text(plan, preview=False),
        reply_markup=back_and_home_kb('admin_users'),
    )
