"""Persistence for the built-in support conversation system."""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import nullcontext
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
_SUPPORT_TICKET_STATUS_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")


class SupportThreadNotFoundError(LookupError):
    """Raised when a support message targets a missing thread."""


class SupportThreadClosedError(RuntimeError):
    """Raised when a message targets a closed support thread."""

__all__ = [
    "SUPPORT_CLEANUP_REMOVE_BUTTON",
    "SUPPORT_CLEANUP_DELETE_MESSAGE",
    "SUPPORT_CLEANUP_MODES",
    "SUPPORT_CLEANUP_SETTING",
    "SupportThreadNotFoundError",
    "SupportThreadClosedError",
    "normalize_support_ticket_status",
    "create_support_thread",
    "create_extension_support_ticket",
    "get_extension_support_ticket",
    "get_support_thread",
    "list_support_ticket_sessions",
    "get_support_ticket_history",
    "set_support_thread_status",
    "claim_support_thread",
    "release_support_thread_assignment",
    "record_support_message",
    "set_support_message_delivery",
    "record_support_admin_notification",
    "save_support_admin_notification_delivery",
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
    """Creates a support thread for an existing user."""
    if initiator_type not in {"user", "admin"}:
        raise ValueError("initiator_type must be user or admin")

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
                status, created_at, updated_at, last_message_at
            )
            VALUES (?, ?, ?, ?, ?, 'open',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                user["id"],
                int(user_telegram_id),
                initiator_type,
                initiator_admin_id,
                assigned_admin_id,
            ),
        )
        thread_id = int(cursor.lastrowid)

    return get_support_thread(thread_id)


