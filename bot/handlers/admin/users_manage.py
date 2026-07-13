import logging
import uuid
from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, extend_vpn_key, create_vpn_key_admin, get_active_servers, get_all_tariffs, get_user_balance, get_user_referral_coefficient, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.datetime_format import format_datetime_for_display
from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.panel_email import get_panel_email_prefix
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.key_lifecycle import sync_user_keys_panel_access
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic

logger = logging.getLogger(__name__)
from bot.utils.text import safe_edit_or_send

router = Router()
USERS_PER_PAGE = 20

def format_user_display(user: dict) -> str:
    """Formats the username for display."""
    if user.get('username'):
        return f"@{user['username']}"
    return f"ID: {user['telegram_id']}"

@router.callback_query(F.data.startswith('admin_user_view:'))
async def show_user_view_callback(callback: CallbackQuery, state: FSMContext):
    """Shows the user card (from callback)."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    await _show_user_view_edit(callback, state, telegram_id)

async def _show_user_view(message: Message, state: FSMContext, telegram_id: int):
    """Shows the user card (new message)."""
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await safe_edit_or_send(message, f'❌ Пользователь с ID {telegram_id} не найден', reply_markup=home_only_kb(), force_new=True)
        return
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=telegram_id)
    (text, keyboard) = _format_user_card(user)
    await safe_edit_or_send(message, text, reply_markup=keyboard, force_new=True)

async def _show_user_view_edit(callback: CallbackQuery, state: FSMContext, telegram_id: int):
    """Shows the user card (editing a message)."""
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=telegram_id)
    (text, keyboard) = _format_user_card(user)
    await safe_edit_or_send(callback.message, text, reply_markup=keyboard)
    await callback.answer()

def _format_user_card(user: dict) -> tuple[str, any]:
    """Formats a user card."""
    telegram_id = user['telegram_id']
    username = user.get('username')
    is_banned = bool(user.get('is_banned'))
    is_bot_blocked = bool(user.get('is_bot_blocked'))
    created_at = format_datetime_for_display(user.get('created_at'), fallback='неизвестно')
    balance_cents = get_user_balance(user['id'])
    referral_coefficient = get_user_referral_coefficient(user['id'])
    vpn_keys = get_user_vpn_keys(user['id'])
    
    lines = []
    if is_banned:
        lines.append('🚫 <b>ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН</b>')
        lines.append('')
        
    if username:
        lines.append(f'👤 Username: @{escape_html(username)}')
    else:
        lines.append('👤 Username: _не указан_')
        
    lines.append(f'📱 Telegram ID: <code>{telegram_id}</code>')
    if is_bot_blocked:
        lines.append('📵 Статус доставки: <b>бот заблокирован пользователем</b>')
    else:
        lines.append('📨 Статус доставки: <b>доступен для сообщений</b>')
    
    panel_email_prefix = get_panel_email_prefix(user)
    lines.append(f'📧 E-mail в панели: <code>{escape_html(panel_email_prefix)}</code>')
    lines.append(f'📅 Зарегистрирован: {created_at}')
    
    balance_rub = balance_cents / 100
    lines.append(f'💰 Баланс: <b>{balance_rub:.2f} ₽</b>')
    lines.append(f'📊 Реферальный коэффициент: <b>{referral_coefficient}x</b>')
    lines.append('')
    if vpn_keys:
        lines.append(f'🔑 <b>VPN-ключи ({len(vpn_keys)}):</b>')
        for key in vpn_keys:
            if key.get('custom_name'):
                key_name = key['custom_name']
            else:
                uuid = key.get('client_uuid') or ''
                if len(uuid) >= 8:
                    key_name = f'{uuid[:4]}...{uuid[-4:]}'
                else:
                    key_name = uuid or f"Ключ #{key['id']}"
            raw_expires = key.get('expires_at')
            try:
                expires_dt = datetime.fromisoformat(str(raw_expires).replace('Z', '+00:00'))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                if expires_dt < datetime.now(timezone.utc):
                    status = '🔴'
                else:
                    status = '🟢'
            except:
                status = '🔑'
            expires = format_datetime_for_display(raw_expires, fallback='?')
            lines.append(f'  {status} <code>{key_name}</code> (до {expires})')
    else:
        lines.append('🔑 _VPN-ключей нет_')
    payment_stats = get_user_payments_stats(user['id'])
    lines.append('')
    lines.append('💳 <b>Оплаты:</b>')
    total_payments = payment_stats.get('total_payments', 0)
    if total_payments > 0:
        total_usd = payment_stats.get('total_amount_cents', 0) / 100
        total_stars = payment_stats.get('total_amount_stars', 0)
        total_rub = payment_stats.get('total_amount_rub', 0)
        last_payment = format_datetime_for_display(payment_stats.get('last_payment_at'), fallback='?')
        lines.append(f'  📊 Всего платежей: {total_payments}')
        if total_usd > 0:
            total_usd_str = f'{total_usd:g}'.replace('.', ',')
            lines.append(f'  💰 Сумма (крипто): ${total_usd_str}')
        if total_stars > 0:
            lines.append(f'  ⭐ Сумма (Stars): {total_stars}')
        if total_rub > 0:
            total_rub_str = f'{total_rub:g}'.replace('.', ',')
            lines.append(f'  💳 Сумма (Рубли): {total_rub_str} ₽')
        lines.append(f'  📅 Последняя оплата: {last_payment}')
    else:
        lines.append('  _Оплат не было_')
    text = '\n'.join(lines)
    keyboard = user_view_kb(telegram_id, vpn_keys, is_banned, balance_cents, referral_coefficient)
    return (text, keyboard)

@router.callback_query(F.data.startswith('admin_user_toggle_ban:'))
async def request_ban_confirmation(callback: CallbackQuery, state: FSMContext):
    """Request confirmation of ban/unban."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    is_banned = bool(user.get('is_banned'))
    if is_banned:
        action = 'разблокировать'
    else:
        action = 'заблокировать'
    text = f'⚠️ <b>Подтверждение</b>\n\nВы уверены, что хотите <b>{action}</b> пользователя <code>{format_user_display(user)}</code>?'
    await safe_edit_or_send(callback.message, text, reply_markup=user_ban_confirm_kb(telegram_id, is_banned))
    await callback.answer()

