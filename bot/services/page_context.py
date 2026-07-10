"""
Память о последней пользовательской странице, которую видел администратор.

Нужна для команды /yaa: администратор может вызвать её прямо с пользовательской
страницы, а агент получает точный контекст и после изменения экран можно
перерисовать без лишних вопросов.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, Message

from bot.utils.custom_pages import custom_page_exists


SUPPORTED_YAA_PAGE_KEYS = frozenset({
    'main',
    'help',
    'trial',
    'access_blocked',
    'prepayment',
    'prepayment_unavailable',
    'renew_payment',
    'referral',
    'key_delivery',
    'qr_payment',
    'crypto_payment',
    'balance_payment',
    'demo_payment',
    'payment_tariff_select',
    'payment_status',
    'support_start',
    'support_status',
    'promo_enter',
    'promo_status',
    'show_id',
    'my_keys',
    'my_keys_empty',
    'key_details',
    'key_status',
    'key_show_unconfigured',
    'renew_payment_unavailable',
    'key_replace_server_select',
    'key_replace_inbound_select',
    'key_replace_confirm',
    'key_rename_prompt',
    'new_key_server_select',
    'new_key_inbound_select',
    'new_key_no_servers',
})


@dataclass
class PageContext:
    """Последний рендер редактируемой пользовательской страницы."""

    page_key: str
    message: Message
    visibility: Optional[Dict[str, bool]]
    context: Optional[Dict[str, Any]]
    text_replacements: Optional[Dict[str, str]]
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]]
    append_buttons: Optional[List[List[InlineKeyboardButton]]]


_contexts: dict[int, PageContext] = {}


def _copy_optional_mapping(value: Optional[Mapping[str, Any]], field_name: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} должен быть mapping или None")
    return dict(value)


def _copy_visibility(value: Optional[Mapping[str, bool]]) -> Optional[Dict[str, bool]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("visibility должен быть mapping или None")
    visibility: Dict[str, bool] = {}
    for button_id, visible in value.items():
        if not isinstance(button_id, str):
            raise ValueError("visibility button_id должен быть строкой")
        if not isinstance(visible, bool):
            raise ValueError("visibility values должны быть bool")
        visibility[button_id] = visible
    return visibility


def _copy_button_rows(
    rows: Optional[List[List[InlineKeyboardButton]]],
    field_name: str,
) -> Optional[List[List[InlineKeyboardButton]]]:
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise ValueError(f"{field_name} должен быть списком рядов кнопок или None")
    copied_rows: List[List[InlineKeyboardButton]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError(f"{field_name} должен содержать только ряды кнопок")
        copied_row: List[InlineKeyboardButton] = []
        for button in row:
            if not isinstance(button, InlineKeyboardButton):
                raise ValueError(f"{field_name} должен содержать только InlineKeyboardButton")
            copied_row.append(button)
        copied_rows.append(copied_row)
    return copied_rows


def is_supported_yaa_page_key(page_key: str) -> bool:
    """Проверяет, можно ли запоминать страницу для контекстной команды /yaa."""
    if not isinstance(page_key, str):
        raise ValueError("page_key должен быть строкой")
    return page_key in SUPPORTED_YAA_PAGE_KEYS or custom_page_exists(page_key)


def remember_page_context(
    telegram_id: int,
    page_key: str,
    message: Message,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict[str, Any]] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> None:
    """Запоминает страницу администратора, если она поддерживает /yaa."""
    if not is_supported_yaa_page_key(page_key):
        return
    _contexts[telegram_id] = PageContext(
        page_key=page_key,
        message=message,
        visibility=_copy_visibility(visibility),
        context=_copy_optional_mapping(context, 'context'),
        text_replacements=_copy_optional_mapping(text_replacements, 'text_replacements'),
        prepend_buttons=_copy_button_rows(prepend_buttons, 'prepend_buttons'),
        append_buttons=_copy_button_rows(append_buttons, 'append_buttons'),
    )


def get_page_context(telegram_id: int) -> Optional[PageContext]:
    """Возвращает последнюю страницу администратора для /yaa."""
    return _contexts.get(telegram_id)


def clear_page_context(telegram_id: int) -> None:
    """Очищает сохранённый контекст страницы администратора."""
    _contexts.pop(telegram_id, None)
