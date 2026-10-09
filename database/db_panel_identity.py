"""Durable coordination between local bindings and panel identity renames."""
import json
import re
from datetime import datetime, timezone

from core.results import CoreError
from .connection import get_db

__all__ = ['get_pending_panel_identity', 'get_due_panel_identity_renames',
           'save_panel_identity_snapshot', 'finish_panel_identity_rename', 'defer_panel_identity_rename',
           'get_panel_binding_conflicts', 'get_pending_panel_identity_ids', 'assert_panel_identity_ready',
           'get_panel_subscription_keys', 'bind_panel_subscription']


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
        owner = conn.execute('SELECT user_id FROM vpn_keys WHERE id = ?', (key_id,)).fetchone()
        if owner is None:
            return True
        conflicts = conn.execute(f'SELECT id, user_id, panel_email FROM vpn_keys WHERE server_id IN ({server_marks}) AND id != ? '
                                 f'AND (LOWER(panel_email) IN ({name_marks}) OR sub_id = ?)',
                                 (*server_ids, key_id, *(name.casefold() for name in names), sub_id)).fetchall()
        for conflict in conflicts:
            if (conflict['panel_email'].casefold() in {name.casefold() for name in names} or
                    conflict['user_id'] != owner['user_id']):
                return True
        pending = conn.execute(f"SELECT key_id, user_id, old_email, new_email FROM panel_identity_renames WHERE server_id IN ({server_marks}) "
                            f"AND key_id != ? AND state != 'done' AND (LOWER(old_email) IN ({name_marks}) "
                            f"OR LOWER(new_email) IN ({name_marks}) OR sub_id = ?)",
                            (*server_ids, key_id, *(name.casefold() for name in names),
                             *(name.casefold() for name in names), sub_id)).fetchall()
        for item in pending:
            if (any(item[field].casefold() in {name.casefold() for name in names}
                    for field in ('old_email', 'new_email')) or item['user_id'] != owner['user_id']):
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


def _enqueue_telegram_panel_renames(conn, user_id: int, now: int) -> int:
    """Use the same queue when Telegram is linked during an unfinished rename."""
    from bot.utils.panel_email import get_panel_email_prefix
    user = conn.execute('SELECT id, telegram_id, username FROM users WHERE id = ?', (user_id,)).fetchone()
    if user['telegram_id'] is None:
        return 0
    new_prefix = get_panel_email_prefix(dict(user))
    keys = conn.execute('SELECT id, server_id, panel_email, sub_id FROM vpn_keys WHERE user_id = ? '
                        'AND server_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM panel_identity_renames r '
                        "WHERE r.key_id = vpn_keys.id AND r.state != 'done')", (user_id,)).fetchall()
    count = 0
    for key in keys:
        match = re.match(r'^site_[0-9]+_', str(key['panel_email'] or ''))
        if not match:
            continue
        new_email = new_prefix + key['panel_email'][match.end():]
        if conn.execute('SELECT 1 FROM vpn_keys WHERE id!=? AND LOWER(panel_email)=LOWER(?) '
                        'UNION ALL SELECT 1 FROM panel_identity_renames WHERE key_id!=? AND state!=? AND LOWER(new_email)=LOWER(?)',
                        (key['id'], new_email, key['id'], 'done', new_email)).fetchone():
            new_email += '_' + str(key['id'])
        conn.execute('INSERT INTO panel_identity_renames(key_id, user_id, server_id, old_email, '
                     'new_email, sub_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (key['id'], user_id, key['server_id'], key['panel_email'],
                      new_email, key['sub_id'], now))
        count += 1
    return count


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
        snapshot = json.loads(row['snapshot_json'] or '{}')
        if snapshot.get('source') == 'subscription_link':
            conn.execute("INSERT INTO key_operation_log (vpn_key_id, user_id, operation_type, source, expires_after) "
                         "SELECT id, user_id, 'subscription_link', 'panel', expires_at FROM vpn_keys WHERE id = ?",
                         (row['key_id'],))
        conn.execute("UPDATE panel_identity_renames SET state = 'done', completed_at = ?, error_code = NULL, snapshot_json = NULL "
                     'WHERE id = ?', (now, operation_id))
        if re.match(r'^site_[0-9]+_', row['new_email']):
            _enqueue_telegram_panel_renames(conn, row['user_id'], now)


def defer_panel_identity_rename(operation_id: int, error_code: str, now: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE panel_identity_renames SET attempts = attempts + 1, error_code = ?, "
                     "next_attempt_at = ? + MIN(3600, 30 * (1 << MIN(attempts, 7))) "
                     "WHERE id = ? AND state != 'done'", (error_code, now, operation_id))


