"""Core-facade operations that create built-in support tickets."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

logger = logging.getLogger(__name__)

_OPERATION_DIRECTIONS = {
    'create_current_user_support_ticket': 'inbound',
    'create_outbound_support_ticket': 'outbound',
}


@dataclass
class _TicketOperationLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_TICKET_OPERATION_LOCKS: dict[tuple[str, str], _TicketOperationLock] = {}


def list_extension_support_ticket_sessions(
    *,
    status: str | None,
    user_id: int | None,
    telegram_id: int | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Builds a safe local facade page of support ticket sessions."""
    from database.requests import list_support_ticket_sessions

    page = list_support_ticket_sessions(
        status=status,
        user_id=user_id,
        telegram_id=telegram_id,
        limit=limit,
        offset=offset,
    )
    total = int(page["total"])
    items = [_public_thread(item) for item in page["items"]]
    return {
        "contract_version": 1,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


def get_extension_support_ticket_history(
    *,
    thread_id: int,
    limit: int,
    before_message_id: int | None,
) -> dict[str, Any] | None:
    """Builds a safe chronological history page for one local ticket."""
    from database.requests import get_support_ticket_history

    history = get_support_ticket_history(
        thread_id,
        limit=limit,
        before_message_id=before_message_id,
    )
    if history is None:
        return None
    newest_first = list(history["messages"])
    has_more = len(newest_first) > limit
    page = newest_first[:limit]
    messages = [_public_message(item) for item in reversed(page)]
    return {
        "contract_version": 1,
        "session": _public_thread(history["thread"]),
        "messages": messages,
        "limit": limit,
        "has_more": has_more,
        "next_before_message_id": int(page[-1]["id"]) if has_more and page else None,
    }


async def set_extension_support_ticket_status(
    *,
    extension_id: str,
    idempotency_key: str,
    thread_id: int,
    status: str,
    actor_telegram_id: int,
) -> dict[str, Any]:
    """Changes one ticket status under facade idempotency and thread locks."""
    operation = "set_support_ticket_status"
    lock_key = (str(extension_id).strip().casefold(), str(idempotency_key).strip())
    lock_entry = _TICKET_OPERATION_LOCKS.setdefault(lock_key, _TicketOperationLock())
    lock_entry.users += 1
    try:
        async with lock_entry.lock:
            return await _set_extension_support_ticket_status_locked(
                extension_id=extension_id,
                idempotency_key=idempotency_key,
                operation=operation,
                thread_id=thread_id,
                status=status,
                actor_telegram_id=actor_telegram_id,
            )
    finally:
        lock_entry.users -= 1
        if lock_entry.users == 0 and _TICKET_OPERATION_LOCKS.get(lock_key) is lock_entry:
            _TICKET_OPERATION_LOCKS.pop(lock_key, None)


async def _set_extension_support_ticket_status_locked(
    *,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    thread_id: int,
    status: str,
    actor_telegram_id: int,
) -> dict[str, Any]:
    from database.requests import (
        build_extension_core_request_fingerprint,
        claim_extension_core_operation,
        finalize_extension_core_operation,
        set_support_thread_status,
    )

    fingerprint = build_extension_core_request_fingerprint(
        operation=operation,
        target_user_id=None,
        payload={"thread_id": thread_id, "ticket_status": status},
    )
    claimed = claim_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=idempotency_key,
        operation=operation,
        target_user_id=None,
        amount=None,
        reason=None,
        request_fingerprint=fingerprint,
    )
    if claimed.get("status") == "idempotency_conflict":
        return _public_status_result(claimed)
    if not claimed.get("claimed"):
        return _public_status_result(claimed, force_already_applied=True)

    from bot.services.support import support_thread_operation

    try:
        async with support_thread_operation(thread_id):
            changed = set_support_thread_status(thread_id, status)
    except Exception as exc:
        logger.exception(
            "Extension support status operation %s:%s failed: %s",
            claimed.get("extension_id"),
            claimed.get("idempotency_key"),
            exc,
        )
        failed = finalize_extension_core_operation(
            extension_id=str(claimed["extension_id"]),
            idempotency_key=str(claimed["idempotency_key"]),
            status="failed",
            metadata={
                "ok": False,
                "status": "failed",
                "thread_id": thread_id,
            },
        )
        return _public_status_result(failed)
    if changed is None:
        finalized = finalize_extension_core_operation(
            extension_id=str(claimed["extension_id"]),
            idempotency_key=str(claimed["idempotency_key"]),
            status="no_op",
            metadata={
                "ok": False,
                "status": "ticket_not_found",
                "thread_id": thread_id,
            },
        )
        return _public_status_result(finalized)

    metadata = {
        "ok": True,
        "status": "applied",
        "thread_id": thread_id,
        "previous_ticket_status": str(changed["previous_status"]),
        "ticket_status": status,
        "is_closed": status == "closed",
        "changed": bool(changed["changed"]),
        "actor_telegram_id": actor_telegram_id,
    }
    finalized = finalize_extension_core_operation(
        extension_id=str(claimed["extension_id"]),
        idempotency_key=str(claimed["idempotency_key"]),
        status="applied",
        metadata=metadata,
    )
    return _public_status_result(finalized)


