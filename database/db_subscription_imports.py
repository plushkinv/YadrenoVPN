"""Atomic ownership of a complete physical-panel group and durable import facts."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from core.results import CoreError
from .connection import get_db
from .db_keys import _allocate_key_custom_name_with_conn

__all__ = ['claim_panel_subscription', 'get_subscription_import', 'has_subscription_import',
           'get_imported_key_binding', 'get_imported_panel_key_ids', 'is_imported_panel_binding',
           'observe_imported_key_state', 'update_imported_control']


def _result(conn, row) -> dict:
    return {'id': row['id'], 'state': 'completed', 'key_ids': [item[0] for item in conn.execute(
        'SELECT key_id FROM subscription_import_members WHERE import_id = ? '
        'AND key_id IS NOT NULL ORDER BY key_id', (row['id'],))]}


def get_subscription_import(import_id: int, user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute('SELECT * FROM subscription_imports WHERE id = ? AND user_id = ?',
                           (import_id, user_id)).fetchone()
        return _result(conn, row) if row else None


def has_subscription_import(user_id: int) -> bool:
    with get_db() as conn:
        return conn.execute('SELECT 1 FROM subscription_imports WHERE user_id = ? LIMIT 1',
                            (user_id,)).fetchone() is not None


def get_imported_panel_key_ids(*, preserve_terms_only: bool = False) -> frozenset[int]:
    with get_db() as conn:
        return frozenset(row[0] for row in conn.execute(
            'SELECT m.key_id FROM subscription_import_members m JOIN vpn_keys k ON k.id = m.key_id' +
            (' WHERE k.tariff_id IS NULL' if preserve_terms_only else '')))


def get_imported_key_binding(key_id: int) -> dict | None:
    from core.panel_identity import physical_panel_key
    with get_db() as conn:
        row = conn.execute('SELECT m.*, i.endpoint, i.source_url, i.user_id, '
                           'k.server_id, k.panel_email, k.sub_id, k.tariff_id '
                           'FROM subscription_import_members m '
                           'JOIN subscription_imports i ON i.id = m.import_id '
                           'JOIN vpn_keys k ON k.id = m.key_id AND k.user_id = i.user_id '
                           'WHERE m.key_id = ?', (key_id,)).fetchone()
        if not row:
            return None
        server = conn.execute('SELECT * FROM servers WHERE id = ?', (row['server_id'],)).fetchone()
        snapshot = json.loads(row['snapshot_json'])
        endpoint, source_url = row['endpoint'], row['source_url']
        if snapshot.get('replacement'):
            snapshot = snapshot['replacement']
            endpoint, source_url = snapshot['endpoint'], None
        if (not server or physical_panel_key(dict(server)) != endpoint or
                row['panel_email'] != row['email'] or row['sub_id'] != snapshot['record']['subId']):
            raise CoreError('panel_identity_changed')
        return {**dict(row), 'snapshot': snapshot, 'source_url': source_url,
                'preserve_terms': row['tariff_id'] is None}


def is_imported_panel_binding(server_id: int, email: str) -> bool:
    with get_db() as conn:
        rows = conn.execute('SELECT k.id FROM vpn_keys k JOIN subscription_import_members m '
                            'ON m.key_id = k.id WHERE k.server_id = ? AND k.panel_email = ?',
                            (server_id, email)).fetchall()
    return len(rows) == 1 and get_imported_key_binding(rows[0][0]) is not None


def claim_panel_subscription(*, user_id: int, endpoint: str, server_id: int,
                             source_url: str, snapshot: dict, now: int) -> dict:
    """Commit all members together; no panel writes or fabricated financial history."""
    from core.panel_identity import physical_panel_key
    members = snapshot['members']
    names = {item['record']['email'].casefold() for item in members}
    sub_ids = set(snapshot['sub_ids'])
    if not members or len(names) != len(members) or not sub_ids:
        raise CoreError('subscription_ambiguous')
    resources = {('email', name) for name in names} | {('sub_id', value) for value in sub_ids}
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT is_banned FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user or user['is_banned']:
            raise CoreError('account_unavailable')
        servers = [dict(row) for row in conn.execute('SELECT * FROM servers')]
        peers = [item['id'] for item in servers if physical_panel_key(item) == endpoint]
        if server_id not in peers:
            raise CoreError('panel_identity_changed')
        # Check current ownership even when a previous receipt exists.
        marks = ','.join('?' for _ in peers)
        existing = [dict(row) for row in conn.execute(
            f'SELECT * FROM vpn_keys WHERE server_id IN ({marks})', peers)
            if str(row['panel_email'] or '').casefold() in names or row['sub_id'] in sub_ids]
        if any(row['user_id'] != user_id for row in existing):
            raise CoreError('subscription_owned')
        prior = []
        for kind, identity in resources:
            row = conn.execute('SELECT i.* FROM subscription_import_resources r '
                               'JOIN subscription_imports i ON i.id = r.import_id '
                               'WHERE r.endpoint = ? AND r.kind = ? AND r.identity = ?',
                               (endpoint, kind, identity)).fetchone()
            if row:
                prior.append(row)
        if prior:
            if any(row['user_id'] != user_id for row in prior):
                raise CoreError('subscription_owned')
            if len({row['id'] for row in prior}) != 1:
                raise CoreError('subscription_ambiguous')
            saved = {(row[0], row[1]) for row in conn.execute(
                'SELECT kind, identity FROM subscription_import_resources WHERE import_id = ?',
                (prior[0]['id'],))}
            if resources != saved:
                raise CoreError('subscription_changed')
            return _result(conn, prior[0])
        # Existing installations need no backfill or unique constraint on old rows.
        pending = conn.execute(f"SELECT * FROM panel_identity_renames WHERE state != 'done' "
                               f'AND server_id IN ({marks})', peers).fetchall()
        if any(row['sub_id'] in sub_ids or row['old_email'].casefold() in names or
               row['new_email'].casefold() in names for row in pending):
            raise CoreError('panel_identity_pending', retryable=True)
        if existing:
            if len(existing) != len(names) or {row['panel_email'].casefold() for row in existing} != names:
                raise CoreError('subscription_ambiguous')
            return {'id': None, 'state': 'completed', 'key_ids': sorted(row['id'] for row in existing)}
        import_id = conn.execute('INSERT INTO subscription_imports '
                                 '(user_id, endpoint, source_url, created_at) VALUES (?, ?, ?, ?)',
                                 (user_id, endpoint, source_url, now)).lastrowid
        conn.executemany('INSERT INTO subscription_import_resources VALUES (?, ?, ?, ?)',
                         [(endpoint, kind, identity, import_id) for kind, identity in sorted(resources)])
        for item in members:
            record = item['record']
            expiry = int(record.get('expiryTime') or 0)
            expires_at = (datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                          if expiry > 0 else None)
            total = max(0, int(record.get('totalGB') or 0))
            key_id = conn.execute('''INSERT INTO vpn_keys
                (user_id, server_id, tariff_id, panel_email, sub_id, custom_name,
                 expires_at, traffic_used, traffic_limit, traffic_limit_override, max_ips_override)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, server_id, record['email'], record['subId'],
                 _allocate_key_custom_name_with_conn(conn, user_id), expires_at,
                 max(0, int(item['traffic_used'])), total, total,
                 int(record['limitIp']) if 1 <= int(record.get('limitIp') or 0) <= 999 else None)).lastrowid
            conn.execute('INSERT INTO subscription_import_members VALUES (?, ?, ?, ?)',
                         (import_id, record['email'], key_id, json.dumps(item, ensure_ascii=True, sort_keys=True)))
            conn.execute('UPDATE vpn_keys SET traffic_updated_at=? WHERE id=?',
                         (datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), key_id))
        return _result(conn, {'id': import_id})


def observe_imported_key_state(key_id, *, server_id, email, sub_id, expiry_ms, traffic_limit, traffic_used, enabled):
    """Cache actual unknown terms without replacing their historical claim snapshot."""
    from .db_key_operations import _assert_key_mutation_ready
    with get_db() as conn:
        _assert_key_mutation_ready(conn, key_id)
        row = conn.execute('SELECT m.snapshot_json FROM subscription_import_members m JOIN vpn_keys k ON k.id=m.key_id '
                           'WHERE k.id=? AND k.tariff_id IS NULL AND k.server_id=? AND k.panel_email=? AND k.sub_id=?',
                           (key_id, server_id, email, sub_id)).fetchone()
        if row is None:
            return False
        snapshot = json.loads(row['snapshot_json'])
        active = snapshot.get('replacement', snapshot)
        active['observation'] = {'expiry_ms': expiry_ms, 'enabled': bool(enabled),
                                 'observed_at': datetime.now(timezone.utc).isoformat()}
        expiry_ms = active.get('control', {}).get('expiry_ms', expiry_ms)
        expires_at = datetime.fromtimestamp(expiry_ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if expiry_ms > 0 else None
        conn.execute('UPDATE subscription_import_members SET snapshot_json=? WHERE key_id=?',
                     (json.dumps(snapshot, sort_keys=True), key_id))
        conn.execute('UPDATE vpn_keys SET expires_at=?,traffic_limit=?,traffic_limit_override=?,traffic_used=?, '
                     'traffic_updated_at=CURRENT_TIMESTAMP WHERE id=?', (expires_at, traffic_limit, traffic_limit, traffic_used, key_id))
        return True


def update_imported_control(key_id, values=None, *, acknowledged=None):
    """Persist only explicit duration changes and reversible account-ban state."""
    from .db_key_operations import _assert_key_mutation_ready
    with get_db() as conn:
        _assert_key_mutation_ready(conn, key_id)
        row = conn.execute('SELECT snapshot_json FROM subscription_import_members WHERE key_id=?', (key_id,)).fetchone()
        if not row:
            return False
        snapshot = json.loads(row[0])
        active = snapshot.get('replacement', snapshot)
        control = active.setdefault('control', {})
        control.update(values or {})
        for name, value in (acknowledged or {}).items():
            if control.get(name) == value:
                control.pop(name, None)
                if name == 'expiry_ms':
                    active.setdefault('observation', {})['expiry_ms'] = value
        conn.execute('UPDATE subscription_import_members SET snapshot_json=? WHERE key_id=?',
                     (json.dumps(snapshot, sort_keys=True), key_id))
        return True


def _extend_imported_terms(conn, key_id, days, *, finite_from_now_if_unlimited=False):
    """Keep first-use duration distinct from unlimited access in legacy day grants."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscription_import_members'").fetchone():
        return None
    row = conn.execute('SELECT m.snapshot_json,k.expires_at FROM subscription_import_members m JOIN vpn_keys k ON k.id=m.key_id '
                       'WHERE k.id=? AND k.tariff_id IS NULL', (key_id,)).fetchone()
    if not row:
        return None
    snapshot = json.loads(row['snapshot_json'])
    active = snapshot.get('replacement', snapshot)
    control = active.setdefault('control', {})
    expiry = int(control.get('expiry_ms', active.get('observation', {}).get('expiry_ms', active['record'].get('expiryTime') or 0)))
    unlimited = expiry == 0 and not finite_from_now_if_unlimited
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if days == 0 or unlimited:
        expiry = 0
    elif expiry < 0:
        remaining_ms = -expiry + int(days) * 86400000
        expiry = (max(now_ms, now_ms + remaining_ms) if finite_from_now_if_unlimited
                  else -remaining_ms if remaining_ms > 0 else now_ms)
    else:
        expiry = max(now_ms, max(now_ms, expiry) + int(days) * 86400000)
    control['expiry_ms'] = expiry
    expires_after = datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if expiry > 0 else None
    conn.execute('UPDATE subscription_import_members SET snapshot_json=? WHERE key_id=?', (json.dumps(snapshot, sort_keys=True), key_id))
    conn.execute('UPDATE vpn_keys SET expires_at=? WHERE id=?', (expires_after, key_id))
    return {'unlimited': unlimited, 'expires_before': row['expires_at'], 'expires_after': expires_after}
