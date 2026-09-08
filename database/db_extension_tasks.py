"""Transactional scheduling and leased execution of extension-owned tasks."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

from .connection import get_db
from .db_extension_core import (
    build_extension_core_request_fingerprint, claim_extension_core_operation,
    finalize_extension_core_operation,
)
from .db_extension_deliveries import (
    DELIVERY_DUE_SQL, claim_delivery_with_conn, extension_delivery_diagnostics,
    finish_extension_delivery,
)
from .db_extension_payments import iso_utc, normalize_datetime, positive_int
from .db_extensions import normalize_extension_id

__all__ = [
    'schedule_extension_task', 'get_due_extension_task_ids', 'claim_extension_task',
    'finish_extension_task', 'get_extension_task_diagnostics',
]
_TABLE = 'extension_scheduled_tasks'


def create_extension_task_table(conn) -> None:
    """Create the queue only through the core migration."""
    conn.execute('''CREATE TABLE IF NOT EXISTS extension_scheduled_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL UNIQUE,
        contract_version INTEGER NOT NULL DEFAULT 1 CHECK(contract_version = 1),
        extension_id TEXT NOT NULL,
        handler_name TEXT NOT NULL,
        user_id INTEGER,
        telegram_id INTEGER,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'processing', 'completed', 'degraded')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        lease_until TEXT,
        claim_token TEXT,
        last_error_code TEXT,
        completed_at TEXT
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_extension_tasks_due
        ON extension_scheduled_tasks(state, next_attempt_at, lease_until, id)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_extension_tasks_handler
        ON extension_scheduled_tasks(extension_id, handler_name, state)''')


def schedule_extension_task(
    *, extension_id: str, handler_name: str, handler_registered: bool,
    idempotency_key: str, delay_seconds=None, run_at=None, payload=None,
    user_id=None, telegram_id=None,
) -> dict:
    """Commit the queue row and extension-wide scheduling receipt together."""
    from bot.utils.action_origin_context import normalize_completion_handler_name, normalize_origin_payload

    extension_id = normalize_extension_id(extension_id)
    name = normalize_completion_handler_name(handler_name)
    if (delay_seconds is None) == (run_at is None):
        raise ValueError('pass exactly one of delay_seconds or run_at')
    if delay_seconds is not None and (type(delay_seconds) is not int or delay_seconds < 0):
        raise ValueError('delay_seconds must be a non-negative integer')
    try:
        run_at = normalize_datetime(run_at, 'run_at')
    except OverflowError as exc:
        raise ValueError('run_at is outside the supported date range') from exc
    payload = normalize_origin_payload({} if payload is None else payload)
    if user_id is not None and telegram_id is not None:
        raise ValueError('pass user_id or telegram_id, not both')
    if user_id is not None:
        user_id = positive_int(user_id, 'user_id')
    if telegram_id is not None:
        telegram_id = positive_int(telegram_id, 'telegram_id')
    # Fingerprint the selector and requested delay, not mutable user state or now.
    fingerprint = build_extension_core_request_fingerprint(
        operation='schedule_task', target_user_id=user_id, amount=None,
        reason='extension_task', payload={
            'handler_name': name, 'delay_seconds': delay_seconds, 'run_at': run_at,
            'telegram_id': telegram_id, 'payload': payload,
        },
    )
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        claimed = claim_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key,
            operation='schedule_task', target_user_id=user_id, amount=None,
            reason='extension_task', request_fingerprint=fingerprint, _conn=conn,
        )
        if not claimed.get('claimed'):
            return {'task_id': None, 'run_at': None, **claimed, **(claimed.get('metadata') or {})}
        if not handler_registered:
            raise ValueError('task handler must be registered in the calling extension')
        identity = None
        if user_id is not None:
            identity = conn.execute(
                'SELECT id, telegram_id FROM users WHERE id = ?', (user_id,),
            ).fetchone()
        elif telegram_id is not None:
            identity = conn.execute(
                'SELECT id, telegram_id FROM users WHERE telegram_id = ?', (telegram_id,),
            ).fetchone()
        if user_id is not None and identity is None:
            result = {'ok': False, 'reason': 'user_not_found', 'task_id': None, 'run_at': None}
        else:
            created_at = conn.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
            try:
                deadline = (
                    datetime.fromisoformat(created_at) + timedelta(seconds=delay_seconds)
                    if delay_seconds is not None
                    else datetime.fromisoformat(run_at.replace('Z', '+00:00')).replace(tzinfo=None)
                )
            except (OverflowError, ValueError) as exc:
                raise ValueError('task deadline is outside the supported date range') from exc
            deadline_sql = deadline.isoformat(sep=' ')
            task_id = f'task_{uuid4().hex}'
            conn.execute(
                '''INSERT INTO extension_scheduled_tasks (
                    task_id, extension_id, handler_name, user_id, telegram_id,
                    payload, created_at, run_at, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (task_id, extension_id, name,
                 identity['id'] if identity else None,
                 identity['telegram_id'] if identity else telegram_id,
                 json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
                 created_at, deadline_sql, deadline_sql),
            )
            result = {
                'ok': True, 'task_id': task_id, 'run_at': iso_utc(deadline_sql),
                'target_user_id': identity['id'] if identity else None,
            }
        finalized = finalize_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key,
            status='applied' if result['ok'] else 'rejected', metadata=result, _conn=conn,
        )
        return {**finalized, **result}


def get_due_extension_task_ids(limit: int = 100) -> list[int]:
    with get_db() as conn:
        return [int(row['id']) for row in conn.execute(
            f'''SELECT id FROM extension_scheduled_tasks WHERE {DELIVERY_DUE_SQL}
            ORDER BY next_attempt_at, id LIMIT ?''',
            (max(1, min(int(limit), 100)),),
        )]


def claim_extension_task(task_id: int) -> dict | None:
    with get_db() as conn:
        return claim_delivery_with_conn(conn, _TABLE, task_id)


def finish_extension_task(task_id: int, claim_token: str, **kwargs) -> bool:
    return finish_extension_delivery(_TABLE, task_id, claim_token, **kwargs)


def get_extension_task_diagnostics() -> dict:
    return extension_delivery_diagnostics(_TABLE)