async def create_extension_support_ticket(
    *,
    bot: Bot,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    target_user_id: int,
    text_html: str,
) -> dict[str, Any]:
    """Creates an idempotent extension-origin ticket and delivers its first message."""
    if operation not in _OPERATION_DIRECTIONS:
        raise ValueError(f'unsupported support operation: {operation}')
    lock_key = (str(extension_id).strip().casefold(), str(idempotency_key).strip())
    lock_entry = _TICKET_OPERATION_LOCKS.setdefault(lock_key, _TicketOperationLock())
    lock_entry.users += 1
    try:
        async with lock_entry.lock:
            return await _create_extension_support_ticket_locked(
                bot=bot,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
                operation=operation,
                target_user_id=target_user_id,
                text_html=text_html,
            )
    finally:
        lock_entry.users -= 1
        if lock_entry.users == 0 and _TICKET_OPERATION_LOCKS.get(lock_key) is lock_entry:
            _TICKET_OPERATION_LOCKS.pop(lock_key, None)


async def _create_extension_support_ticket_locked(
    *,
    bot: Bot,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    target_user_id: int,
    text_html: str,
) -> dict[str, Any]:
    """Runs one ticket operation while its process-local delivery is serialized."""

    from database.requests import (
        build_extension_core_request_fingerprint,
        claim_extension_core_operation,
        create_extension_support_ticket as create_ticket_record,
        finalize_extension_core_operation,
    )

    fingerprint = build_extension_core_request_fingerprint(
        operation=operation,
        target_user_id=target_user_id,
        payload={'text_html': text_html},
    )
    claimed = claim_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=idempotency_key,
        operation=operation,
        target_user_id=target_user_id,
        amount=None,
        reason=None,
        request_fingerprint=fingerprint,
    )
    if claimed.get('status') == 'idempotency_conflict':
        return _public_ticket_result(claimed)
    if not claimed.get('claimed') and claimed.get('stored_status') != 'applied':
        return _public_ticket_result(claimed)

    normalized_extension_id = str(claimed['extension_id'])
    normalized_key = str(claimed['idempotency_key'])
    try:
        ticket = create_ticket_record(
            target_user_id,
            direction=_OPERATION_DIRECTIONS[operation],
            extension_id=normalized_extension_id,
            idempotency_key=normalized_key,
            text_html=text_html,
        )
    except Exception as exc:
        logger.exception(
            'Extension support operation %s:%s failed: %s',
            normalized_extension_id,
            normalized_key,
            exc,
        )
        failed = finalize_extension_core_operation(
            extension_id=normalized_extension_id,
            idempotency_key=normalized_key,
            status='failed',
            metadata={
                'ok': False,
                'operation': operation,
                'target_user_id': target_user_id,
                'status': 'failed',
            },
        )
        return _public_ticket_result(failed)

    if ticket is None:
        missing = finalize_extension_core_operation(
            extension_id=normalized_extension_id,
            idempotency_key=normalized_key,
            status='no_op',
            metadata={
                'ok': False,
                'operation': operation,
                'target_user_id': target_user_id,
                'status': 'user_not_found',
            },
        )
        return _public_ticket_result(missing)

    thread = ticket['thread']
    message = ticket['message']
    replay = bool(
        claimed.get('already_applied')
        or claimed.get('stored_status') == 'applied'
        or ticket.get('already_created')
    )

    base_metadata = {
        'ok': True,
        'operation': operation,
        'target_user_id': target_user_id,
        'thread_id': int(thread['id']),
        'message_id': int(message['id']),
    }
    finalize_extension_core_operation(
        extension_id=normalized_extension_id,
        idempotency_key=normalized_key,
        status='applied',
        metadata=base_metadata,
    )

    from bot.services.support import (
        send_generated_admin_message_to_user,
        send_generated_user_message_to_admins,
    )

    if _OPERATION_DIRECTIONS[operation] == 'inbound':
        delivery = await send_generated_user_message_to_admins(
            bot,
            thread=thread,
            user=ticket['user'],
            support_message_id=int(message['id']),
            text_html=str(message['text_html']),
        )
    else:
        delivery = await send_generated_admin_message_to_user(
            bot,
            thread=thread,
            message=message,
        )

    finalized = finalize_extension_core_operation(
        extension_id=normalized_extension_id,
        idempotency_key=normalized_key,
        status='applied',
        metadata={
            **base_metadata,
            'delivery': delivery,
        },
    )
    return _public_ticket_result(
        finalized,
        force_already_applied=replay,
    )


