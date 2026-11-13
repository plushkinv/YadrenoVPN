
from localization import text as _tr
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
from bot.utils.admin_accounts import selected_user, account_selector, identity_line
from bot.utils.datetime_format import format_datetime_for_display
from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.panel_email import get_panel_email_prefix
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.key_lifecycle import sync_user_keys_panel_access
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic
from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.services.money import format_money_minor, parse_major_to_minor
from database.requests import get_base_currency

logger = logging.getLogger(__name__)
from bot.utils.text import safe_edit_or_send

router = Router()
USERS_PER_PAGE = 20

def format_user_display(user: dict) -> str:
    """Formats the username for display."""
    if user.get('username'):
        return f"@{user['username']}"
    if user.get('telegram_id') is None:
        return _tr('users_manage.format_user_display.1', p0=f"{user['id']}")
    return f"ID: {user['telegram_id']}"


@router.callback_query(F.data.startswith('admin_account_view:'))
async def show_account_view_callback(callback: CallbackQuery, state: FSMContext):
    """Read an account by internal ID without reinterpreting legacy callbacks."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    from database.requests import get_user_by_id
    user = get_user_by_id(int(callback.data.split(':')[1]))
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_id=user['id'], current_user_telegram_id=user['telegram_id'], current_user_selector=account_selector(user))
    text, keyboard = _format_user_card(user)
    await safe_edit_or_send(callback.message, text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith('admin_user_view:'))
async def show_user_view_callback(callback: CallbackQuery, state: FSMContext):
    """Shows the user card (from callback)."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    await _show_user_view_edit(callback, state, telegram_id)

async def _show_user_view(message: Message, state: FSMContext, telegram_id: int):
    """Shows the user card (new message)."""
    user = selected_user(telegram_id)
    if not user:
        await safe_edit_or_send(message, _tr('users_manage.show_user_view.1', p0=f'{telegram_id}'), reply_markup=home_only_kb(), force_new=True)
        return
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=user['telegram_id'], current_user_id=user['id'], current_user_selector=account_selector(user))
    (text, keyboard) = _format_user_card(user)
    await safe_edit_or_send(message, text, reply_markup=keyboard, force_new=True)

async def _show_user_view_edit(callback: CallbackQuery, state: FSMContext, telegram_id: int):
    """Shows the user card (editing a message)."""
    user = selected_user(telegram_id)
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=user['telegram_id'], current_user_id=user['id'], current_user_selector=account_selector(user))
    (text, keyboard) = _format_user_card(user)
    await safe_edit_or_send(callback.message, text, reply_markup=keyboard)
    await callback.answer()

def _format_user_card(user: dict) -> tuple[str, any]:
    """Formats a user card."""
    telegram_id = user['telegram_id']
    username = user.get('username')
    is_banned = bool(user.get('is_banned'))
    is_bot_blocked = bool(user.get('is_bot_blocked'))
    created_at = format_datetime_for_display(user.get('created_at'), fallback=_tr('system.format_extensions_diagnostics.1'))
    balance_cents = get_user_balance(user['id'])
    referral_coefficient = get_user_referral_coefficient(user['id'])
    vpn_keys = get_user_vpn_keys(user['id'])
    
    lines = []
    if is_banned:
        lines.append(_tr('users_manage.format_user_card.1'))
        lines.append('')
        
    if username:
        lines.append(f'👤 Username: @{escape_html(username)}')
    else:
        lines.append(_tr('users_manage.format_user_card.2'))
        
    if telegram_id is None:
        lines.append(_tr('users_manage.format_user_card.3', p0=f"{user['id']}"))
        lines.append(_tr('users_manage.format_user_card.4'))
    else:
        lines.append(f'📱 Telegram ID: <code>{telegram_id}</code>')
    if telegram_id is None:
        lines.append(_tr('users_manage.format_user_card.5'))
    elif is_bot_blocked:
        lines.append(_tr('users_manage.format_user_card.6'))
    else:
        lines.append(_tr('users_manage.format_user_card.7'))
    
    panel_email_prefix = get_panel_email_prefix(user) if telegram_id is not None else f"site_{user['id']}_"
    lines.append(_tr('users_keys.show_key_view.6', p0=f'{escape_html(panel_email_prefix)}'))
    lines.append(_tr('users_manage.format_user_card.8', p0=f'{created_at}'))
    
    lines.append(_tr('users_manage.format_user_card.9', p0=f'{format_money_minor(balance_cents)}'))
    lines.append(_tr('users_manage.format_user_card.10', p0=f'{referral_coefficient}'))
    lines.append('')
    if vpn_keys:
        lines.append(_tr('users_manage.format_user_card.11', p0=f'{len(vpn_keys)}'))
        for key in vpn_keys:
            key_name = key.get('custom_name') or _tr('users_keys.show_key_view.2', p0=f"{key['id']}")
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
            lines.append(_tr('users_manage.format_user_card.12', p0=f'{status}', p1=f'{key_name}', p2=f'{expires}'))
    else:
        lines.append(_tr('users_manage.format_user_card.13'))
    payment_stats = get_user_payments_stats(user['id'])
    lines.append('')
    lines.append(_tr('users_manage.format_user_card.14'))
    total_payments = payment_stats.get('total_payments', 0)
    if total_payments > 0:
        base_totals = payment_stats.get('base_totals') or {}
        last_payment = format_datetime_for_display(payment_stats.get('last_payment_at'), fallback='?')
        lines.append(_tr('users_manage.format_user_card.15', p0=f'{total_payments}'))

        for currency, amount in sorted(base_totals.items()):
            if int(amount or 0) > 0:
                lines.append(_tr('users_manage.format_user_card.16', p0=f'{currency}', p1=f'{format_money_minor(amount, currency)}'))
        lines.append(_tr('users_manage.format_user_card.17', p0=f'{last_payment}'))
    else:
        lines.append(_tr('users_manage.format_user_card.18'))
    text = '\n'.join(lines)
    if telegram_id is None:
        from bot.keyboards.admin_users import account_read_kb
        keyboard = account_read_kb(user['id'], vpn_keys, is_banned, balance_cents, referral_coefficient)
    else:
        keyboard = user_view_kb(telegram_id, vpn_keys, is_banned, balance_cents, referral_coefficient)
    return (text, keyboard)

