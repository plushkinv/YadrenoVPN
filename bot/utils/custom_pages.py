"""Правила пользовательских страниц конструктора."""
from __future__ import annotations

import re
from typing import Optional


CUSTOM_PAGE_PREFIX = "custom_"
CUSTOM_PAGE_CALLBACK_PREFIX = "page:"
MAX_CALLBACK_DATA_BYTES = 64

_CUSTOM_PAGE_KEY_RE = re.compile(r"^custom_[a-z0-9_]+$")


def is_custom_page_key(page_key: object) -> bool:
    """Проверяет формат ключа пользовательской страницы."""
    return isinstance(page_key, str) and bool(_CUSTOM_PAGE_KEY_RE.fullmatch(page_key))


def build_custom_page_callback(page_key: str) -> Optional[str]:
    """Возвращает callback для custom-страницы, если он помещается в лимит Telegram."""
    if not is_custom_page_key(page_key):
        return None

    callback_data = f"{CUSTOM_PAGE_CALLBACK_PREFIX}{page_key}"
    if len(callback_data.encode("utf-8")) > MAX_CALLBACK_DATA_BYTES:
        return None

    return callback_data


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
