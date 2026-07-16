from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

USERS_FILTERS = {'all': '👤 Все', 'active': '✅ Активные', 'inactive': '❌ Неактивные', 'never_paid': '🆕 Новые', 'expired': '🚫 Истёкшие', 'bot_blocked': '📵 Бот заблокирован'}

def users_menu_kb(stats: Dict[str, int]) -> InlineKeyboardMarkup:
    """
    Main menu of the users section.
    
    Args:
        stats: User statistics by filters
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"📋 Все пользователи ({stats.get('total', 0)})", callback_data='admin_users_list'))
    builder.row(InlineKeyboardButton(text='🔍 Выбрать пользователя', callback_data='admin_users_select'))
    builder.row(InlineKeyboardButton(text='📤 Выгрузить в панель (БД → Панель)', callback_data='admin_sync_db_to_panel'))
    builder.row(InlineKeyboardButton(text='📥 Загрузить из панели (Панель → БД)', callback_data='admin_sync_panel_to_db'))
    builder.row(InlineKeyboardButton(text='🗑️ Синхронизация удалённых', callback_data='admin_sync_deleted_menu'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def manual_sync_preview_kb(direction: str, token: str) -> InlineKeyboardMarkup:
    """Confirmation keyboard for a previously calculated manual sync preview."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text='✅ Применить изменения',
        callback_data=f'admin_sync_apply:{direction}:{token}',
    ))
    builder.row(InlineKeyboardButton(
        text='❌ Отмена',
        callback_data=f'admin_sync_cancel:{token}',
    ))
    builder.row(back_button('admin_users'), home_button())
    return builder.as_markup()

def users_list_kb(users: List[Dict[str, Any]], page: int, total_pages: int, current_filter: str='all') -> InlineKeyboardMarkup:
    """
    User list keyboard with pagination and filters.
    
    Args:
        users: List of users on the current page
        page: Current page number (starting from 0)
        total_pages: Total number of pages
        current_filter: Current filter
    """
    builder = InlineKeyboardBuilder()
    filter_buttons = []
    for (filter_key, filter_name) in USERS_FILTERS.items():
        text = f'🔹{filter_name}' if filter_key == current_filter else filter_name
        filter_buttons.append(InlineKeyboardButton(text=text, callback_data=f'admin_users_filter:{filter_key}'))
    builder.row(*filter_buttons[:3])
    builder.row(*filter_buttons[3:])
    for user in users:
        username = user.get('username')
        telegram_id = user.get('telegram_id')
        if username:
            text = f'@{username}'
        else:
            text = f'ID: {telegram_id}'
        builder.row(InlineKeyboardButton(text=text, callback_data=f'admin_user_view:{telegram_id}'))
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text='◀️', callback_data=f'admin_users_page:{page - 1}'))
        nav_buttons.append(InlineKeyboardButton(text=f'{page + 1}/{total_pages}', callback_data='noop'))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text='▶️', callback_data=f'admin_users_page:{page + 1}'))
        builder.row(*nav_buttons)
    builder.row(back_button('admin_users'), home_button())
    return builder.as_markup()

