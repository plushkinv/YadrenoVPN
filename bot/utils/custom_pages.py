"""Правила пользовательских страниц конструктора."""
from __future__ import annotations

import re
from typing import Optional

from bot.utils.action_registry import normalize_callback_data


CUSTOM_PAGE_PREFIX = "custom_"
CUSTOM_PAGE_CALLBACK_PREFIX = "page:"

_CUSTOM_PAGE_KEY_RE = re.compile(r"^custom_[a-z0-9_]+$")


def is_custom_page_key(page_key: object) -> bool:
    """Проверяет формат ключа пользовательской страницы."""
    return isinstance(page_key, str) and bool(_CUSTOM_PAGE_KEY_RE.fullmatch(page_key))


def build_custom_page_callback(page_key: object) -> Optional[str]:
    """Возвращает callback для custom-страницы, если он помещается в лимит Telegram."""
    if not is_custom_page_key(page_key):
        return None

    callback_data = f"{CUSTOM_PAGE_CALLBACK_PREFIX}{page_key}"
    try:
        return normalize_callback_data(callback_data)
    except ValueError:
        return None


def extract_custom_page_key(callback_data: object) -> Optional[str]:
    """Извлекает и валидирует page_key из callback_data вида page:custom_x."""
    if not isinstance(callback_data, str):
        return None
    if not callback_data.startswith(CUSTOM_PAGE_CALLBACK_PREFIX):
        return None

    page_key = callback_data[len(CUSTOM_PAGE_CALLBACK_PREFIX):]
    if not build_custom_page_callback(page_key):
        return None

    return page_key


def custom_page_exists(page_key: object) -> bool:
    """Проверяет, что custom-страница валидна и есть в таблице pages."""
    if not is_custom_page_key(page_key):
        return False

    from database.requests import get_page

    return get_page(page_key) is not None
