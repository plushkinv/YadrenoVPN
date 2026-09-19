"""Keyboards of the Yadreno Admin section."""
from __future__ import annotations

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


def yadreno_admin_chat_kb(
    topic_id: int = 0,
    *,
    show_api_key_action: bool = True,
) -> InlineKeyboardMarkup:
    """Agent chat input keyboard."""
    builder = InlineKeyboardBuilder()
    primary_buttons = [
        InlineKeyboardButton(
            text='🆕 Новый чат',
            callback_data=f'admin_yadreno_new_chat:{int(topic_id)}',
        )
    ]
    if show_api_key_action:
        primary_buttons.append(
            InlineKeyboardButton(
                text='🔑 Заменить api_key',
                callback_data='admin_yadreno_set_key',
            )
        )
    builder.row(*primary_buttons)
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def yadreno_admin_agent_kb(
    topic_id: int = 0,
    *,
    active_request: bool = True,
    viewer_url: str | None = None,
    cancel_button_text: str | None = None,
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

    buttons: list[InlineKeyboardButton] = []
    if active_request and cancel_button_text:
        buttons.append(InlineKeyboardButton(
            text=cancel_button_text,
            callback_data=f'admin_yadreno_cancel:{int(topic_id)}',
        ))
    elif not active_request:
        buttons.append(InlineKeyboardButton(
            text='🚪 Выйти',
            callback_data='admin_panel',
        ))
    builder.row(
        *buttons,
        InlineKeyboardButton(
            text='🔄 Ну чё там?',
            callback_data=f'admin_yadreno_nudge:{int(topic_id)}',
        ),
    )
    return builder.as_markup()


def yadreno_admin_request_error_kb(
    topic_id: int = 0,
    *,
    active_request: bool,
    cancel_button_text: str | None = None,
    configuration_error: bool = False,
    show_api_key_action: bool = True,
) -> InlineKeyboardMarkup:
    """Show task controls only when the rejected turn has an active request."""
    if configuration_error:
        return yadreno_admin_chat_kb(topic_id)
    if active_request:
        return yadreno_admin_agent_kb(topic_id, cancel_button_text=cancel_button_text)
    return yadreno_admin_chat_kb(
        topic_id,
        show_api_key_action=show_api_key_action,
    )


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


def yadreno_admin_input_kb(task_controls: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """Local navigation before a contextual task has been sent to the Hub."""
    builder = InlineKeyboardBuilder()
    if task_controls:
        builder.attach(InlineKeyboardBuilder.from_markup(task_controls))
    builder.row(back_button('admin_yadreno_input_exit'), home_button())
    return builder.as_markup()
