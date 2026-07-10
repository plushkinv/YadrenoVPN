"""Правила data-driven маршрутов конструктора страниц."""
from __future__ import annotations

import re
from typing import Optional

from bot.utils.action_registry import normalize_callback_data


PAGE_ROUTE_CALLBACK_PREFIX = 'route:'

_ROUTE_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,48}$')


def is_page_route_key(route_key: object) -> bool:
    """Проверяет формат ключа маршрута."""
    return isinstance(route_key, str) and bool(_ROUTE_KEY_RE.fullmatch(route_key))


def build_page_route_callback(route_key: object) -> Optional[str]:
    """Возвращает callback для route, если он валиден и помещается в лимит Telegram."""
    if not is_page_route_key(route_key):
        return None

    callback_data = f'{PAGE_ROUTE_CALLBACK_PREFIX}{route_key}'
    try:
        return normalize_callback_data(callback_data)
    except ValueError:
        return None


def extract_page_route_key(callback_data: object) -> Optional[str]:
    """Извлекает route_key из callback_data вида `route:<route_key>`."""
    if not isinstance(callback_data, str):
        return None
    if not callback_data.startswith(PAGE_ROUTE_CALLBACK_PREFIX):
        return None

    route_key = callback_data[len(PAGE_ROUTE_CALLBACK_PREFIX):]
    if not build_page_route_callback(route_key):
        return None
    return route_key


def page_route_exists(route_key: object) -> bool:
    """Проверяет, что route валиден, есть в БД и включён."""
    if not is_page_route_key(route_key):
        return False

    from database.requests import page_route_exists as db_page_route_exists

    return db_page_route_exists(str(route_key))
