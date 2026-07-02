"""
Память о последней пользовательской странице, которую видел администратор.

Нужна для команды /yaa: администратор может вызвать её прямо с пользовательской
страницы, а агент получает точный контекст и после изменения экран можно
перерисовать без лишних вопросов.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, Message

from bot.utils.custom_pages import custom_page_exists


SUPPORTED_YAA_PAGE_KEYS = frozenset({
    'main',
    'help',
    'trial',
    'prepayment',
    'renew_payment',
    'referral',
    'key_delivery',
    'my_keys',
    'my_keys_empty',
    'key_details',
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


def is_supported_yaa_page_key(page_key: str) -> bool:
    """Проверяет, можно ли запоминать страницу для контекстной команды /yaa."""
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
        visibility=dict(visibility) if visibility else None,
        context=dict(context) if context else None,
        text_replacements=dict(text_replacements) if text_replacements else None,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )


def get_page_context(telegram_id: int) -> Optional[PageContext]:
    """Возвращает последнюю страницу администратора для /yaa."""
    return _contexts.get(telegram_id)


def clear_page_context(telegram_id: int) -> None:
    """Очищает сохранённый контекст страницы администратора."""
    _contexts.pop(telegram_id, None)
