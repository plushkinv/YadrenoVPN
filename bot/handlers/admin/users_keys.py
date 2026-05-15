import logging
import uuid
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, extend_vpn_key, create_vpn_key_admin, get_active_servers, get_all_tariffs, get_user_balance, get_user_referral_coefficient, add_to_balance, deduct_from_balance, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, safe_edit_or_send
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic
from bot.handlers.admin.users_manage import format_user_display, _show_user_view_edit
from bot.handlers.admin.users_list import show_users_menu

logger = logging.getLogger(__name__)
from bot.utils.text import safe_edit_or_send

router = Router()
USERS_PER_PAGE = 20

def generate_unique_email(user: dict) -> str:
    """
    Генерирует уникальный email для панели 3X-UI.
    Формат: user_{username/id}_{random_suffix}
    """
    base = f"user_{user['username']}" if user.get('username') else f"user_{user['telegram_id']}"
    suffix = uuid.uuid4().hex[:5]
    return f'{base}_{suffix}'

@router.callback_query(F.data.startswith('admin_key_view:'))
async def show_key_view(callback: CallbackQuery, state: FSMContext):
    """Показывает экран управления ключом."""
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
    expires_at = key.get('expires_at', '?')
    created_at = key.get('created_at', '?')
    text = f'🔑 <b>{key_name}</b>\n\n🖥️ Сервер: {server_name}\n📋 Тариф: {tariff_name}\n📅 Создан: {created_at}\n⏰ Истекает: {expires_at}\n'
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
        text += '\n💳 <b>История платежей:</b>\n'
        for p in payments_history:
            dt = p['paid_at']
            amount = ''
            if p['payment_type'] == 'crypto':
                usd = p['amount_cents'] / 100
                usd_str = f'{usd:g}'.replace('.', ',')
                amount = f'${usd_str}'
            elif p['payment_type'] == 'stars':
                amount = f"{p['amount_stars']} ⭐"
            elif p.get('payment_type') == 'cards':
                rub = p.get('price_rub') or 0
                rub_str = f'{rub:g}'.replace('.', ',')
                amount = f'{rub_str} ₽'
            else:
                amount = '?'
            tariff_safe = escape_html(p['tariff_name'] or 'Неизвестно')
            text += f'• <code>{dt}</code>: {amount} — {tariff_safe}\n'
    else:
        text += '\n💳 <b>История платежей:</b> _пусто_\n'
    user_telegram_id = key.get('telegram_id')
    await safe_edit_or_send(callback.message, text, reply_markup=key_view_kb(key_id, user_telegram_id))
    await callback.answer()

@router.callback_query(F.data.startswith('admin_key_extend:'))
async def start_key_extend(callback: CallbackQuery, state: FSMContext):
    """Начало продления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    await state.set_state(AdminStates.key_extend_days)
    await state.update_data(current_key_id=key_id)
    await safe_edit_or_send(callback.message, '📅 <b>Продление ключа</b>\n\nВведите количество дней для продления:', reply_markup=key_action_cancel_kb(key_id, 0))
    await callback.answer()

@router.message(AdminStates.key_extend_days, F.text, ~F.text.startswith('/'))
async def process_key_extend(message: Message, state: FSMContext):
    """Обработка ввода дней для продления."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit() or int(text) < 1 or int(text) > 99999:
        await safe_edit_or_send(message, '❌ Введите число от 1 до 99999')
        return
    days = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    success = extend_vpn_key(key_id, days)
    if success:
        await safe_edit_or_send(message, f'✅ Ключ продлён на {days} дней!', force_new=True)
        from bot.services.vpn_api import push_key_to_panel, restore_traffic_limit_in_db
        # Восстанавливаем лимит трафика в БД
        restore_traffic_limit_in_db(key_id)
        # Пушим ВСЕ данные из БД на панель (сброс up/down + обновление)
        await push_key_to_panel(key_id, reset_traffic=True)
        key = get_vpn_key_by_id(key_id)
        if key:
            await state.set_state(AdminStates.key_view)
    else:
        await safe_edit_or_send(message, '❌ Ошибка продления ключа')

@router.callback_query(F.data.startswith('admin_key_reset_traffic:'))
async def reset_key_traffic(callback: CallbackQuery, state: FSMContext):
    """Сброс трафика ключа."""
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
        # Обнуляем traffic_used и пороги уведомлений в БД
        from database.requests import reset_key_traffic_notification
        reset_key_traffic_notification(key_id)
        # Пушим данные из БД на панель (сброс up/down + правильные expiryTime и totalGB)
        from bot.services.vpn_api import push_key_to_panel
        await push_key_to_panel(key_id, reset_traffic=True)
        await callback.answer('✅ Трафик успешно сброшен!', show_alert=True)
    except VPNAPIError as e:
        logger.error(f'Ошибка сброса трафика: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка при сбросе трафика: {e}')
        await callback.answer('❌ Ошибка при сбросе трафика', show_alert=True)