def user_view_kb(telegram_id: int, vpn_keys: List[Dict[str, Any]], is_banned: bool, balance_cents: int=0, referral_coefficient: float=1.0) -> InlineKeyboardMarkup:
    """
    User view keyboard.
    
    Args:
        telegram_id: Telegram user ID
        vpn_keys: List of user's VPN keys
        is_banned: Whether the user is banned
        balance_cents: Balance in kopecks
        referral_coefficient: Referral coefficient
    """
    builder = InlineKeyboardBuilder()
    for key in vpn_keys:
        key_id = key['id']
        if key.get('custom_name'):
            key_name = key['custom_name']
        else:
            uuid = key.get('client_uuid') or ''
            if len(uuid) >= 8:
                key_name = f'{uuid[:4]}...{uuid[-4:]}'
            else:
                key_name = uuid or f'Ключ #{key_id}'
        expires_at = key.get('expires_at')
        if expires_at:
            status = '🔑'
        else:
            status = '🔑'
        builder.row(InlineKeyboardButton(text=f'{status} {key_name}', callback_data=f'admin_key_view:{key_id}'))
    builder.row(InlineKeyboardButton(text='➕ Добавить ключ', callback_data=f'admin_user_add_key:{telegram_id}'))
    builder.row(InlineKeyboardButton(text='💬 Написать', callback_data=f'admin_support_start:{telegram_id}'))
    balance_rub = balance_cents / 100
    builder.row(InlineKeyboardButton(text=f'💰 Баланс: {balance_rub:.2f} ₽', callback_data=f'admin_user_balance:{telegram_id}'), InlineKeyboardButton(text='➕ Пополнить', callback_data=f'admin_user_balance_add:{telegram_id}'), InlineKeyboardButton(text='➖ Списать', callback_data=f'admin_user_balance_deduct:{telegram_id}'))
    builder.row(InlineKeyboardButton(text=f'📊 Реферальный коэффициент: {referral_coefficient}x', callback_data=f'admin_user_coefficient:{telegram_id}'))
    if is_banned:
        ban_text = '✅ Разблокировать'
    else:
        ban_text = '🚫 Заблокировать'
    builder.row(InlineKeyboardButton(text=ban_text, callback_data=f'admin_user_toggle_ban:{telegram_id}'))
    builder.row(back_button('admin_users_list'), home_button())
    return builder.as_markup()