@router.callback_query(F.data.startswith('admin_user_ban_confirm:'))
async def confirm_ban_toggle(callback: CallbackQuery, state: FSMContext):
    """Confirmation and execution of ban/unban."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    new_status = toggle_user_ban(telegram_id)
    if new_status is None:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    action_text = 'заблокирован' if new_status else 'разблокирован'
    icon = '🚫' if new_status else '✅'
    try:
        sync_result = await sync_user_keys_panel_access(telegram_id)
        keys_total = int(sync_result.get('keys_total', 0) or 0)
        synced = int(sync_result.get('synced', 0) or 0)
        errors = int(sync_result.get('errors', 0) or 0)
        if keys_total == 0:
            panel_status = 'ключей нет'
        elif errors:
            panel_status = f'панель: {synced}/{keys_total}, ошибок: {errors}'
        else:
            panel_status = 'панель синхронизирована'
    except Exception as e:
        logger.warning(f"Не удалось синхронизировать панель после бана пользователя {telegram_id}: {e}")
        panel_status = 'панель: ошибка синхронизации'

    await callback.answer(f'{icon} Пользователь {action_text}. {panel_status}', show_alert=True)
    await _show_user_view_edit(callback, state, telegram_id)

@router.callback_query(F.data.startswith('admin_user_coefficient:'))
async def start_coefficient_edit(callback: CallbackQuery, state: FSMContext):
    """Start editing the coefficient."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    current_coefficient = get_user_referral_coefficient(user['id'])
    await state.set_state(AdminStates.waiting_coefficient)
    await state.update_data(coefficient_user_telegram_id=telegram_id, coefficient_edit_message_id=callback.message.message_id)
    await safe_edit_or_send(callback.message, f'📊 <b>Редактирование реферального коэффициента</b>\n\n👤 {format_user_display(user)}\n📱 ID: <code>{telegram_id}</code>\n\nТекущий реферальный коэффициент: <b>{current_coefficient}x</b>\n\nВведите новый реферальный коэффициент (0.0 - 10.0):', reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
    await callback.answer()

@router.message(AdminStates.waiting_coefficient, F.text, ~F.text.startswith('/'))
async def process_coefficient_input(message: Message, state: FSMContext):
    """Processing coefficient input."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').replace(',', '.')
    try:
        coefficient = float(text)
        if not 0.0 <= coefficient <= 10.0:
            raise ValueError()
    except ValueError:
        await message.delete()
        return
    data = await state.get_data()
    telegram_id = data.get('coefficient_user_telegram_id')
    edit_message_id = data.get('coefficient_edit_message_id')
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await message.delete()
        return
    set_user_referral_coefficient(user['id'], coefficient)
    await message.delete()
    if edit_message_id:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=edit_message_id, text=f'📊 <b>Реферальный коэффициент обновлён</b>\n\n👤 {format_user_display(user)}\n📱 ID: <code>{telegram_id}</code>\n\nНовый реферальный коэффициент: <b>{coefficient}x</b>', reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'), parse_mode='HTML')
        except Exception:
            pass
    await state.clear()

@router.callback_query(F.data.regexp('^admin_user_balance_add:(\\d+)$'))
async def start_balance_add(callback: CallbackQuery, state: FSMContext):
    """Start of replenishing the user's balance."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    current_balance = get_user_balance(user['id'])
    balance_rub = current_balance / 100
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(balance_user_telegram_id=telegram_id, balance_operation='add')
    await safe_edit_or_send(callback.message, f'💰 <b>Пополнение баланса</b>\n\n👤 {format_user_display(user)}\n📱 ID: <code>{telegram_id}</code>\n💼 Текущий баланс: <b>{balance_rub:.2f} ₽</b>\n\nВведите сумму пополнения в рублях (например: 100 или 50.5):', reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
    await callback.answer()

@router.callback_query(F.data.regexp('^admin_user_balance_deduct:(\\d+)$'))
async def start_balance_deduct(callback: CallbackQuery, state: FSMContext):
    """Start of debiting the user's balance."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    current_balance = get_user_balance(user['id'])
    balance_rub = current_balance / 100
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(balance_user_telegram_id=telegram_id, balance_operation='deduct')
    await safe_edit_or_send(callback.message, f'💸 <b>Списание баланса</b>\n\n👤 {format_user_display(user)}\n📱 ID: <code>{telegram_id}</code>\n💼 Текущий баланс: <b>{balance_rub:.2f} ₽</b>\n\nВведите сумму списания в рублях (например: 100 или 50.5):', reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
    await callback.answer()

@router.message(AdminStates.waiting_balance_amount, F.text, ~F.text.startswith('/'))
async def process_balance_amount(message: Message, state: FSMContext):
    """Processing the balance amount entry."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').replace(',', '.')
    try:
        amount_rub = float(text)
        if amount_rub <= 0:
            raise ValueError()
    except ValueError:
        await safe_edit_or_send(message, '❌ Введите положительное число (например: 100 или 50.5)')
        return
    amount_cents = int(round(amount_rub * 100))
    data = await state.get_data()
    telegram_id = data.get('balance_user_telegram_id')
    operation = data.get('balance_operation')
    if not telegram_id:
        await safe_edit_or_send(message, '❌ Ошибка: потерян контекст операции')
        return
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await safe_edit_or_send(message, '❌ Пользователь не найден')
        return
    user_id = user['id']
    current_balance = get_user_balance(user_id)
    if operation == 'deduct':
        if amount_cents > current_balance:
            balance_rub = current_balance / 100
            await safe_edit_or_send(message, f'❌ Недостаточно средств на балансе.\nТекущий баланс: {balance_rub:.2f} ₽\nПопытка списать: {amount_rub:.2f} ₽')
            return
        from bot.services.balance import debit_user_balance

        result = await debit_user_balance(
            user_id,
            amount_cents,
            source='admin_manual',
            reason='Ручное списание администратором',
            performed_by=message.from_user.id,
            metadata={'admin_telegram_id': message.from_user.id},
        )
        if not result.get('ok'):
            await safe_edit_or_send(message, '❌ Не удалось списать баланс. Проверьте текущий баланс пользователя.')
            return
        new_balance = int(result.get('balance_after') or get_user_balance(user_id))
        new_balance_rub = new_balance / 100
        await safe_edit_or_send(message, f'✅ Баланс списан\n\nСписано: {amount_rub:.2f} ₽\nНовый баланс: {new_balance_rub:.2f} ₽')
        logger.info(f'Админ {message.from_user.id} списал {amount_cents} коп с баланса user {user_id}')
    else:
        from bot.services.balance import credit_user_balance

        result = await credit_user_balance(
            user_id,
            amount_cents,
            source='admin_manual',
            reason='Ручное пополнение администратором',
            performed_by=message.from_user.id,
            metadata={'admin_telegram_id': message.from_user.id},
        )
        if not result.get('ok'):
            await safe_edit_or_send(message, '❌ Не удалось пополнить баланс. Попробуйте ещё раз.')
            return
        new_balance = int(result.get('balance_after') or get_user_balance(user_id))
        new_balance_rub = new_balance / 100
        await safe_edit_or_send(message, f'✅ Баланс пополнен\n\nПополнено: {amount_rub:.2f} ₽\nНовый баланс: {new_balance_rub:.2f} ₽')
        logger.info(f'Админ {message.from_user.id} пополнил баланс user {user_id} на {amount_cents} коп')
    try:
        await message.delete()
    except:
        pass
    await state.update_data(balance_user_telegram_id=None, balance_operation=None)