def create_extension_support_ticket(
    target_user_id: int,
    *,
    direction: str,
    extension_id: str,
    idempotency_key: str,
    text_html: str,
) -> Optional[Dict[str, Any]]:
    """Atomically creates one extension-origin support thread and message."""
    if direction not in {"inbound", "outbound"}:
        raise ValueError("direction must be inbound or outbound")
    if (
        not isinstance(target_user_id, int)
        or isinstance(target_user_id, bool)
        or target_user_id <= 0
    ):
        raise ValueError("target_user_id must be a positive integer")
    extension_id = _required_text(extension_id, "extension_id")
    idempotency_key = _required_text(idempotency_key, "idempotency_key")
    text_html = _required_text(text_html, "text_html")

    try:
        with get_db() as conn:
            existing = _get_extension_support_ticket_in_connection(
                conn,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
            )
            if existing:
                existing["already_created"] = True
                return existing

            user = conn.execute(
                """
                SELECT id, telegram_id, username, first_name, last_name
                FROM users
                WHERE id = ?
                """,
                (target_user_id,),
            ).fetchone()
            if not user:
                return None

            initiator_type = "user" if direction == "inbound" else "admin"
            thread_cursor = conn.execute(
                """
                INSERT INTO support_threads (
                    user_id, user_telegram_id, initiator_type,
                    initiator_admin_id, assigned_admin_id,
                    status, created_at, updated_at, last_message_at
                )
                VALUES (?, ?, ?, NULL, NULL, 'open',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (target_user_id, user["telegram_id"], initiator_type),
            )
            thread_id = int(thread_cursor.lastrowid)
            if user['telegram_id'] is None:
                conn.execute("UPDATE support_threads SET channel='web' WHERE id=?", (thread_id,))
            sender_type = "user" if direction == "inbound" else "admin"
            sender_telegram_id = user["telegram_id"] if direction == "inbound" else None
            recipient_telegram_id = user["telegram_id"] if direction == "outbound" else None
            conn.execute(
                """
                INSERT INTO support_messages (
                    thread_id, sender_type, sender_telegram_id,
                    recipient_telegram_id, text_html, media_type, media_file_id,
                    source_chat_id, source_message_id, origin_type,
                    origin_extension_id, origin_operation_key,
                    delivered_message_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'text', NULL, NULL, NULL,
                        'extension', ?, ?, NULL, CURRENT_TIMESTAMP)
                """,
                (
                    thread_id,
                    sender_type,
                    sender_telegram_id,
                    recipient_telegram_id,
                    text_html,
                    extension_id,
                    idempotency_key,
                ),
            )
            created = _get_extension_support_ticket_in_connection(
                conn,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
            )
            if created is None:
                raise RuntimeError("created support ticket could not be reloaded")
            created["already_created"] = False
            return created
    except sqlite3.IntegrityError:
        existing = get_extension_support_ticket(
            extension_id=extension_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            existing["already_created"] = True
            return existing
        raise


def get_extension_support_ticket(
    *,
    extension_id: str,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    """Returns the ticket linked to one extension idempotency key."""
    with get_db() as conn:
        return _get_extension_support_ticket_in_connection(
            conn,
            extension_id=_required_text(extension_id, "extension_id"),
            idempotency_key=_required_text(idempotency_key, "idempotency_key"),
        )


def _get_extension_support_ticket_in_connection(
    conn: sqlite3.Connection,
    *,
    extension_id: str,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    message = conn.execute(
        """
        SELECT *
        FROM support_messages
        WHERE origin_type = 'extension'
          AND origin_extension_id = ?
          AND origin_operation_key = ?
        LIMIT 1
        """,
        (extension_id, idempotency_key),
    ).fetchone()
    if not message:
        return None
    thread = conn.execute(
        "SELECT * FROM support_threads WHERE id = ?",
        (int(message["thread_id"]),),
    ).fetchone()
    if not thread:
        return None
    user = conn.execute(
        """
        SELECT id, telegram_id, username, first_name, last_name
        FROM users
        WHERE id = ?
        """,
        (int(thread["user_id"]),),
    ).fetchone()
    if not user:
        return None
    return {
        "thread": dict(thread),
        "message": dict(message),
        "user": dict(user),
    }


def get_support_thread(thread_id: int) -> Optional[Dict[str, Any]]:
    """Returns a support thread by id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM support_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def list_support_ticket_sessions(
    *,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    telegram_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """Returns one activity-ordered page of support threads and its total."""
    if user_id is not None and telegram_id is not None:
        raise ValueError("user_id and telegram_id are mutually exclusive")

    filters: List[str] = []
    params: List[Any] = []
    if status is not None:
        filters.append("st.status = ?")
        params.append(status)
    if user_id is not None:
        filters.append("st.user_id = ?")
        params.append(user_id)
    if telegram_id is not None:
        filters.append("st.user_telegram_id = ?")
        params.append(telegram_id)
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    with get_db() as conn:
        conn.execute("BEGIN")
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM support_threads st {where_sql}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT
                st.*,
                u.username AS user_username,
                u.first_name AS user_first_name,
                u.last_name AS user_last_name,
                (
                    SELECT COUNT(*)
                    FROM support_messages sm
                    WHERE sm.thread_id = st.id
                ) AS message_count
            FROM support_threads st
            LEFT JOIN users u ON u.id = st.user_id
            {where_sql}
            ORDER BY st.last_message_at DESC, st.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total_row["total"]) if total_row else 0,
        }


