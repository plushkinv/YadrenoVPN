"""Клавиатуры раздела Yadreno Admin."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .admin_misc import back_button, home_button


def yadreno_admin_no_key_kb() -> InlineKeyboardMarkup:
    """Клавиатура экрана, где api_key ещё не задан."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='🔑 Указать api_key',
            callback_data='admin_yadreno_set_key',
            style='primary',
        )
    )
    builder.row(
        InlineKeyboardButton(
            text='🤖 Открыть @YadrenoAdmin_Bot',
            url='https://t.me/YadrenoAdmin_Bot',
        )
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def yadreno_admin_chat_kb() -> InlineKeyboardMarkup:
    """Клавиатура режима диалога с агентом."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='🔑 Заменить api_key',
            callback_data='admin_yadreno_set_key',
        )
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def yadreno_admin_cancel_key_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены ввода api_key."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='❌ Отмена',
            callback_data='admin_yadreno',
        )
    )
    return builder.as_markup()
