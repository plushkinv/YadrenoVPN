"""Common token-fenced persistence for event deliveries and scheduled tasks."""
from __future__ import annotations

from uuid import uuid4

from .connection import get_db

DELIVERY_DUE_SQL = """((state = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP)
    OR (state = 'processing' AND lease_until <= CURRENT_TIMESTAMP))"""
_TABLES = {'extension_event_deliveries', 'extension_scheduled_tasks'}


def _table(value: str) -> str:
    if value not in _TABLES:
        raise ValueError('unsupported extension delivery table')
    return value


def claim_delivery_with_conn(conn, table: str, delivery_id: int):
    table = _table(table)
    updated = conn.execute(
        f"""UPDATE {table} SET state = 'processing', attempts = attempts + 1,
        claim_token = ?, lease_until = datetime('now', '+60 seconds')
        WHERE id = ? AND {DELIVERY_DUE_SQL}""",
        (uuid4().hex, int(delivery_id)),
    )
    if updated.rowcount == 0:
        return None
    return dict(conn.execute(f'SELECT * FROM {table} WHERE id = ?', (delivery_id,)).fetchone())


def finish_extension_delivery(
    table: str, delivery_id: int, claim_token: str, *, state: str,
    error_code: str | None = None, retry_seconds: int = 0,
) -> bool:
    table = _table(table)
    if state not in {'pending', 'completed', 'degraded'}:
        raise ValueError('unsupported delivery result')
    with get_db() as conn:
        result = conn.execute(
            f"""UPDATE {table} SET state = ?,
            next_attempt_at = datetime('now', ?), lease_until = NULL, claim_token = NULL,
            last_error_code = ?, completed_at = CASE WHEN ? IN ('completed', 'degraded')
                THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id = ? AND state = 'processing' AND claim_token = ?""",
            (state, f'+{max(0, int(retry_seconds))} seconds', error_code, state,
             int(delivery_id), claim_token),
        )
        return result.rowcount > 0


def extension_delivery_diagnostics(table: str):
    """Expose bounded state only, without identities, payloads or operation keys."""
    table = _table(table)
    with get_db() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
        ).fetchone():
            return {'totals': {}, 'handlers': [], 'issues': []}
        totals = conn.execute(f'SELECT state, COUNT(*) AS count FROM {table} GROUP BY state').fetchall()
        handlers = conn.execute(
            f'''SELECT extension_id, handler_name,
            SUM(state = 'pending') AS pending, SUM(state = 'processing') AS processing,
            SUM(state = 'completed') AS completed, SUM(state = 'degraded') AS degraded,
            MAX(attempts) AS max_attempts FROM {table}
            GROUP BY extension_id, handler_name ORDER BY extension_id, handler_name LIMIT 100''',
        ).fetchall()
        issues = conn.execute(
            f'''SELECT id, extension_id, handler_name, state, attempts, last_error_code,
            next_attempt_at FROM {table}
            WHERE last_error_code IS NOT NULL ORDER BY id DESC LIMIT 20''',
        ).fetchall()
        return {
            'totals': {row['state']: int(row['count']) for row in totals},
            'handlers': [dict(row) for row in handlers],
            'issues': [dict(row) for row in issues],
        }