def get_support_ticket_history(
    thread_id: int,
    *,
    limit: int = 50,
    before_message_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Returns raw thread metadata and a newest-first history window."""
    with get_db() as conn:
        conn.execute("BEGIN")
        thread = conn.execute(
            """
            SELECT
                st.*,
                u.username AS user_username,
                u.first_name AS user_first_name,
                u.last_name AS user_last_name,
                (
                    SELECT COUNT(*)
                    FROM support_messages sm
                    WHERE sm.thread_id = st.id
                ) AS message_count
            FROM support_threads st
            LEFT JOIN users u ON u.id = st.user_id
            WHERE st.id = ?
            """,
            (thread_id,),
        ).fetchone()
        if not thread:
            return None

        message_params: List[Any] = [thread_id]
        before_sql = ""
        if before_message_id is not None:
            before_sql = "AND id < ?"
            message_params.append(before_message_id)
        message_params.append(limit + 1)
        messages = conn.execute(
            f"""
            SELECT
                id, sender_type, sender_telegram_id,
                text_html, media_type, media_file_id,
                origin_type, origin_extension_id, created_at
            FROM support_messages
            WHERE thread_id = ? {before_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            message_params,
        ).fetchall()
        return {
            "thread": dict(thread),
            "messages": [dict(row) for row in messages],
        }


def set_support_thread_status(thread_id: int, status: str) -> Optional[Dict[str, Any]]:
    """Atomically changes a thread status and returns its previous value."""
    status = normalize_support_ticket_status(status)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT user_id, status FROM support_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return None
        previous_status = str(row["status"])
        if previous_status != status:
            conn.execute(
                """
                UPDATE support_threads
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, thread_id),
            )
        return {
            "thread_id": int(thread_id),
            "user_id": int(row["user_id"]),
            "previous_status": previous_status,
            "status": status,
            "changed": previous_status != status,
        }


def claim_support_thread(thread_id: int, admin_telegram_id: int) -> str:
    """Atomically assigns an unassigned support thread to an admin."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE support_threads
            SET assigned_admin_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND assigned_admin_id IS NULL AND status != 'closed'
            """,
            (admin_telegram_id, thread_id),
        )
        if cursor.rowcount > 0:
            return "claimed"

        row = conn.execute(
            "SELECT assigned_admin_id, status FROM support_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return "not_found"
        if row["status"] == "closed":
            return "closed"
        if row["assigned_admin_id"] == admin_telegram_id:
            return "already_mine"
        return "assigned_other"


def release_support_thread_assignment(thread_id: int, admin_telegram_id: int) -> bool:
    """Removes an assignment if it is still owned by the specified admin."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE support_threads
            SET assigned_admin_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND assigned_admin_id = ? AND status != 'closed'
            """,
            (thread_id, admin_telegram_id),
        )
        return cursor.rowcount > 0


def record_support_message(
    thread_id: int,
    *,
    sender_type: str,
    sender_telegram_id: Optional[int],
    recipient_telegram_id: Optional[int],
    text_html: str,
    media_type: Optional[str],
    media_file_id: Optional[str],
    source_chat_id: Optional[int],
    source_message_id: Optional[int],
    origin_type: str = "telegram",
    origin_extension_id: Optional[str] = None,
    origin_operation_key: Optional[str] = None,
    delivered_message_id: Optional[int] = None,
    _conn=None,
) -> int:
    """Writes a message to the support log."""
    if sender_type not in {"user", "admin"}:
        raise ValueError("sender_type must be user or admin")
    if origin_type not in {"telegram", "extension"}:
        raise ValueError("origin_type must be telegram or extension")

    with nullcontext(_conn) if _conn is not None else get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO support_messages (
                thread_id, sender_type, sender_telegram_id, recipient_telegram_id,
                text_html, media_type, media_file_id,
                source_chat_id, source_message_id, origin_type,
                origin_extension_id, origin_operation_key,
                delivered_message_id, created_at
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
            FROM support_threads
            WHERE id = ? AND status != 'closed'
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
                origin_type,
                origin_extension_id,
                origin_operation_key,
                delivered_message_id,
                thread_id,
            ),
        )
        if cursor.rowcount <= 0:
            thread = conn.execute(
                "SELECT status FROM support_threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if not thread:
                raise SupportThreadNotFoundError(
                    f"support thread {thread_id} does not exist"
                )
            raise SupportThreadClosedError(
                f"support thread {thread_id} is closed"
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


def set_support_message_delivery(message_id: int, delivered_message_id: int) -> Optional[int]:
    """Stores the outbound Telegram message id without replacing an earlier delivery."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE support_messages
            SET delivered_message_id = COALESCE(delivered_message_id, ?)
            WHERE id = ?
            """,
            (int(delivered_message_id), int(message_id)),
        )
        row = conn.execute(
            "SELECT delivered_message_id FROM support_messages WHERE id = ?",
            (int(message_id),),
        ).fetchone()
        if not row or row["delivered_message_id"] is None:
            return None
        return int(row["delivered_message_id"])


def record_support_admin_notification(
    thread_id: int,
    admin_telegram_id: int,
    *,
    card_message_id: Optional[int],
    copy_message_id: Optional[int],
    support_message_id: Optional[int] = None,
) -> int:
    """Saves messages sent to an admin for an unassigned thread."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO support_admin_notifications (
                thread_id, admin_telegram_id, card_message_id, copy_message_id,
                is_active, created_at, support_message_id
            )
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
            """,
            (
                thread_id,
                admin_telegram_id,
                card_message_id,
                copy_message_id,
                support_message_id,
            ),
        )
        return int(cursor.lastrowid)


def save_support_admin_notification_delivery(
    thread_id: int,
    admin_telegram_id: int,
    *,
    support_message_id: int,
    card_message_id: Optional[int] = None,
    copy_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Creates or completes delivery state for one generated support message."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO support_admin_notifications (
                thread_id, admin_telegram_id, card_message_id,
                copy_message_id, is_active, created_at, support_message_id
            )
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
            """,
            (
                thread_id,
                admin_telegram_id,
                card_message_id,
                copy_message_id,
                support_message_id,
            ),
        )
        conn.execute(
            """
            UPDATE support_admin_notifications
            SET card_message_id = COALESCE(card_message_id, ?),
                copy_message_id = COALESCE(copy_message_id, ?)
            WHERE support_message_id = ? AND admin_telegram_id = ?
            """,
            (card_message_id, copy_message_id, support_message_id, admin_telegram_id),
        )
        saved = conn.execute(
            """
            SELECT * FROM support_admin_notifications
            WHERE support_message_id = ? AND admin_telegram_id = ?
            """,
            (support_message_id, admin_telegram_id),
        ).fetchone()
        if saved is None:
            raise RuntimeError("generated support delivery state could not be saved")
        return dict(saved)