def _public_ticket_result(
    result: dict[str, Any],
    *,
    force_already_applied: bool = False,
) -> dict[str, Any]:
    metadata = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
    stored_status = str(result.get('stored_status') or result.get('status') or '')
    already_applied = bool(force_already_applied or result.get('already_applied'))
    status = str(result.get('status') or stored_status)
    if status == 'idempotency_conflict':
        stored_status = 'idempotency_conflict'
    elif stored_status == 'applied':
        status = 'already_applied' if already_applied else 'applied'
    elif metadata.get('status'):
        status = str(metadata['status'])

    public = {
        **result,
        'ok': stored_status == 'applied',
        'status': status,
        'applied': stored_status == 'applied' and not already_applied,
        'already_applied': stored_status == 'applied' and already_applied,
    }
    for key in ('thread_id', 'message_id', 'delivery'):
        if key in metadata:
            public[key] = metadata[key]
    return public


def _public_status_result(
    result: dict[str, Any],
    *,
    force_already_applied: bool = False,
) -> dict[str, Any]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    stored_status = str(result.get("stored_status") or result.get("status") or "")
    already_applied = bool(
        (force_already_applied and stored_status == "applied")
        or result.get("already_applied")
    )
    is_conflict = result.get("status") == "idempotency_conflict"
    if is_conflict:
        public_status = "idempotency_conflict"
    elif stored_status == "applied":
        public_status = "already_applied" if already_applied else "applied"
    else:
        public_status = str(metadata.get("status") or result.get("status") or stored_status)

    public = {
        **result,
        "ok": stored_status == "applied" and not is_conflict,
        "status": public_status,
        "applied": (
            stored_status == "applied" and not already_applied and not is_conflict
        ),
        "already_applied": (
            stored_status == "applied" and already_applied and not is_conflict
        ),
    }
    for key in (
        "previous_ticket_status",
        "ticket_status",
        "is_closed",
        "changed",
        "thread_id",
    ):
        if key in metadata:
            public[key] = metadata[key]
    return public


def _public_thread(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row["status"])
    first_name = _optional_plain_text(row.get("user_first_name"))
    last_name = _optional_plain_text(row.get("user_last_name"))
    username = _optional_plain_text(row.get("user_username"))
    name_parts = [part for part in (first_name, last_name) if part]
    if name_parts:
        display_name = " ".join(name_parts)
    elif username:
        display_name = f"@{username}"
    else:
        display_name = (f"ID {int(row['user_telegram_id'])}" if row.get('user_telegram_id') is not None
                        else f"Account {int(row['user_id'])}")
    return {
        "thread_id": int(row["id"]),
        "status": status,
        "is_closed": status == "closed",
        "initiator_type": str(row["initiator_type"]),
        "initiator_admin_id": _optional_int(row.get("initiator_admin_id")),
        "assigned_admin_id": _optional_int(row.get("assigned_admin_id")),
        "message_count": int(row.get("message_count") or 0),
        "created_at": _iso_utc(row.get("created_at")),
        "updated_at": _iso_utc(row.get("updated_at")),
        "last_message_at": _iso_utc(row.get("last_message_at")),
        "user": {
            "user_id": int(row["user_id"]),
            "telegram_id": _optional_int(row.get("user_telegram_id")),
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": display_name,
        },
    }


def _public_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": int(row["id"]),
        "sender_type": str(row["sender_type"]),
        "sender_telegram_id": _optional_int(row.get("sender_telegram_id")),
        "text_html": str(row.get("text_html") or ""),
        "created_at": _iso_utc(row.get("created_at")),
        "origin_type": str(row.get("origin_type") or "telegram"),
        "origin_extension_id": _optional_plain_text(row.get("origin_extension_id")),
        "media_type": str(row.get("media_type") or "text"),
        "media_file_id": _optional_plain_text(row.get("media_file_id")),
    }


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_plain_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


__all__ = [
    'create_extension_support_ticket',
    'get_extension_support_ticket_history',
    'list_extension_support_ticket_sessions',
    'set_extension_support_ticket_status',
]
