from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

BROADCAST_FILTERS = {'all': '👤 Все пользователи', 'active': '✅ С активными ключами', 'inactive': '❌ Без активных ключей', 'never_paid': '🆕 Никогда не покупали', 'expired': '🚫 Ключ истёк'}

def broadcast_main_kb(has_message: bool, current_filter: str, broadcast_in_progress: bool, user_count: int) -> InlineKeyboardMarkup:
    """
    Main mailing menu.
    
    Args:
        has_message: Whether there is a saved message
        current_filter: Current selected filter
        broadcast_in_progress: Is broadcasting in progress now?
        user_count: Number of users by current filter
    """
    builder = InlineKeyboardBuilder()
    msg_status = '✅' if has_message else '❌'
    builder.row(InlineKeyboardButton(text=f'✉️ Сообщение: {msg_status}', callback_data='broadcast_edit_message'), InlineKeyboardButton(text='👁️ Превью', callback_data='broadcast_preview'))
    for (filter_key, filter_name) in BROADCAST_FILTERS.items():
        radio = '🔘' if filter_key == current_filter else '⚪'
        builder.row(InlineKeyboardButton(text=f'{radio} {filter_name}', callback_data=f'broadcast_filter:{filter_key}'))
    if broadcast_in_progress:
        builder.row(InlineKeyboardButton(text='🛑 Остановить рассылку', callback_data='broadcast_stop'))
    else:
        builder.row(InlineKeyboardButton(text=f'🚀 Начать рассылку ({user_count} чел.)', callback_data='broadcast_start'))
    builder.row(InlineKeyboardButton(text='─────────────────', callback_data='noop'))
    builder.row(InlineKeyboardButton(text='⏰ Настройки автоуведомлений', callback_data='broadcast_notifications'))
    builder.row(back_button('admin_marketing'), home_button())
    return builder.as_markup()

def broadcast_confirm_kb(user_count: int) -> InlineKeyboardMarkup:
    """Mailing confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f'✅ Да, разослать ({user_count} чел.)', callback_data='broadcast_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_broadcast'))
    return builder.as_markup()

def broadcast_stop_kb() -> InlineKeyboardMarkup:
    """Keyboard to stop the current mailing."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🛑 Остановить рассылку', callback_data='broadcast_stop'))
    return builder.as_markup()

def broadcast_notifications_kb(days: int) -> InlineKeyboardMarkup:
    """Keyboard for setting auto notifications."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f'📅 За сколько дней: {days}', callback_data='broadcast_notify_days'))
    builder.row(InlineKeyboardButton(text='📝 Текст уведомления', callback_data='broadcast_notify_text'))
    builder.row(back_button('admin_broadcast'), home_button())
    return builder.as_markup()

def broadcast_back_kb() -> InlineKeyboardMarkup:
    """Return to mailing keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_broadcast'))
    return builder.as_markup()

def broadcast_notify_back_kb() -> InlineKeyboardMarkup:
    """Keyboard to return to notification settings."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='broadcast_notifications'))
    return builder.as_markup()