def get_support_admin_notifications(
    thread_id: int,
    *,
    exclude_admin_id: Optional[int] = None,
    active_only: bool = True,
    support_message_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Returns recorded admin deliveries for a support thread."""
    query = "SELECT * FROM support_admin_notifications WHERE thread_id = ?"
    params: List[Any] = [thread_id]
    if active_only:
        query += " AND is_active = 1"
    if exclude_admin_id is not None:
        query += " AND admin_telegram_id != ?"
        params.append(exclude_admin_id)
    if support_message_id is not None:
        query += " AND support_message_id = ?"
        params.append(support_message_id)
    query += " ORDER BY id"

    with get_db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def mark_support_admin_notifications_inactive(thread_id: int, admin_telegram_ids: List[int]) -> int:
    """Marks notifications for the specified admins as inactive."""
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
    """Returns the configured cleanup mode for unassigned admin cards."""
    from database.db_settings import get_setting

    value = get_setting(SUPPORT_CLEANUP_SETTING, SUPPORT_CLEANUP_REMOVE_BUTTON)
    if value not in SUPPORT_CLEANUP_MODES:
        logger.warning(
            "Unknown support_claim_cleanup_mode=%s; using %s",
            value,
            SUPPORT_CLEANUP_REMOVE_BUTTON,
        )
        return SUPPORT_CLEANUP_REMOVE_BUTTON
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def normalize_support_ticket_status(value: Any) -> str:
    """Normalizes one newly written support workflow status."""
    if not isinstance(value, str):
        raise ValueError("status must be a string")
    status = value.strip().casefold()
    if not _SUPPORT_TICKET_STATUS_RE.fullmatch(status):
        raise ValueError(
            "status must match ^[a-z][a-z0-9_.-]{0,31}$ after normalization"
        )
    return status
