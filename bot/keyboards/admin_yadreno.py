"""Keyboards of the Yadreno Admin section."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.telegram_links import build_telegram_link

from .admin_misc import back_button, home_button


def yadreno_admin_no_key_kb() -> InlineKeyboardMarkup:
    """Screen keyboard where api_key has not yet been set."""
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
            url=build_telegram_link('YadrenoAdmin_Bot'),
        )
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def yadreno_admin_chat_kb(topic_id: int = 0) -> InlineKeyboardMarkup:
    """Agent chat input keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='🆕 Новый чат',
            callback_data=f'admin_yadreno_new_chat:{int(topic_id)}',
        ),
        InlineKeyboardButton(
            text='🔑 Заменить api_key',
            callback_data='admin_yadreno_set_key',
        ),
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def yadreno_admin_agent_kb(
    topic_id: int = 0,
    *,
    active_request: bool = True,
    viewer_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Build controls for an active request or a completed agent response."""
    builder = InlineKeyboardBuilder()
    if viewer_url:
        builder.row(
            InlineKeyboardButton(
                text='📄 Открыть ответ',
                url=viewer_url,
            )
        )

    primary_button = (
        InlineKeyboardButton(
            text='❌ Отмена',
            callback_data=f'admin_yadreno_cancel:{int(topic_id)}',
        )
        if active_request
        else InlineKeyboardButton(
            text='🚪 Выйти',
            callback_data='admin_panel',
        )
    )
    builder.row(
        primary_button,
        InlineKeyboardButton(
            text='🔄 Ну чё там?',
            callback_data=f'admin_yadreno_nudge:{int(topic_id)}',
        ),
    )
    return builder.as_markup()


def yadreno_admin_cancel_key_kb() -> InlineKeyboardMarkup:
    """Keyboard to cancel input api_key."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='❌ Отмена',
            callback_data='admin_yadreno',
        )
    )
    return builder.as_markup()
