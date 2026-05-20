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


SUPPORTED_YAA_PAGE_KEYS = frozenset({
    'main',
    'help',
    'trial',
    'prepayment',
    'referral',
})


@dataclass
class PageContext:
    """Последний рендер редактируемой пользовательской страницы."""

    page_key: str
    message: Message
    visibility: Optional[Dict[str, bool]]
    context: Optional[Dict[str, Any]]
    text_replacements: Optional[Dict[str, str]]
    append_buttons: Optional[List[List[InlineKeyboardButton]]]


_contexts: dict[int, PageContext] = {}


def remember_page_context(
    telegram_id: int,
    page_key: str,
    message: Message,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict[str, Any]] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> None:
    """Запоминает страницу администратора, если она поддерживает /yaa."""
    if page_key not in SUPPORTED_YAA_PAGE_KEYS:
        return
    _contexts[telegram_id] = PageContext(
        page_key=page_key,
        message=message,
        visibility=dict(visibility) if visibility else None,
        context=dict(context) if context else None,
        text_replacements=dict(text_replacements) if text_replacements else None,
        append_buttons=append_buttons,
    )


def get_page_context(telegram_id: int) -> Optional[PageContext]:
    """Возвращает последнюю страницу администратора для /yaa."""
    return _contexts.get(telegram_id)


def clear_page_context(telegram_id: int) -> None:
    """Очищает сохранённый контекст страницы администратора."""
    _contexts.pop(telegram_id, None)
