"""Custom page builder routes."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .db_page_flow import normalize_registry_names
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_page_route',
    'page_route_exists',
    'upsert_page_route',
]


_ROUTE_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,48}$')


def _is_route_key(route_key: object) -> bool:
    return isinstance(route_key, str) and bool(_ROUTE_KEY_RE.fullmatch(route_key))


def get_page_route(route_key: str) -> Optional[dict[str, Any]]:
    """Returns route configuration from the database."""
    if not _is_route_key(route_key):
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM page_routes WHERE route_key = ?",
            (route_key,),
        ).fetchone()
        return dict(row) if row else None


def page_route_exists(route_key: str) -> bool:
    """Checks if route is enabled."""
    route = get_page_route(route_key)
    return bool(route and route.get('is_enabled'))


def upsert_page_route(
    route_key: str,
    page_key: str,
    *,
    guard_names: list[str] | tuple[str, ...] | str | None = None,
    hook_names: list[str] | tuple[str, ...] | str | None = None,
    is_enabled: bool = True,
) -> None:
    """Creates or updates a page builder route."""
    if not _is_route_key(route_key):
        raise ValueError(f"Некорректный route_key: {route_key!r}")

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO page_routes (
                route_key, page_key, guard_names, hook_names, is_enabled, updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(route_key) DO UPDATE SET
                page_key = excluded.page_key,
                guard_names = excluded.guard_names,
                hook_names = excluded.hook_names,
                is_enabled = excluded.is_enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                route_key,
                page_key,
                normalize_registry_names(guard_names),
                normalize_registry_names(hook_names),
                1 if is_enabled else 0,
            ),
        )
    logger.info("Маршрут страницы обновлён: %s -> %s", route_key, page_key)