@router.callback_query(F.data.startswith('admin_key_change_traffic:'))
async def start_change_traffic_limit(callback: CallbackQuery, state: FSMContext):
    """Начало изменения лимита трафика."""
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
async def process_change_traffic_limit(message: Message, state: FSMContext):
    """Обработка ввода нового лимита трафика."""
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
        # Сначала обновляем лимит в БД
        from database.requests import update_key_traffic_limit
        update_key_traffic_limit(key_id, traffic_gb * (1024**3))
        # Пушим данные из БД на панель
        from bot.services.vpn_api import push_key_to_panel
        await push_key_to_panel(key_id)
        traffic_text = f'{traffic_gb} ГБ' if traffic_gb > 0 else 'без лимита'
        await safe_edit_or_send(message, f'✅ Лимит трафика успешно обновлён: {traffic_text}!', force_new=True)
        await state.set_state(AdminStates.key_view)
    except VPNAPIError as e:
        logger.error(f'Ошибка обновления лимита трафика: {e}')
        await safe_edit_or_send(message, f'❌ Ошибка: {e}')
    except Exception as e:
        logger.error(f'Неожиданная ошибка при обновлении лимита трафика: {e}')
        await safe_edit_or_send(message, '❌ Ошибка при обновлении лимита трафика')

@router.callback_query(F.data.startswith('admin_user_add_key:'))
async def start_add_key(callback: CallbackQuery, state: FSMContext):
    """Начало добавления ключа."""
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
    """Выбор сервера для нового ключа."""
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

    # Subscription mode: пропускаем выбор inbound — ключ создаётся во всех
    if is_subscription_mode():
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()
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
    """Выбор inbound для нового ключа."""
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
    """Обработка ввода лимита трафика."""
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
    """Обработка ввода срока действия."""
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
async def confirm_add_key(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и создание ключа."""
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
            inbounds = await client.get_inbounds()
            if not inbounds:
                await callback.answer('❌ На сервере нет inbound', show_alert=True)
                return
            sub_id = _uuid.uuid4().hex
            min_inbound_id = min(inb['id'] for inb in inbounds)
            first_uuid = None
            created = 0
            for inb in inbounds:
                try:
                    flow = await client.get_inbound_flow(inb['id'])
                    res = await client.add_client(
                        inbound_id=inb['id'], email=email,
                        total_gb=traffic_gb, expire_days=days,
                        limit_ip=1, tg_id=str(user_telegram_id),
                        flow=flow, sub_id=sub_id,
                    )
                    if inb['id'] == min_inbound_id:
                        first_uuid = res['uuid']
                    created += 1
                except Exception as e:
                    logger.warning(
                        f"admin_add_key (subscription): не удалось создать клиента "
                        f"в inbound {inb['id']}: {e}"
                    )
            if not first_uuid or created == 0:
                raise RuntimeError('Не удалось создать ни одного клиента на сервере')
            key_id = create_vpn_key_subscription_admin(
                user_id=user_id, server_id=server_id, tariff_id=tariff_id,
                panel_inbound_id=min_inbound_id, panel_email=email,
                client_uuid=first_uuid, sub_id=sub_id,
                days=days, traffic_limit=traffic_limit_bytes,
            )
        else:
            flow = await client.get_inbound_flow(inbound_id)
            result = await client.add_client(
                inbound_id=inbound_id, email=email, total_gb=traffic_gb,
                expire_days=days, limit_ip=1,
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
    """Отмена добавления ключа."""
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
    """Шаг назад при добавлении ключа."""
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
        # В subscription шага inbound нет — возвращаемся сразу на выбор сервера
        servers = get_active_servers()
        await state.set_state(AdminStates.add_key_server)
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await safe_edit_or_send(callback.message, f"➕ <b>Добавление ключа для {(format_user_display(user) if user else '?')}</b>\n\nВыберите сервер:", reply_markup=add_key_server_kb(servers))
    else:
        await cancel_add_key(callback, state)

@router.callback_query(F.data == 'admin_sync_db_to_panel')
async def sync_db_to_panel(callback: CallbackQuery, state: FSMContext):
    """Выгрузка данных из БД в панель (БД → Панель)."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    
    await callback.answer('📤 Запуск выгрузки...')
    await safe_edit_or_send(callback.message, '⏳ <b>Выгрузка данных в панель (БД → Панель)...</b>\n\nЭто может занять некоторое время.')
    
    import json
    from database.requests import get_all_active_keys_with_server, get_all_servers
    from bot.services.vpn_api import push_key_to_panel, get_client_from_server_data
    from datetime import datetime
    
    keys = get_all_active_keys_with_server()
    if not keys:
        await safe_edit_or_send(callback.message, '✅ Нет активных ключей для синхронизации.')
        return
    
    # Группируем по серверам
    keys_by_server = {}
    for key in keys:
        sid = key['server_id']
        if sid not in keys_by_server:
            keys_by_server[sid] = []
        keys_by_server[sid].append(key)
    
    servers = get_all_servers()
    server_map = {s['id']: s for s in servers}
    
    fixed = 0
    errors = 0
    ok = 0
    
    for server_id, server_keys in keys_by_server.items():
        server = server_map.get(server_id)
        if not server or not server.get('is_active'):
            continue
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()
            
            panel_map = {}
            for inbound in inbounds:
                settings = json.loads(inbound.get('settings', '{}'))
                for cl in settings.get('clients', []):
                    panel_map[cl.get('email', '')] = {
                        'expiryTime': cl.get('expiryTime', 0),
                        'totalGB': cl.get('totalGB', 0)
                    }
            
            for key in server_keys:
                email = key.get('panel_email')
                if not email or email not in panel_map:
                    continue
                
                panel = panel_map[email]
                needs_fix = False
                
                # Проверяем expiryTime
                expires_at = key.get('expires_at')
                expected_ms = 0
                if expires_at:
                    from datetime import timezone
                    dt_str = str(expires_at).replace('Z', '+00:00')
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    now_utc = datetime.now(timezone.utc)
                    if dt > now_utc + timedelta(days=90000):
                        expected_ms = 0
                    else:
                        expected_ms = int(dt.timestamp() * 1000)

                panel_ms = panel['expiryTime']
                if expected_ms == 0 and panel_ms != 0:
                    needs_fix = True
                elif expected_ms > 0 and panel_ms == 0:
                    needs_fix = True
                elif expected_ms > 0 and panel_ms > 0 and abs(expected_ms - panel_ms) > 86400 * 1000:
                    needs_fix = True
                
                # Проверяем totalGB
                traffic_limit = key.get('traffic_limit', 0) or 0
                panel_total = panel['totalGB']
                if traffic_limit > 0 and (panel_total == 0 or abs(panel_total - traffic_limit) > 1024**3):
                    needs_fix = True
                elif traffic_limit == 0 and panel_total > 0:
                    needs_fix = True
                
                if needs_fix:
                    try:
                        await push_key_to_panel(key['id'])
                        fixed += 1
                    except Exception as e:
                        errors += 1
                        logger.error(f"Ошибка синхронизации ключа {key['id']} ({email}): {e}")
                else:
                    ok += 1
        except Exception as e:
            errors += len(server_keys)
            logger.error(f"Ошибка подключения к серверу {server.get('name', server_id)}: {e}")
    
    result = (
        f"✅ <b>Выгрузка в панель завершена</b>\n\n"
        f"📤 Отправлено: <b>{fixed}</b>\n"
        f"✅ Без расхождений: <b>{ok}</b>\n"
    )
    if errors > 0:
        result += f"❌ Ошибок: <b>{errors}</b>\n"
    result += f"\n📊 Всего ключей: <b>{len(keys)}</b>"
    
    await safe_edit_or_send(callback.message, result, reply_markup=back_and_home_kb('admin_users'))

    await callback.answer()


@router.callback_query(F.data == 'admin_sync_panel_to_db')
async def sync_panel_to_db(callback: CallbackQuery, state: FSMContext):
    """Загрузка данных из панели в БД (Панель → БД)."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    
    await callback.answer('📥 Запуск загрузки...')
    await safe_edit_or_send(callback.message, '⏳ <b>Загрузка данных из панели (Панель → БД)...</b>\n\nЭто может занять некоторое время.')
    
    import json
    from database.requests import get_all_active_keys_with_server, get_all_servers
    from database.db_keys import update_key_traffic_limit, update_key_traffic
    from datetime import datetime
    
    keys = get_all_active_keys_with_server()
    if not keys:
        await safe_edit_or_send(callback.message, '✅ Нет активных ключей для загрузки.', reply_markup=back_and_home_kb('admin_users'))
        return
    
    # Группируем по серверам
    keys_by_server = {}
    for key in keys:
        sid = key['server_id']
        if sid not in keys_by_server:
            keys_by_server[sid] = []
        keys_by_server[sid].append(key)
    
    servers = get_all_servers()
    server_map = {s['id']: s for s in servers}
    
    updated = 0
    errors = 0
    skipped = 0
    
    for server_id, server_keys in keys_by_server.items():
        server = server_map.get(server_id)
        if not server or not server.get('is_active'):
            continue
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()
            
            # Собираем данные из панели: email → {expiryTime, totalGB, up, down}
            panel_map = {}
            for inbound in inbounds:
                settings = json.loads(inbound.get('settings', '{}'))
                # Собираем трафик из clientStats
                client_stats = {}
                for stat in inbound.get('clientStats', []):
                    client_stats[stat.get('email', '')] = {
                        'up': stat.get('up', 0),
                        'down': stat.get('down', 0)
                    }
                
                for cl in settings.get('clients', []):
                    email = cl.get('email', '')
                    stats = client_stats.get(email, {'up': 0, 'down': 0})
                    panel_map[email] = {
                        'expiryTime': cl.get('expiryTime', 0),
                        'totalGB': cl.get('totalGB', 0),
                        'traffic_used': stats['up'] + stats['down']
                    }
            
            for key in server_keys:
                email = key.get('panel_email')
                if not email or email not in panel_map:
                    skipped += 1
                    continue
                
                panel = panel_map[email]
                changed = False
                
                try:
                    from datetime import timezone, timedelta
                    # Обновляем expires_at из панели
                    panel_ms = panel['expiryTime']
                    max_expires = datetime.now(timezone.utc) + timedelta(days=99999)
                    
                    if panel_ms == 0:
                        # Бесконечный ключ на панели → ставим максимум
                        panel_dt = max_expires
                    else:
                        panel_dt = datetime.fromtimestamp(panel_ms / 1000, tz=timezone.utc)
                        # Ограничиваем слишком далёкие даты
                        if panel_dt > max_expires:
                            panel_dt = max_expires
                    
                    # Для БД SQLite используем наивную строку (которая подразумевается в UTC)
                    panel_expires_str = panel_dt.replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')
                    
                    db_expires = key.get('expires_at')
                    need_update = False
                    if db_expires:
                        db_dt_str = str(db_expires).replace('Z', '+00:00')
                        db_dt = datetime.fromisoformat(db_dt_str)
                        if db_dt.tzinfo is None:
                            db_dt = db_dt.replace(tzinfo=timezone.utc)
                            
                        # Обновляем если разница больше суток
                        if abs((panel_dt - db_dt).total_seconds()) > 86400:
                            need_update = True
                    else:
                        need_update = True
                        
                    if need_update:
                        from database.connection import get_db
                        with get_db() as conn:
                            conn.execute(
                                "UPDATE vpn_keys SET expires_at = ? WHERE id = ?",
                                (panel_expires_str, key['id'])
                            )
                        changed = True
                    
                    # Обновляем traffic_limit из панели
                    panel_total_bytes = panel['totalGB']
                    db_limit = key.get('traffic_limit', 0) or 0
                    if panel_total_bytes != db_limit:
                        update_key_traffic_limit(key['id'], panel_total_bytes)
                        changed = True
                    
                    # Обновляем traffic_used из панели
                    panel_traffic = panel['traffic_used']
                    db_traffic = key.get('traffic_used', 0) or 0
                    if panel_traffic != db_traffic:
                        update_key_traffic(key['id'], panel_traffic)
                        changed = True
                    
                    if changed:
                        updated += 1
                    else:
                        skipped += 1
                        
                except Exception as e:
                    errors += 1
                    logger.error(f"Ошибка обновления ключа {key['id']} ({email}): {e}")
                    
        except Exception as e:
            errors += len(server_keys)
            logger.error(f"Ошибка подключения к серверу {server.get('name', server_id)}: {e}")
    
    result = (
        f"✅ <b>Загрузка из панели завершена</b>\n\n"
        f"📥 Обновлено: <b>{updated}</b>\n"
        f"✅ Без расхождений: <b>{skipped}</b>\n"
    )
    if errors > 0:
        result += f"❌ Ошибок: <b>{errors}</b>\n"
    result += f"\n📊 Всего ключей: <b>{len(keys)}</b>"
    
    await safe_edit_or_send(callback.message, result, reply_markup=back_and_home_kb('admin_users'))
    await callback.answer()