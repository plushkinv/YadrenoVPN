"""Durable coordination between local bindings and panel identity renames."""
import json

from core.results import CoreError
from .connection import get_db

__all__ = ['get_pending_panel_identity', 'get_due_panel_identity_renames',
           'save_panel_identity_snapshot', 'finish_panel_identity_rename', 'defer_panel_identity_rename',
           'get_panel_binding_conflicts', 'get_pending_panel_identity_ids', 'assert_panel_identity_ready']


def assert_panel_identity_ready(server: dict, email: str) -> None:
    """Reject mutations of either reserved name on any matching logical server."""
    from core.panel_identity import physical_panel_key
    endpoint = physical_panel_key(server)
    with get_db() as conn:
        rows = conn.execute("SELECT r.old_email, r.new_email, s.* FROM panel_identity_renames r "
                            "JOIN servers s ON s.id = r.server_id WHERE r.state != 'done' "
                            "AND (LOWER(r.old_email) = ? OR LOWER(r.new_email) = ?)",
                            (email.casefold(), email.casefold())).fetchall()
        if any(physical_panel_key(dict(row)) == endpoint for row in rows):
            raise CoreError('panel_identity_pending', retryable=True)


def get_pending_panel_identity_ids() -> frozenset[int]:
    with get_db() as conn:
        return frozenset(row[0] for row in conn.execute(
            "SELECT key_id FROM panel_identity_renames WHERE state != 'done'"))


def get_panel_binding_conflicts(server_ids: list[int], names: list[str], sub_id: str, key_id: int) -> bool:
    with get_db() as conn:
        server_marks = ','.join('?' for _ in server_ids)
        name_marks = ','.join('?' for _ in names)
        conflicts = conn.execute(f'SELECT id, panel_email FROM vpn_keys WHERE server_id IN ({server_marks}) AND id != ? '
                                 f'AND (LOWER(panel_email) IN ({name_marks}) OR sub_id = ?)',
                                 (*server_ids, key_id, *(name.casefold() for name in names), sub_id)).fetchall()
        own = conn.execute('SELECT import_id FROM subscription_import_members WHERE key_id = ?',
                           (key_id,)).fetchone()
        for conflict in conflicts:
            other = conn.execute('SELECT import_id FROM subscription_import_members WHERE key_id = ?',
                                 (conflict['id'],)).fetchone()
            if (conflict['panel_email'].casefold() in {name.casefold() for name in names} or
                    not own or not other or own[0] != other[0]):
                return True
        pending = conn.execute(f"SELECT key_id, old_email, new_email FROM panel_identity_renames WHERE server_id IN ({server_marks}) "
                            f"AND key_id != ? AND state != 'done' AND (LOWER(old_email) IN ({name_marks}) "
                            f"OR LOWER(new_email) IN ({name_marks}) OR sub_id = ?)",
                            (*server_ids, key_id, *(name.casefold() for name in names),
                             *(name.casefold() for name in names), sub_id)).fetchall()
        for item in pending:
            other = conn.execute('SELECT import_id FROM subscription_import_members WHERE key_id = ?',
                                 (item['key_id'],)).fetchone()
            if (any(item[field].casefold() in {name.casefold() for name in names}
                    for field in ('old_email', 'new_email')) or not own or not other or own[0] != other[0]):
                return True
        return False


def get_pending_panel_identity(key_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM panel_identity_renames WHERE key_id = ? AND state != 'done'",
                           (key_id,)).fetchone()
        return dict(row) if row else None


def get_due_panel_identity_renames(now: int, limit: int = 10) -> list[dict]:
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM panel_identity_renames WHERE state != 'done' AND next_attempt_at <= ? "
            "ORDER BY id LIMIT ?", (now, limit))]


def save_panel_identity_snapshot(operation_id: int, snapshot: dict) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM panel_identity_renames WHERE id = ?', (operation_id,)).fetchone()
        if not row or row['state'] == 'done':
            raise CoreError('panel_identity_changed')
        if row['snapshot_json'] is not None:
            return json.loads(row['snapshot_json'])
        conn.execute("UPDATE panel_identity_renames SET snapshot_json = ?, state = 'running' WHERE id = ?",
                     (json.dumps(snapshot, ensure_ascii=True, sort_keys=True), operation_id))
        return snapshot


def finish_panel_identity_rename(operation_id: int, now: int) -> None:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM panel_identity_renames WHERE id = ?', (operation_id,)).fetchone()
        if row is None or row['state'] == 'done':
            return
        changed = conn.execute('UPDATE vpn_keys SET panel_email = ? WHERE id = ? AND user_id = ? '
                               'AND server_id = ? AND sub_id = ? AND panel_email = ?',
                               (row['new_email'], row['key_id'], row['user_id'], row['server_id'],
                                row['sub_id'], row['old_email'])).rowcount
        if changed != 1:
            raise CoreError('panel_identity_changed')
        imported = conn.execute('SELECT m.*, i.endpoint FROM subscription_import_members m '
                                'JOIN subscription_imports i ON i.id = m.import_id WHERE key_id = ?',
                                (row['key_id'],)).fetchone()
        if imported:
            snapshot = json.loads(imported['snapshot_json'])
            active_snapshot = snapshot.get('replacement') or snapshot
            active_snapshot['record']['email'] = row['new_email']
            for placement in active_snapshot['placements']:
                placement['client']['email'] = row['new_email']
            conn.execute('UPDATE subscription_import_members SET email = ?, snapshot_json = ? WHERE key_id = ?',
                         (row['new_email'], json.dumps(snapshot, ensure_ascii=True, sort_keys=True), row['key_id']))
            conn.execute("UPDATE subscription_import_resources SET identity = ? WHERE endpoint = ? "
                         "AND kind = 'email' AND identity = ? AND import_id = ?",
                         (row['new_email'].casefold(), imported['endpoint'], row['old_email'].casefold(),
                          imported['import_id']))
        conn.execute("UPDATE panel_identity_renames SET state = 'done', completed_at = ?, error_code = NULL "
                     'WHERE id = ?', (now, operation_id))


def defer_panel_identity_rename(operation_id: int, error_code: str, now: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE panel_identity_renames SET attempts = attempts + 1, error_code = ?, "
                     "next_attempt_at = ? + MIN(3600, 30 * (1 << MIN(attempts, 7))) "
                     "WHERE id = ? AND state != 'done'", (error_code, now, operation_id))
