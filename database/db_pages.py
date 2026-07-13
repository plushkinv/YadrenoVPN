"""
Module for working with user pages.

The pages table stores the text, media, and buttons for each screen.
Buttons are stored in two JSON fields:
  - buttons_default — developer defaults (updated only by migrations)
  - buttons_custom — admin customization (updated via the admin panel)
*_default functions are called ONLY from migrations.
"""
import json
import logging
from typing import Optional, List, Dict, Any
from .db_page_flow import normalize_registry_names
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_page',
    'update_page_custom',
    'update_page_flow',
    'upsert_page_defaults',
]


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
        # Trying to insert a new record
        conn.execute(
            """
            INSERT OR IGNORE INTO pages (page_key, text_default, image_default, media_type_default, buttons_default)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_key, text, image, normalized_media_type, buttons)
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