@router.callback_query(F.data.startswith('admin_user_toggle_ban:'))
async def request_ban_confirmation(callback: CallbackQuery, state: FSMContext):
    """Request confirmation of ban/unban."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    user = selected_user(telegram_id)
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    is_banned = bool(user.get('is_banned'))
    if is_banned:
        action = _tr('users_manage.request_ban_confirmation.1')
    else:
        action = _tr('users_manage.request_ban_confirmation.2')
    text = _tr('users_manage.request_ban_confirmation.3', p0=f'{action}', p1=f'{format_user_display(user)}')
    await safe_edit_or_send(callback.message, text, reply_markup=user_ban_confirm_kb(telegram_id, is_banned))
    await callback.answer()

@router.callback_query(F.data.startswith('admin_user_ban_confirm:'))
@regular_panel_operation
async def confirm_ban_toggle(callback: CallbackQuery, state: FSMContext):
    """Confirmation and execution of ban/unban."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    user = selected_user(telegram_id)
    from database.requests import toggle_account_ban
    new_status = toggle_account_ban(user['id']) if user else None
    if new_status is None:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    action_text = _tr('users_manage.confirm_ban_toggle.1') if new_status else _tr('users_manage.confirm_ban_toggle.2')
    icon = '🚫' if new_status else '✅'
    try:
        from bot.services.key_lifecycle import sync_account_keys_panel_access
        sync_result = await sync_account_keys_panel_access(user['id'])
        keys_total = int(sync_result.get('keys_total', 0) or 0)
        synced = int(sync_result.get('synced', 0) or 0)
        errors = int(sync_result.get('errors', 0) or 0)
        if keys_total == 0:
            panel_status = _tr('users_manage.confirm_ban_toggle.3')
        elif errors:
            panel_status = _tr('users_manage.confirm_ban_toggle.4', p0=f'{synced}', p1=f'{keys_total}', p2=f'{errors}')
        else:
            panel_status = _tr('users_manage.confirm_ban_toggle.5')
    except Exception as e:
        logger.warning(f"Не удалось синхронизировать панель после бана пользователя {telegram_id}: {e}")
        panel_status = _tr('users_manage.confirm_ban_toggle.6')

    await callback.answer(_tr('users_manage.confirm_ban_toggle.7', p0=f'{icon}', p1=f'{action_text}', p2=f'{panel_status}'), show_alert=True)
    await _show_user_view_edit(callback, state, telegram_id)

