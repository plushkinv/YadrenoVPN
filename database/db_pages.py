"""
Module for working with user pages.

The pages table stores the text, media, and buttons for each screen.
Buttons are stored in two JSON fields:
  - buttons_default — developer defaults (updated only by migrations)
  - buttons_custom — admin customization (updated via the admin panel)
*_default functions are called ONLY from migrations.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any
from .db_page_flow import normalize_registry_names
from .connection import get_db
from .page_button_styles import validate_page_item_colors
from .page_registry import (
    CORE_PAGE_KEYS,
    PAGE_KIND_CORE,
    PAGE_KIND_CUSTOM,
    PAGE_KIND_LEGACY_CUSTOM,
    PAGE_KIND_UNKNOWN,
    PAGE_KINDS,
    PageClassification,
    classify_page,
    is_valid_custom_page_key,
)

logger = logging.getLogger(__name__)

__all__ = [
    'get_page',
    'get_page_keys',
    'get_pages',
    'get_page_classification',
    'get_page_classification_diagnostics',
    'get_page_kind_counts',
    'is_page_render_allowed',
    'resolve_page_row',
    'resolve_renderable_page',
    'create_custom_page',
    'apply_page_custom_patch',
    'normalize_page_custom_patch',
    'update_page_custom',
    'update_page_flow',
    'upsert_page_defaults',
]

PAGE_CUSTOM_FIELDS = frozenset({
    'text_custom',
    'image_custom',
    'media_type_custom',
    'buttons_custom',
})


def get_page_keys() -> frozenset[str]:
    """Return every stored page key for startup integrity validation."""
    with get_db() as conn:
        rows = conn.execute("SELECT page_key FROM pages").fetchall()
    return frozenset(str(row['page_key']) for row in rows)


def get_pages(*, kind: str | None = None) -> list[Dict[str, Any]]:
    """Return stored page rows, optionally filtered by persisted page_kind."""
    if kind is not None and kind not in PAGE_KINDS:
        raise ValueError(f'unsupported page kind: {kind}')
    query = "SELECT * FROM pages"
    params: tuple[object, ...] = ()
    if kind is not None:
        query += " WHERE page_kind = ?"
        params = (kind,)
    query += " ORDER BY page_key"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_page(page_key: str) -> Optional[Dict[str, Any]]:
    """
    Returns page data from the pages table.

    Args:
        page_key: Page key

    Returns:
        Dictionary with table fields or None if page not found
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM pages WHERE page_key = ?",
            (page_key,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _classification_for_row(
    page_key: str,
    row: Optional[Dict[str, Any]],
) -> PageClassification:
    if row is None:
        return classify_page(page_key, PAGE_KIND_UNKNOWN, exists=False)
    return classify_page(
        str(row.get('page_key') or page_key),
        row.get('page_kind'),
        exists=True,
    )


def get_page_classification(page_key: str) -> PageClassification:
    """Return one page classification without producing runtime log entries."""
    normalized = str(page_key).strip()
    return _classification_for_row(normalized, get_page(normalized))


def resolve_page_row(
    page_key: str,
    row: Optional[Dict[str, Any]],
    *,
    warn_unknown: bool = True,
) -> Optional[Dict[str, Any]]:
    """Apply the shared resolver to an already fetched page row."""
    normalized = str(page_key).strip()
    classification = _classification_for_row(normalized, row)
    if classification.render_allowed:
        return row
    if warn_unknown:
        logger.warning(
            "Page render rejected: page_key=%r page_kind=%s reason=%s",
            normalized,
            classification.page_kind,
            classification.reason,
        )
    return None


def resolve_renderable_page(
    page_key: str,
    *,
    warn_unknown: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return an allowed stored page and warn for every rejected runtime key."""
    normalized = str(page_key).strip()
    return resolve_page_row(
        normalized,
        get_page(normalized),
        warn_unknown=warn_unknown,
    )


def is_page_render_allowed(page_key: str, *, warn_unknown: bool = True) -> bool:
    """Return whether a stored page passes the shared runtime resolver."""
    return resolve_renderable_page(
        page_key,
        warn_unknown=warn_unknown,
    ) is not None


def get_page_kind_counts() -> dict[str, int]:
    """Return persisted counts for all four page kinds without runtime logs."""
    counts = {kind: 0 for kind in sorted(PAGE_KINDS)}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT page_kind, COUNT(*) AS count FROM pages GROUP BY page_kind"
        ).fetchall()
    for row in rows:
        kind = str(row['page_kind'])
        target_kind = kind if kind in PAGE_KINDS else PAGE_KIND_UNKNOWN
        counts[target_kind] += int(row['count'])
    return counts


def get_page_classification_diagnostics(*, limit: int = 10) -> dict[str, Any]:
    """Return a bounded static page-classification snapshot without logging."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('limit must be a non-negative integer')
    counts = get_page_kind_counts()
    with get_db() as conn:
        legacy = [
            str(row['page_key'])
            for row in conn.execute(
                "SELECT page_key FROM pages WHERE page_kind = 'legacy_custom' "
                "ORDER BY page_key LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        unknown = [
            str(row['page_key'])
            for row in conn.execute(
                "SELECT page_key FROM pages WHERE page_kind = 'unknown' "
                "ORDER BY page_key LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    return {
        'counts': counts,
        'legacy': legacy,
        'legacy_total': counts[PAGE_KIND_LEGACY_CUSTOM],
        'unknown': unknown,
        'unknown_total': counts[PAGE_KIND_UNKNOWN],
        'limit': limit,
    }


def create_custom_page(
    page_key: str,
    *,
    text: str,
    image: Optional[str],
    media_type: Optional[str],
    buttons: list[dict[str, Any]],
    guard_names: list[str],
    hook_names: list[str],
) -> bool:
    """Insert one validated custom page without overwriting an existing row."""
    if not is_valid_custom_page_key(page_key) or page_key in CORE_PAGE_KEYS:
        raise ValueError('custom page_key must be an unreserved custom_* key')
    validate_page_item_colors(buttons)
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pages (
                page_key, page_kind, text_default, image_default,
                media_type_default, buttons_default, guard_names, hook_names
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_key,
                PAGE_KIND_CUSTOM,
                text,
                image,
                media_type,
                json.dumps(buttons, ensure_ascii=False, separators=(',', ':')),
                normalize_registry_names(guard_names),
                normalize_registry_names(hook_names),
            ),
        )
    return cursor.rowcount > 0


def normalize_page_custom_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate a page override patch and convert values to DB storage form."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError('page patch must be a non-empty object')
    unknown = sorted(set(patch) - PAGE_CUSTOM_FIELDS)
    if unknown:
        raise ValueError(f"page patch contains protected or unknown fields: {unknown}")

    normalized: dict[str, Any] = {}
    for field, value in patch.items():
        if field in {'text_custom', 'image_custom'}:
            if value is not None and not isinstance(value, str):
                raise TypeError(f'{field} must be a string or null')
            normalized[field] = value
        elif field == 'media_type_custom':
            if value not in {None, 'photo', 'video', 'animation'}:
                raise ValueError(
                    'media_type_custom must be photo, video, animation, or null'
                )
            normalized[field] = value
        else:
            if value is not None and not isinstance(value, list):
                raise TypeError('buttons_custom must be an array or null')
            if isinstance(value, list) and any(
                not isinstance(item, dict) for item in value
            ):
                raise TypeError('buttons_custom items must be objects')
            validate_page_item_colors(value)
            normalized[field] = (
                None
                if value is None
                else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            )
    return normalized


def apply_page_custom_patch(page_key: str, patch: dict[str, Any]) -> bool:
    """Atomically update only administrator-owned page override columns."""
    normalized = normalize_page_custom_patch(patch)

    with get_db() as conn:
        row = conn.execute(
            "SELECT page_key FROM pages WHERE page_key = ?",
            (str(page_key),),
        ).fetchone()
        if row is None:
            raise KeyError(f'unknown page_key: {page_key}')
        assignments = [f'{field} = ?' for field in normalized]
        values = [normalized[field] for field in normalized]
        values.append(str(page_key))
        cursor = conn.execute(
            f"UPDATE pages SET {', '.join(assignments)}, "
            "updated_at = CURRENT_TIMESTAMP WHERE page_key = ?",
            values,
        )
    logger.info("Custom page patch applied: %s", page_key)
    return cursor.rowcount > 0


def update_page_custom(
    page_key: str,
    text: Optional[str] = None,
    image: Optional[str] = None,
    media_type: Optional[str] = None,
    buttons: Optional[str] = None,
) -> None:
    """
    Updates custom page fields.
    DOES NOT touch *_default fields.

    Args:
        page_key: Page key
        text: Custom text (None = do not change)
        image: Custom image file_id (None = do not change)
        buttons: Custom JSON buttons (None = do not change)
    """
    # We collect only the transmitted fields
    updates = []
    params = []
    if text is not None:
        updates.append("text_custom = ?")
        params.append(text)
    if image is not None:
        updates.append("image_custom = ?")
        params.append(image)
        if image:
            updates.append("media_type_custom = ?")
            params.append(media_type if media_type in {'photo', 'video', 'animation'} else 'photo')
        else:
            updates.append("media_type_custom = NULL")
    if buttons is not None:
        try:
            parsed_buttons = json.loads(buttons)
        except (TypeError, ValueError):
            parsed_buttons = None
        validate_page_item_colors(parsed_buttons)
        updates.append("buttons_custom = ?")
        params.append(buttons)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(page_key)

    with get_db() as conn:
        conn.execute(
            f"UPDATE pages SET {', '.join(updates)} WHERE page_key = ?",
            params
        )
    logger.info(f"Кастомные данные страницы обновлены: {page_key}")


def update_page_flow(
    page_key: str,
    guard_names: list[str] | tuple[str, ...] | str | None = None,
    hook_names: list[str] | tuple[str, ...] | str | None = None,
) -> None:
    """
    Updates page-level guards/hooks for direct transitions page:<custom_*>
    and route transitions to this page.

    None means "don't change the field"; to clear, pass [] or '[]'.
    """
    updates = []
    params = []
    if guard_names is not None:
        updates.append("guard_names = ?")
        params.append(normalize_registry_names(guard_names))
    if hook_names is not None:
        updates.append("hook_names = ?")
        params.append(normalize_registry_names(hook_names))

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(page_key)

    with get_db() as conn:
        conn.execute(
            f"UPDATE pages SET {', '.join(updates)} WHERE page_key = ?",
            params,
        )
    logger.info("Flow-настройки страницы обновлены: %s", page_key)


def upsert_page_defaults(
    page_key: str,
    text: str,
    image: Optional[str],
    buttons: str,
    media_type: Optional[str] = None,
) -> None:
    """
    Inserts or updates ONLY default page fields.
    Called EXCLUSIVELY from migrations!
    NEVER touches *_custom fields.

    Args:
        page_key: Page key
        text: Default text (HTML)
        image: Default media file_id (or None)
        media_type: Default media type: photo, video or animation
        buttons: JSON string of button array
    """
    normalized_media_type = media_type if media_type in {'photo', 'video', 'animation'} else None
    if image and normalized_media_type is None:
        normalized_media_type = 'photo'

    with get_db() as conn:
        columns = {
            str(row['name'])
            for row in conn.execute("PRAGMA table_info(pages)").fetchall()
        }
        if 'page_kind' in columns:
            if page_key in CORE_PAGE_KEYS:
                page_kind = PAGE_KIND_CORE
            elif is_valid_custom_page_key(page_key):
                page_kind = PAGE_KIND_CUSTOM
            else:
                page_kind = PAGE_KIND_UNKNOWN
            conn.execute(
                """
                INSERT OR IGNORE INTO pages (
                    page_key, page_kind, text_default, image_default,
                    media_type_default, buttons_default
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (page_key, page_kind, text, image, normalized_media_type, buttons),
            )
        else:
            # Older migrations run before v105 introduces page_kind.
            conn.execute(
                """
                INSERT OR IGNORE INTO pages (
                    page_key, text_default, image_default,
                    media_type_default, buttons_default
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (page_key, text, image, normalized_media_type, buttons),
            )
        # Update *_default fields (for existing records)
        conn.execute(
            """
            UPDATE pages
            SET text_default    = ?,
                image_default   = ?,
                media_type_default = ?,
                buttons_default = ?
            WHERE page_key = ?
            """,
            (text, image, normalized_media_type, buttons, page_key)
        )
    logger.info(f"Дефолты страницы обновлены: {page_key}")
