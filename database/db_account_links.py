"""Two-party identity linking and transfer into the confirmed Telegram account."""
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
    existing = conn.execute('SELECT u.*,COALESCE(c.version,0) AS credential_version FROM users u '
                            'LEFT JOIN account_credentials c ON c.user_id=u.id WHERE telegram_id=?', (telegram_id,)).fetchone()
    if existing and (existing['is_banned'] or existing['merged_into_user_id']):
        raise CoreError('access_denied')
    return existing


def _preview(conn, row, target):
    credentials = conn.execute('SELECT phone FROM account_credentials WHERE user_id=?', (row['user_id'],)).fetchone()
    return {'account_id': row['user_id'], 'target_account_id': target['id'] if target else row['user_id'],
            'phone': credentials['phone'] if credentials else None, 'merge': target is not None}


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
        target = _available_telegram(conn, row, telegram_id)
        return {**_preview(conn, row, target), 'expires_at': row['expires_at']}


def confirm_account_link_telegram(*, token_hash: str, bot_id: int, actor: dict, now: int) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _request(conn, token_hash, bot_id, now)
        target = _available_telegram(conn, row, actor['id'])
        conn.execute("UPDATE account_link_requests SET telegram_id = ?, telegram_username = ?, "
                     "telegram_first_name = ?, telegram_last_name = ?, telegram_confirmed_at = ?, "
                     "state = 'confirmed',target_user_id=?,target_credential_version=? WHERE token_hash = ?",
                     (actor['id'], actor.get('username'), actor.get('first_name'), actor.get('last_name'),
                      now, target['id'] if target else None, target['credential_version'] if target else None, token_hash))
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
        target = _available_telegram(conn, row, row['telegram_id']) if row['telegram_id'] else None
        return {**_preview(conn, row, target), 'state': row['state'], 'expires_at': row['expires_at'], 'telegram_id': row['telegram_id'],
                'username': row['telegram_username'], 'first_name': row['telegram_first_name'],
                'last_name': row['telegram_last_name']}


def finish_account_link(*, token_hash: str, session_hash: str, bot_id: int, telegram_id: int, now: int,
                        expected_target_id: int | None = None, subscribers=()) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _request(conn, token_hash, bot_id, now)
        if row['session_hash'] != session_hash:
            raise CoreError('link_invalid')
        if row['state'] != 'confirmed' or row['telegram_id'] != telegram_id:
            raise CoreError('link_confirmation_required')
        target = _available_telegram(conn, row, telegram_id)
        owner_id = target['id'] if target else row['user_id']
        if expected_target_id is not None and owner_id != expected_target_id:
            raise CoreError('link_conflict')
        if target and (row['target_user_id'] != target['id'] or row['target_credential_version'] != target['credential_version']):
            raise CoreError('credentials_changed')
        pending = conn.execute("SELECT id FROM account_operations WHERE user_id IN (?,?) "
                    "AND kind IN ('key.replace','key.configure','key.delete','key.device_delete') AND result_json IS NULL LIMIT 1",
                    (row['user_id'], owner_id)).fetchone()
        if pending:
            raise CoreError('key_operation_pending', retryable=True, operation_id=pending['id'])
        if target:
            from .db_account_merge import merge_account_with_conn
            merge_account_with_conn(conn, row['user_id'], owner_id, now, subscribers=subscribers)
        else:
            changed = conn.execute('UPDATE users SET telegram_id=?,username=?,first_name=?,last_name=? '
                                   'WHERE id=? AND telegram_id IS NULL',
                                   (telegram_id, row['telegram_username'], row['telegram_first_name'],
                                    row['telegram_last_name'], row['user_id'])).rowcount
            if changed != 1:
                raise CoreError('link_conflict')
            conn.execute('UPDATE account_sessions SET revoked_at=? WHERE user_id=?', (now, owner_id))
            conn.execute('DELETE FROM auth_challenges WHERE user_id=? OR session_hash IN '
                         '(SELECT token_hash FROM account_sessions WHERE user_id=?)', (owner_id, owner_id))
        from .db_panel_identity import _enqueue_telegram_panel_renames
        count = _enqueue_telegram_panel_renames(conn, owner_id, now)
        conn.execute("UPDATE account_link_requests SET state = 'completed', completed_at = ? WHERE token_hash = ?",
                     (now, token_hash))
        return {'account_id': owner_id, 'telegram_id': telegram_id, 'pending_renames': count}