@router.callback_query(F.data.startswith('admin_user_coefficient:'))
async def start_coefficient_edit(callback: CallbackQuery, state: FSMContext):
    """Start editing the coefficient."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    user = selected_user(telegram_id)
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    current_coefficient = get_user_referral_coefficient(user['id'])
    await state.set_state(AdminStates.waiting_coefficient)
    await state.update_data(coefficient_user_telegram_id=user['telegram_id'], coefficient_user_selector=account_selector(user), coefficient_edit_message_id=callback.message.message_id)
    await safe_edit_or_send(callback.message, _tr('users_manage.start_coefficient_edit.1', p0=f'{format_user_display(user)}', p1=f'{identity_line(user)}', p2=f'{current_coefficient}'), reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
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
    telegram_id = data.get('coefficient_user_selector') or data.get('coefficient_user_telegram_id')
    edit_message_id = data.get('coefficient_edit_message_id')
    user = selected_user(telegram_id)
    if not user:
        await message.delete()
        return
    set_user_referral_coefficient(user['id'], coefficient)
    await message.delete()
    if edit_message_id:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=edit_message_id, text=_tr('users_manage.process_coefficient_input.1', p0=f'{format_user_display(user)}', p1=f'{identity_line(user)}', p2=f'{coefficient}'), reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'), parse_mode='HTML')
        except Exception:
            pass
    await state.clear()

@router.callback_query(F.data.regexp('^admin_user_balance_add:((?:account_)?\\d+)$'))
async def start_balance_add(callback: CallbackQuery, state: FSMContext):
    """Start of replenishing the user's balance."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    user = selected_user(telegram_id)
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    current_balance = get_user_balance(user['id'])
    base_currency = get_base_currency()
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(balance_user_telegram_id=user['telegram_id'], balance_user_selector=account_selector(user), balance_operation='add')
    await safe_edit_or_send(callback.message, _tr('users_manage.start_balance_add.1', p0=f'{format_user_display(user)}', p1=f'{identity_line(user)}', p2=f'{format_money_minor(current_balance, base_currency)}', p3=f'{base_currency}'), reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
    await callback.answer()

@router.callback_query(F.data.regexp('^admin_user_balance_deduct:((?:account_)?\\d+)$'))
async def start_balance_deduct(callback: CallbackQuery, state: FSMContext):
    """Start of debiting the user's balance."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    telegram_id = callback.data.split(':', 1)[1]
    user = selected_user(telegram_id)
    if not user:
        await callback.answer(_tr('users_keys.start_add_key.1'), show_alert=True)
        return
    current_balance = get_user_balance(user['id'])
    base_currency = get_base_currency()
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(balance_user_telegram_id=user['telegram_id'], balance_user_selector=account_selector(user), balance_operation='deduct')
    await safe_edit_or_send(callback.message, _tr('users_manage.start_balance_deduct.1', p0=f'{format_user_display(user)}', p1=f'{identity_line(user)}', p2=f'{format_money_minor(current_balance, base_currency)}', p3=f'{base_currency}'), reply_markup=back_and_home_kb(f'admin_user_view:{telegram_id}'))
    await callback.answer()

@router.message(AdminStates.waiting_balance_amount, F.text, ~F.text.startswith('/'))
async def process_balance_amount(message: Message, state: FSMContext):
    """Processing the balance amount entry."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').replace(',', '.')
    base_currency = get_base_currency()
    try:
        amount_minor = parse_major_to_minor(text, base_currency)
        if amount_minor <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.1'))
        return
    data = await state.get_data()
    telegram_id = data.get('balance_user_selector') or data.get('balance_user_telegram_id')
    operation = data.get('balance_operation')
    if not telegram_id:
        await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.2'))
        return
    user = selected_user(telegram_id)
    if not user:
        await safe_edit_or_send(message, _tr('support.admin_support_start.2'))
        return
    user_id = user['id']
    current_balance = get_user_balance(user_id)
    if operation == 'deduct':
        if amount_minor > current_balance:
            await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.3', p0=f'{format_money_minor(current_balance, base_currency)}', p1=f'{format_money_minor(amount_minor, base_currency)}'))
            return
        from bot.services.balance import debit_user_balance

        result = await debit_user_balance(
            user_id,
            amount_minor,
            source='admin_manual',
            reason=_tr('users_manage.process_balance_amount.4'),
            performed_by=message.from_user.id,
            metadata={'admin_telegram_id': message.from_user.id},
        )
        if not result.get('ok'):
            await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.5'))
            return
        new_balance = int(result.get('balance_after') or get_user_balance(user_id))
        await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.6', p0=f'{format_money_minor(amount_minor, base_currency)}', p1=f'{format_money_minor(new_balance, base_currency)}'))
        logger.info(f'Админ {message.from_user.id} списал {amount_minor} minor {base_currency} с баланса user {user_id}')
    else:
        from bot.services.balance import credit_user_balance

        result = await credit_user_balance(
            user_id,
            amount_minor,
            source='admin_manual',
            reason=_tr('users_manage.process_balance_amount.7'),
            performed_by=message.from_user.id,
            metadata={'admin_telegram_id': message.from_user.id},
        )
        if not result.get('ok'):
            await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.8'))
            return
        new_balance = int(result.get('balance_after') or get_user_balance(user_id))
        await safe_edit_or_send(message, _tr('users_manage.process_balance_amount.9', p0=f'{format_money_minor(amount_minor, base_currency)}', p1=f'{format_money_minor(new_balance, base_currency)}'))
        logger.info(f'Админ {message.from_user.id} пополнил баланс user {user_id} на {amount_minor} minor {base_currency}')
    try:
        await message.delete()
    except:
        pass
    await state.update_data(balance_user_telegram_id=None, balance_user_selector=None, balance_operation=None)