def _panel_subscription_keys(conn, endpoint, sub_ids, names):
    from core.panel_identity import physical_panel_key
    peers = [row['id'] for row in conn.execute('SELECT * FROM servers')
             if physical_panel_key(dict(row)) == endpoint]
    marks = ','.join('?' for _ in peers)
    names = {name.casefold() for name in names}
    return [dict(row) for row in conn.execute(
        f'SELECT k.*, t.group_id, r.old_email, r.new_email FROM vpn_keys k '
        'JOIN tariffs t ON t.id = k.tariff_id '
        "LEFT JOIN panel_identity_renames r ON r.key_id = k.id AND r.state != 'done' "
        f'WHERE k.server_id IN ({marks})', peers)
        if row['sub_id'] in sub_ids or any(str(row[field] or '').casefold() in names
                                         for field in ('panel_email', 'old_email', 'new_email'))]


def get_panel_subscription_keys(endpoint: str, sub_ids: list[str], names=()) -> list[dict]:
    """Read ordinary ownership, including names reserved by an unfinished rename."""
    with get_db() as conn:
        return _panel_subscription_keys(conn, endpoint, sub_ids, names)


def bind_panel_subscription(*, user_id: int, endpoint: str, group_id: int,
                            members: list[dict], servers: list[dict], now: int) -> list[int]:
    """Reserve the complete subscription as ordinary keys and enqueue their renames."""
    from bot.utils.panel_email import generate_unique_panel_email
    from core.panel_identity import physical_panel_key
    from .db_keys import _allocate_key_custom_name_with_conn

    names = {item['record']['email'].casefold() for item in members}
    sub_ids = {item['record']['subId'] for item in members}
    if not members or len(names) != len(members) or not all(sub_ids):
        raise CoreError('subscription_ambiguous')
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if user is None or user['is_banned']:
            raise CoreError('account_unavailable')
        tariff = conn.execute("SELECT id FROM tariffs WHERE group_id = ? AND system_type = 'admin_custom'",
                              (group_id,)).fetchone()
        if tariff is None:
            raise CoreError('subscription_group_unavailable')
        for selected in servers:
            current = conn.execute('SELECT s.* FROM servers s JOIN server_groups g ON g.server_id = s.id '
                                   'WHERE s.id = ? AND g.group_id = ? AND s.is_active = 1',
                                   (selected['id'], group_id)).fetchone()
            if (not current or physical_panel_key(dict(current)) != endpoint or
                    current['inbound_group_id'] != selected['inbound_group_id']):
                raise CoreError('subscription_group_unavailable')
        existing = _panel_subscription_keys(conn, endpoint, sub_ids, names)
        if any(row['user_id'] != user_id for row in existing):
            raise CoreError('subscription_owned')
        if existing:
            if (len(existing) != len(members) or any(row['group_id'] != group_id for row in existing) or
                    any(not any(row['sub_id'] == member['record']['subId'] and
                                member['record']['email'].casefold() in
                                {str(row[field] or '').casefold() for field in ('panel_email', 'old_email', 'new_email')}
                                for row in existing) for member in members)):
                raise CoreError('subscription_ambiguous')
            return sorted(row['id'] for row in existing)
        result = []
        for item, server in zip(members, servers, strict=True):
            record = item['record']
            expiry = int(record.get('expiryTime') or 0)
            expiry = now * 1000 - expiry if expiry < 0 else expiry
            expires_at = (datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
                          if expiry else None)
            total, used = int(record.get('totalGB') or 0), int(item['traffic_used'])
            key_id = conn.execute('''INSERT INTO vpn_keys
                (user_id, server_id, tariff_id, panel_email, sub_id, custom_name, expires_at,
                 traffic_used, traffic_limit, traffic_limit_override, max_ips_override, traffic_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                (user_id, server['id'], tariff['id'], record['email'], record['subId'],
                 _allocate_key_custom_name_with_conn(conn, user_id), expires_at, used, total, total,
                 item['device_limit'])).lastrowid
            target = generate_unique_panel_email(dict(user))
            if _panel_subscription_keys(conn, endpoint, (), [target]):
                raise CoreError('panel_identity_conflict')
            snapshot = {**item, 'version': 1, 'endpoint': endpoint, 'sub_id': record['subId'],
                        'source': 'subscription_link'}
            if int(record.get('expiryTime') or 0) < 0:
                snapshot['expiry_time_ms'] = expiry
            conn.execute('''INSERT INTO panel_identity_renames
                (key_id, user_id, server_id, old_email, new_email, sub_id, snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (key_id, user_id, server['id'], record['email'], target, record['subId'],
                 json.dumps(snapshot, sort_keys=True), now))
            result.append(key_id)
        return result