def user_ban_confirm_kb(telegram_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    """
    Ban/unban confirmation keyboard.
    
    Args:
        telegram_id: Telegram user ID
        is_banned: Current status (True = banned)
    """
    builder = InlineKeyboardBuilder()
    if is_banned:
        confirm_text = '✅ Да, разблокировать'
    else:
        confirm_text = '🚫 Да, заблокировать'
    builder.row(InlineKeyboardButton(text=confirm_text, callback_data=f'admin_user_ban_confirm:{telegram_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'admin_user_view:{telegram_id}'))
    return builder.as_markup()

def key_view_kb(key_id: int, user_telegram_id: int) -> InlineKeyboardMarkup:
    """
    VPN key management keyboard.
    
    Args:
        key_id: Key ID
        user_telegram_id: Telegram owner ID (for return)
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='📅 Продлить', callback_data=f'admin_key_extend:{key_id}'))
    builder.row(InlineKeyboardButton(text='🔄 Сбросить трафик', callback_data=f'admin_key_reset_traffic:{key_id}'))
    builder.row(InlineKeyboardButton(text='📊 Изменить лимит трафика', callback_data=f'admin_key_change_traffic:{key_id}'))
    builder.row(InlineKeyboardButton(text='🗑️ Удалить ключ', callback_data=f'admin_key_delete_ask:{key_id}'))
    builder.row(back_button(f'admin_user_view:{user_telegram_id}'), home_button())
    return builder.as_markup()

def add_key_server_kb(servers: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Server selection keyboard for a new key.
    
    Args:
        servers: List of active servers
    """
    builder = InlineKeyboardBuilder()
    for server in servers:
        builder.row(InlineKeyboardButton(text=f"🖥️ {server['name']}", callback_data=f"admin_add_key_server:{server['id']}"))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_user_add_key_cancel'))
    return builder.as_markup()

def add_key_inbound_kb(inbounds: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Keyboard selection inbound for new key.
    
    Args:
        inbounds: List of inbound connections
    """
    builder = InlineKeyboardBuilder()
    for inbound in inbounds:
        inbound_id = inbound.get('id')
        protocol = inbound.get('protocol', 'unknown')
        remark = inbound.get('remark', f'Inbound #{inbound_id}')
        builder.row(InlineKeyboardButton(text=f'🔌 {remark} ({protocol})', callback_data=f'admin_add_key_inbound:{inbound_id}'))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_add_key_back'), InlineKeyboardButton(text='❌ Отмена', callback_data='admin_user_add_key_cancel'))
    return builder.as_markup()

def add_key_step_kb(step: int) -> InlineKeyboardMarkup:
    """
    Keyboard for steps to add a key (traffic, days).
    
    Args:
        step: Current step
    """
    builder = InlineKeyboardBuilder()
    buttons = []
    if step > 1:
        buttons.append(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_add_key_back'))
    buttons.append(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_user_add_key_cancel'))
    builder.row(*buttons)
    return builder.as_markup()

def add_key_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard confirmation for key creation."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Создать ключ', callback_data='admin_add_key_confirm'))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_add_key_back'), InlineKeyboardButton(text='❌ Отмена', callback_data='admin_user_add_key_cancel'))
    return builder.as_markup()

def users_input_cancel_kb() -> InlineKeyboardMarkup:
    """Cancel keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_users'))
    return builder.as_markup()

def key_action_cancel_kb(key_id: int, user_telegram_id: int) -> InlineKeyboardMarkup:
    """Undo keypad with key."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'admin_key_view:{key_id}'))
    return builder.as_markup()

def key_delete_confirm_kb(key_id: int, user_telegram_id: int) -> InlineKeyboardMarkup:
    """Keypad to confirm key deletion."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🗑️ Да, удалить', callback_data=f'admin_key_delete_confirm:{key_id}'))
    builder.row(InlineKeyboardButton(text='❌ Оставить', callback_data=f'admin_key_view:{key_id}'))
    return builder.as_markup()

def sync_deleted_menu_kb() -> InlineKeyboardMarkup:
    """Submenu for synchronizing remote keys."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🧹 Очистить панель', callback_data='admin_sync_deleted_panel_ask'))
    builder.row(InlineKeyboardButton(text='🗑️ Очистить базу', callback_data='admin_sync_deleted_db_ask'))
    builder.row(back_button('admin_users'), home_button())
    return builder.as_markup()

def sync_deleted_panel_confirm_kb() -> InlineKeyboardMarkup:
    """Panel clear confirmation keypad."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🧹 Да, очистить панель', callback_data='admin_sync_deleted_panel_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()

def sync_deleted_db_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard confirmation to start scanning the database."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔍 Начать сканирование', callback_data='admin_sync_deleted_db_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()

def sync_deleted_db_report_kb(report: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Database scan report keyboard - a button for each category of problematic keys."""
    builder = InlineKeyboardBuilder()

    # Category 1: Serverless keys
    if report.get('null_total', 0) > 0:
        builder.row(InlineKeyboardButton(
            text=f"🗑️ Без сервера ({report['null_total']})",
            callback_data='admin_sync_db_orphans_ask'
        ))

    # Category 2: remote servers
    for sid, count in report.get('deleted_srv_keys', {}).items():
        builder.row(InlineKeyboardButton(
            text=f"👻 Удалённый сервер ID {sid} ({count})",
            callback_data=f'admin_sync_db_gone_ask:{sid}'
        ))

    # Categories 3-5: by server
    for r in report.get('server_results', []):
        if r['status'] == 'reachable' and r.get('missing_count', 0) > 0:
            builder.row(InlineKeyboardButton(
                text=f"🗑️ {r['name']}: нет на панели ({r['missing_count']})",
                callback_data=f'admin_sync_db_missing_ask:{r["server_id"]}'
            ))
        elif r['status'] == 'unreachable':
            active_mark = "" if r['is_active'] else " ⏸️"
            builder.row(InlineKeyboardButton(
                text=f"⚠️ {r['name']}{active_mark}: недоступен ({r['total_keys']})",
                callback_data=f'admin_sync_db_unreach_ask:{r["server_id"]}'
            ))

    builder.row(InlineKeyboardButton(text='🔄 Повторить', callback_data='admin_sync_deleted_db_confirm'))
    builder.row(back_button('admin_sync_deleted_menu'), home_button())
    return builder.as_markup()

def sync_db_orphans_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard confirmation for deleting keys without a server."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🗑️ Да, удалить', callback_data='admin_sync_db_orphans_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()

def sync_db_gone_confirm_kb(server_id: int) -> InlineKeyboardMarkup:
    """Keyboard for confirming the deletion of remote server keys."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🗑️ Да, удалить', callback_data=f'admin_sync_db_gone_confirm:{server_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()

def sync_db_missing_confirm_kb(server_id: int) -> InlineKeyboardMarkup:
    """Keyboard to confirm deletion of keys missing from the panel."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🗑️ Да, удалить', callback_data=f'admin_sync_db_missing_confirm:{server_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()

def sync_db_unreach_confirm_kb(server_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for deleting unavailable server keys."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🚨 ДА, УДАЛИТЬ КЛЮЧИ', callback_data=f'admin_sync_db_unreach_confirm:{server_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_sync_deleted_menu'))
    return builder.as_markup()
