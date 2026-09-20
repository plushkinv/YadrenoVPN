"""Two-party identity linking; no implicit Telegram registration or account merge."""
from __future__ import annotations

from core.results import CoreError
from .connection import get_db
from .db_account_auth import _session_with_conn

__all__ = ['create_account_link', 'inspect_account_link', 'confirm_account_link_telegram',
           'cancel_account_link', 'finish_account_link', 'get_account_link_status']


def _request(conn, token_hash, bot_id, now):
    row = conn.execute('SELECT * FROM account_link_requests WHERE token_hash = ? AND bot_id = ?',
                       (token_hash, bot_id)).fetchone()
    if row is None or row['expires_at'] <= now or row['state'] == 'completed':
        raise CoreError('link_invalid')
    session = _session_with_conn(conn, row['session_hash'], now)
    if session is None or session['user_id'] != row['user_id'] or session['telegram_id'] is not None:
        raise CoreError('link_invalid')
    return row


def _available_telegram(conn, request, telegram_id):
    if request['telegram_id'] not in (None, telegram_id):
        raise CoreError('link_conflict')
    existing = conn.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    if existing is not None:
        raise CoreError('link_conflict')


def create_account_link(*, token_hash: str, session_hash: str, user_id: int, bot_id: int, now: int) -> None:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        session = _session_with_conn(conn, session_hash, now)
        if session is None or session['user_id'] != user_id:
            raise CoreError('authentication_required')
        if session['telegram_id'] is not None:
            raise CoreError('telegram_already_linked')
        conn.execute('INSERT INTO account_link_requests(token_hash, session_hash, user_id, bot_id, '
                     'created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)',
                     (token_hash, session_hash, user_id, bot_id, now, now + 600))


def inspect_account_link(*, token_hash: str, bot_id: int, telegram_id: int, now: int) -> dict:
    with get_db() as conn:
        row = _request(conn, token_hash, bot_id, now)
        _available_telegram(conn, row, telegram_id)
        return {'account_id': row['user_id'], 'expires_at': row['expires_at']}


def confirm_account_link_telegram(*, token_hash: str, bot_id: int, actor: dict, now: int) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _request(conn, token_hash, bot_id, now)
        _available_telegram(conn, row, actor['id'])
        conn.execute("UPDATE account_link_requests SET telegram_id = ?, telegram_username = ?, "
                     "telegram_first_name = ?, telegram_last_name = ?, telegram_confirmed_at = ?, "
                     "state = 'confirmed' WHERE token_hash = ?",
                     (actor['id'], actor.get('username'), actor.get('first_name'), actor.get('last_name'),
                      now, token_hash))
        return {'account_id': row['user_id'], 'state': 'confirmed'}


def cancel_account_link(*, token_hash: str, bot_id: int, telegram_id: int, now: int) -> None:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _request(conn, token_hash, bot_id, now)
        if row['telegram_id'] not in (None, telegram_id):
            raise CoreError('link_conflict')
        conn.execute('DELETE FROM account_link_requests WHERE token_hash = ?', (token_hash,))


def get_account_link_status(*, token_hash: str, session_hash: str, bot_id: int, now: int) -> dict:
    with get_db() as conn:
        row = _request(conn, token_hash, bot_id, now)
        if row['session_hash'] != session_hash:
            raise CoreError('link_invalid')
        return {'state': row['state'], 'expires_at': row['expires_at'], 'telegram_id': row['telegram_id'],
                'username': row['telegram_username'], 'first_name': row['telegram_first_name'],
                'last_name': row['telegram_last_name']}


def finish_account_link(*, token_hash: str, session_hash: str, bot_id: int, telegram_id: int, now: int) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _request(conn, token_hash, bot_id, now)
        if row['session_hash'] != session_hash:
            raise CoreError('link_invalid')
        if row['state'] != 'confirmed' or row['telegram_id'] != telegram_id:
            raise CoreError('link_confirmation_required')
        _available_telegram(conn, row, telegram_id)
        pending = conn.execute("SELECT id FROM account_operations WHERE user_id=? "
                    "AND kind IN ('key.replace','key.configure','key.delete') AND result_json IS NULL LIMIT 1",
                    (row['user_id'],)).fetchone()
        if pending:
            raise CoreError('key_operation_pending', retryable=True, operation_id=pending['id'])
        changed = conn.execute('UPDATE users SET telegram_id = ?, username = ?, first_name = ?, last_name = ? '
                               'WHERE id = ? AND telegram_id IS NULL',
                               (telegram_id, row['telegram_username'], row['telegram_first_name'],
                                row['telegram_last_name'], row['user_id'])).rowcount
        if changed != 1:
            raise CoreError('link_conflict')
        prefix = f"site_{row['user_id']}_"
        keys = conn.execute('SELECT id, server_id, panel_email, sub_id FROM vpn_keys WHERE user_id = ? '
                            'AND server_id IS NOT NULL', (row['user_id'],)).fetchall()
        count = 0
        for key in keys:
            if not key['panel_email'].startswith(prefix):
                continue
            conn.execute('INSERT INTO panel_identity_renames(key_id, user_id, server_id, old_email, '
                         'new_email, sub_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (key['id'], row['user_id'], key['server_id'], key['panel_email'],
                          f"user_{telegram_id}_" + key['panel_email'][len(prefix):], key['sub_id'], now))
            count += 1
        conn.execute("UPDATE account_link_requests SET state = 'completed', completed_at = ? WHERE token_hash = ?",
                     (now, token_hash))
        return {'account_id': row['user_id'], 'telegram_id': telegram_id, 'pending_renames': count}
