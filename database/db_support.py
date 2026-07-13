import logging
from typing import Any, Dict, List, Optional

from .connection import get_db

logger = logging.getLogger(__name__)

SUPPORT_CLEANUP_REMOVE_BUTTON = "remove_button"
SUPPORT_CLEANUP_DELETE_MESSAGE = "delete_message"
SUPPORT_CLEANUP_MODES = {
    SUPPORT_CLEANUP_REMOVE_BUTTON,
    SUPPORT_CLEANUP_DELETE_MESSAGE,
}
SUPPORT_CLEANUP_SETTING = "support_claim_cleanup_mode"

__all__ = [
    "SUPPORT_CLEANUP_REMOVE_BUTTON",
    "SUPPORT_CLEANUP_DELETE_MESSAGE",
    "SUPPORT_CLEANUP_MODES",
    "SUPPORT_CLEANUP_SETTING",
    "create_support_thread",
    "get_support_thread",
    "claim_support_thread",
    "release_support_thread_assignment",
    "record_support_message",
    "record_support_admin_notification",
    "get_support_admin_notifications",
    "mark_support_admin_notifications_inactive",
    "get_support_claim_cleanup_mode",
]


def create_support_thread(
    user_telegram_id: int,
    *,
    initiator_type: str,
    initiator_admin_id: Optional[int] = None,
    assigned_admin_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Creates a support chain for an existing user."""
    if initiator_type not in {"user", "admin"}:
        raise ValueError("initiator_type должен быть user или admin")

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, telegram_id FROM users WHERE telegram_id = ?",
            (user_telegram_id,),
        ).fetchone()
        if not user:
            return None

        cursor = conn.execute(
            """
            INSERT INTO support_threads (
                user_id, user_telegram_id, initiator_type,
                initiator_admin_id, assigned_admin_id,
                created_at, updated_at, last_message_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                user["id"],
                int(user_telegram_id),
                initiator_type,
                initiator_admin_id,
                assigned_admin_id,
            ),
        )
        thread_id = cursor.lastrowid

    return get_support_thread(thread_id)


def get_support_thread(thread_id: int) -> Optional[Dict[str, Any]]:
    """Returns the support chain by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM support_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def claim_support_thread(thread_id: int, admin_telegram_id: int) -> str:
    """
    Atomically assigns an unassigned chain to the admin.

    Returns:
        claimed, already_mine, assigned_other or not_found.
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE support_threads
            SET assigned_admin_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND assigned_admin_id IS NULL
            """,
            (admin_telegram_id, thread_id),
        )
        if cursor.rowcount > 0:
            return "claimed"

        row = conn.execute(
            "SELECT assigned_admin_id FROM support_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return "not_found"
        if row["assigned_admin_id"] == admin_telegram_id:
            return "already_mine"
        return "assigned_other"


def release_support_thread_assignment(thread_id: int, admin_telegram_id: int) -> bool:
    """Removes a pin if it is still owned by the specified admin."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE support_threads
            SET assigned_admin_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND assigned_admin_id = ?
            """,
            (thread_id, admin_telegram_id),
        )
        return cursor.rowcount > 0


def record_support_message(
    thread_id: int,
    *,
    sender_type: str,
    sender_telegram_id: int,
    recipient_telegram_id: Optional[int],
    text_html: str,
    media_type: Optional[str],
    media_file_id: Optional[str],
    source_chat_id: int,
    source_message_id: int,
) -> int:
    """Writes a message to the support log."""
    if sender_type not in {"user", "admin"}:
        raise ValueError("sender_type должен быть user или admin")

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO support_messages (
                thread_id, sender_type, sender_telegram_id, recipient_telegram_id,
                text_html, media_type, media_file_id,
                source_chat_id, source_message_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                thread_id,
                sender_type,
                sender_telegram_id,
                recipient_telegram_id,
                text_html or "",
                media_type,
                media_file_id,
                source_chat_id,
                source_message_id,
            ),
        )
        conn.execute(
            """
            UPDATE support_threads
            SET updated_at = CURRENT_TIMESTAMP, last_message_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (thread_id,),
        )
        return int(cursor.lastrowid)


def record_support_admin_notification(
    thread_id: int,
    admin_telegram_id: int,
    *,
    card_message_id: Optional[int],
    copy_message_id: Optional[int],
) -> int:
    """Saves messages sent to the admin via an unpinned thread."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO support_admin_notifications (
                thread_id, admin_telegram_id, card_message_id, copy_message_id,
                is_active, created_at
            )
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (thread_id, admin_telegram_id, card_message_id, copy_message_id),
        )
        return int(cursor.lastrowid)


def get_support_admin_notifications(
    thread_id: int,
    *,
    exclude_admin_id: Optional[int] = None,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """Returns admin notifications for the thread."""
    query = "SELECT * FROM support_admin_notifications WHERE thread_id = ?"
    params: List[Any] = [thread_id]
    if active_only:
        query += " AND is_active = 1"
    if exclude_admin_id is not None:
        query += " AND admin_telegram_id != ?"
        params.append(exclude_admin_id)
    query += " ORDER BY id"

    with get_db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def mark_support_admin_notifications_inactive(thread_id: int, admin_telegram_ids: List[int]) -> int:
    """Marks notifications from the specified admins as inactive."""
    if not admin_telegram_ids:
        return 0

    placeholders = ", ".join("?" for _ in admin_telegram_ids)
    params: List[Any] = [thread_id, *admin_telegram_ids]
    with get_db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE support_admin_notifications
            SET is_active = 0
            WHERE thread_id = ? AND admin_telegram_id IN ({placeholders})
            """,
            params,
        )
        return int(cursor.rowcount)


def get_support_claim_cleanup_mode() -> str:
    """Returns the mode for clearing notifications for admins after taking the chain."""
    from database.db_settings import get_setting

    value = get_setting(SUPPORT_CLEANUP_SETTING, SUPPORT_CLEANUP_REMOVE_BUTTON)
    if value not in SUPPORT_CLEANUP_MODES:
        logger.warning(
            "Неизвестный support_claim_cleanup_mode=%s, используется %s",
            value,
            SUPPORT_CLEANUP_REMOVE_BUTTON,
        )
        return SUPPORT_CLEANUP_REMOVE_BUTTON
    return value
