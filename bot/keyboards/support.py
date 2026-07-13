from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def support_contact_kb(support_link: str) -> InlineKeyboardMarkup:
    """Keyboard transition to external support."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Написать в поддержку", url=support_link))
    builder.row(InlineKeyboardButton(text="🈴 На главную", callback_data="start"))
    return builder.as_markup()


def user_support_reply_kb(thread_id: int) -> InlineKeyboardMarkup:
    """User reply button in the support chain."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Ответить", callback_data=f"support_reply:{thread_id}"))
    builder.row(InlineKeyboardButton(text="🈴 На главную", callback_data="start"))
    return builder.as_markup()


def admin_support_reply_kb(thread_id: int) -> InlineKeyboardMarkup:
    """Admin response button in the support chain."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_support_reply:{thread_id}"))
    return builder.as_markup()


def support_user_cancel_kb() -> InlineKeyboardMarkup:
    """Keyboard override user input support."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="start"))
    return builder.as_markup()


def support_admin_cancel_kb(back_callback: str = "admin_panel") -> InlineKeyboardMarkup:
    """Keyboard cancel admin input support."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="admin_panel"),
    )
    return builder.as_markup()


def support_admin_home_kb() -> InlineKeyboardMarkup:
    """The admin keyboard returns to the main admin area."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🈴 На главную", callback_data="admin_panel"))
    return builder.as_markup()
