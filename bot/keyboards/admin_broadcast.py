from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Optional

from .admin_misc import back_button, home_button

BROADCAST_FILTERS = {'all': '👤 Все пользователи', 'active': '✅ С активными ключами', 'inactive': '❌ Без активных ключей', 'never_paid': '🆕 Никогда не покупали', 'expired': '🚫 Ключ истёк'}

def broadcast_main_kb(
    has_message: bool,
    current_filter: str,
    broadcast_in_progress: bool,
    user_count: int,
    content_kind: Optional[str] = None,
) -> InlineKeyboardMarkup:
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
    content_label = '📊 Опрос' if content_kind == 'poll' else '✉️ Сообщение'
    builder.row(InlineKeyboardButton(text=f'{content_label}: {msg_status}', callback_data='broadcast_edit_message'), InlineKeyboardButton(text='👁️ Превью', callback_data='broadcast_preview'))
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

def _legacy_broadcast_confirm_kb(user_count: int) -> InlineKeyboardMarkup:
    """Mailing confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f'✅ Да, разослать ({user_count} чел.)', callback_data='broadcast_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_broadcast'))
    return builder.as_markup()

def broadcast_confirm_kb(user_count: int, token: str = "") -> InlineKeyboardMarkup:
    """Build a one-time-token launch confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    callback_data = f"broadcast_confirm:{token}" if token else "broadcast_confirm"
    builder.row(
        InlineKeyboardButton(
            text=f"✅ Разослать {user_count}",
            callback_data=callback_data,
        )
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast")
    )
    return builder.as_markup()


def broadcast_editor_kb() -> InlineKeyboardMarkup:
    """Trusted local controls shown after each broadcast-editor response."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👁 Превью", callback_data="broadcast_editor_preview"),
        InlineKeyboardButton(text="💾 Сохранить", callback_data="broadcast_editor_save"),
    )
    builder.row(InlineKeyboardButton(text="🚀 К запуску", callback_data="broadcast_editor_launch"))
    builder.row(InlineKeyboardButton(text="🚪 Выйти", callback_data="broadcast_editor_exit"))
    return builder.as_markup()


def broadcast_editor_dirty_exit_kb() -> InlineKeyboardMarkup:
    """Resolve unsaved staged edits before leaving the editor lane."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💾 Сохранить и выйти",
            callback_data="broadcast_editor_exit_save",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Выйти без сохранения",
            callback_data="broadcast_editor_exit_discard",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="↩️ Продолжить",
            callback_data="broadcast_editor_exit_continue",
        )
    )
    return builder.as_markup()


def broadcast_stop_kb() -> InlineKeyboardMarkup:
    """Keyboard to stop the current mailing."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🛑 Остановить рассылку', callback_data='broadcast_stop'))
    return builder.as_markup()


def broadcast_poll_mode_kb() -> InlineKeyboardMarkup:
    """Choice between a clean poll and preserving its existing results."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🧹 Начать с нуля', callback_data='broadcast_poll_mode:clean'))
    builder.row(InlineKeyboardButton(text='📊 Сохранить голоса', callback_data='broadcast_poll_mode:preserve'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_broadcast'))
    return builder.as_markup()


def broadcast_result_kb(poll_close_callback: Optional[str] = None) -> InlineKeyboardMarkup:
    """Final broadcast controls with an optional common-poll close action."""
    builder = InlineKeyboardBuilder()
    if poll_close_callback:
        builder.row(InlineKeyboardButton(text='🛑 Закрыть опрос', callback_data=poll_close_callback))
    builder.row(home_button())
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
