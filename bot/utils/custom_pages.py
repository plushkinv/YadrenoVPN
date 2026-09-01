"""Rules for custom builder pages."""
from __future__ import annotations

from typing import Optional

from bot.utils.action_registry import normalize_callback_data
from database.page_registry import CUSTOM_PAGE_PREFIX, is_valid_custom_page_key


CUSTOM_PAGE_CALLBACK_PREFIX = "page:"


def is_custom_page_key(page_key: object) -> bool:
    """Checks the format of the user page key."""
    return is_valid_custom_page_key(page_key)


def build_page_callback(page_key: object) -> Optional[str]:
    """Return a callback for any non-empty page key that fits Telegram limits."""
    if not isinstance(page_key, str) or not page_key.strip():
        return None
    callback_data = f"{CUSTOM_PAGE_CALLBACK_PREFIX}{page_key.strip()}"
    try:
        return normalize_callback_data(callback_data)
    except ValueError:
        return None


def build_custom_page_callback(page_key: object) -> Optional[str]:
    """Returns a callback for a custom page if it fits within the Telegram limit."""
    if not is_custom_page_key(page_key):
        return None

    return build_page_callback(page_key)


def extract_page_key(callback_data: object) -> Optional[str]:
    """Extract any callback-safe page key from ``page:<page_key>``."""
    if not isinstance(callback_data, str):
        return None
    if not callback_data.startswith(CUSTOM_PAGE_CALLBACK_PREFIX):
        return None
    page_key = callback_data[len(CUSTOM_PAGE_CALLBACK_PREFIX):].strip()
    if not build_page_callback(page_key):
        return None
    return page_key


def extract_custom_page_key(callback_data: object) -> Optional[str]:
    """Retrieves and validates page_key from callback_data of the form page:custom_x."""
    page_key = extract_page_key(callback_data)
    if not is_custom_page_key(page_key):
        return None
    return page_key


def page_exists(page_key: object, *, warn_unknown: bool = True) -> bool:
    """Check an arbitrary page key through the shared runtime resolver."""
    if not isinstance(page_key, str) or not page_key.strip():
        return False
    from database.db_pages import resolve_page_row
    from database.requests import get_page

    normalized = page_key.strip()
    return resolve_page_row(
        normalized,
        get_page(normalized),
        warn_unknown=warn_unknown,
    ) is not None


def custom_page_exists(page_key: object) -> bool:
    """Checks that the custom page is valid and is in the pages table."""
    if not is_custom_page_key(page_key):
        return False

    return page_exists(page_key)


__all__ = [
    'CUSTOM_PAGE_CALLBACK_PREFIX',
    'CUSTOM_PAGE_PREFIX',
    'build_custom_page_callback',
    'build_page_callback',
    'custom_page_exists',
    'extract_custom_page_key',
    'extract_page_key',
    'is_custom_page_key',
    'page_exists',
]
