"""Atomic credential and session persistence for the existing account owner."""
from __future__ import annotations

import sqlite3

from core.results import CoreError
from .connection import get_db
from .db_users import _insert_user_with_conn

__all__ = [
    'get_account_credentials', 'get_phone_credentials', 'create_account_credentials',
    'change_account_credentials', 'reset_account_password', 'create_account_session',
    'get_account_session', 'revoke_account_session', 'consume_auth_limits',
]


def get_account_credentials(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute('SELECT * FROM account_credentials WHERE user_id = ?', (user_id,)).fetchone()
        return dict(row) if row else None


def get_phone_credentials(phone: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            'SELECT c.*, u.is_banned FROM account_credentials c '
            'JOIN users u ON u.id = c.user_id WHERE phone = ?', (phone,),
        ).fetchone()
        return dict(row) if row else None


def _consume_proof(conn, *, proof_hash, phone, purpose, session_hash, now):
    if not proof_hash:
        raise CoreError('sms_proof_required')
    changed = conn.execute(
        "UPDATE auth_challenges SET state = 'used', used_at = ? WHERE proof_hash = ? "
        "AND phone = ? AND purpose = ? AND session_hash IS ? "
        "AND state = 'verified' AND expires_at > ?",
        (now, proof_hash, phone, purpose, session_hash, now),
    ).rowcount
    if changed != 1:
        raise CoreError('sms_proof_invalid')


def create_account_credentials(
    *, phone: str, password_hash: str, now: int, verified: bool,
    proof_hash: str | None = None, user_id: int | None = None,
    session_hash: str | None = None,
    referral_code: str | None = None,
) -> dict:
    """Create a site account, or attach first credentials to a verified Mini App owner."""
    try:
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM account_credentials WHERE phone = ?', (phone,)).fetchone():
                raise CoreError('phone_in_use')
            if user_id is not None:
                session = _session_with_conn(conn, session_hash, now)
                if not session or session['user_id'] != user_id or session['source'] != 'mini_app':
                    raise CoreError('authentication_required')
                if session['authenticated_at'] < now - 300:
                    raise CoreError('reauthentication_required')
                if conn.execute('SELECT 1 FROM account_credentials WHERE user_id = ?', (user_id,)).fetchone():
                    raise CoreError('credentials_already_exist')
            if verified:
                _consume_proof(conn, proof_hash=proof_hash, phone=phone,
                               purpose='credentials' if user_id is not None else 'register',
                               session_hash=session_hash, now=now)
            if user_id is None:
                user_id = _insert_user_with_conn(conn, None)['id']
                if referral_code:
                    referrer = conn.execute('SELECT id FROM users WHERE referral_code=?', (referral_code,)).fetchone()
                    if referrer and referrer['id'] != user_id:
                        from .db_users import set_user_referrer
                        set_user_referrer(user_id, referrer['id'], is_new_registration=True, _conn=conn)
            conn.execute(
                'INSERT INTO account_credentials(user_id, phone, password_hash, phone_verified, updated_at) '
                'VALUES (?, ?, ?, ?, ?)', (user_id, phone, password_hash, int(verified), now),
            )
            conn.execute('UPDATE account_sessions SET revoked_at = ? WHERE user_id = ?', (now, user_id))
            return dict(conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        if 'account_credentials.phone' in str(exc):
            raise CoreError('phone_in_use') from None
        raise


def change_account_credentials(
    *, user_id: int, expected_version: int, phone: str, password_hash: str,
    now: int, verified: bool, session_hash: str, proof_hash: str | None = None,
) -> None:
    """Commit an authenticated change, consume its proof and revoke all sessions."""
    try:
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            session = _session_with_conn(conn, session_hash, now)
            if not session or session['user_id'] != user_id:
                raise CoreError('authentication_required')
            old = conn.execute('SELECT * FROM account_credentials WHERE user_id = ?', (user_id,)).fetchone()
            if old is None or old['version'] != expected_version:
                raise CoreError('credentials_changed')
            if phone != old['phone'] and verified:
                _consume_proof(conn, proof_hash=proof_hash, phone=phone, purpose='credentials',
                               session_hash=session_hash, now=now)
            conn.execute(
                'UPDATE account_credentials SET phone = ?, password_hash = ?, phone_verified = ?, '
                'version = version + 1, updated_at = ? WHERE user_id = ?',
                (phone, password_hash, int(verified), now, user_id),
            )
            conn.execute('UPDATE account_sessions SET revoked_at = ? WHERE user_id = ?', (now, user_id))
    except sqlite3.IntegrityError as exc:
        if 'account_credentials.phone' in str(exc):
            raise CoreError('phone_in_use') from None
        raise


def reset_account_password(*, phone: str, password_hash: str, proof_hash: str, now: int) -> None:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _consume_proof(conn, proof_hash=proof_hash, phone=phone, purpose='reset', session_hash=None, now=now)
        row = conn.execute('SELECT user_id FROM account_credentials WHERE phone = ?', (phone,)).fetchone()
        if row is None:
            raise CoreError('sms_proof_invalid')
        conn.execute(
            'UPDATE account_credentials SET password_hash = ?, phone_verified = 1, '
            'version = version + 1, updated_at = ? WHERE user_id = ?',
            (password_hash, now, row['user_id']),
        )
        conn.execute('UPDATE account_sessions SET revoked_at = ? WHERE user_id = ?', (now, row['user_id']))


def create_account_session(
    *, user_id: int, source: str, expected_version: int, token_hash: str,
    csrf_hash: str, now: int, expires_at: int,
) -> dict:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            'SELECT u.*, COALESCE(c.version, 0) AS credential_version FROM users u '
            'LEFT JOIN account_credentials c ON c.user_id = u.id WHERE u.id = ?', (user_id,),
        ).fetchone()
        if row is None or row['is_banned']:
            raise CoreError('authentication_failed')
        if row['credential_version'] != expected_version:
            raise CoreError('credentials_changed')
        conn.execute(
            'INSERT INTO account_sessions(token_hash, csrf_hash, user_id, source, credential_version, '
            'created_at, authenticated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (token_hash, csrf_hash, user_id, source, expected_version, now, now, expires_at),
        )
        return dict(row)


def _session_with_conn(conn, token_hash, now):
    return conn.execute(
        'SELECT s.*, u.telegram_id FROM account_sessions s JOIN users u ON u.id = s.user_id '
        'LEFT JOIN account_credentials c ON c.user_id = s.user_id '
        'WHERE s.token_hash = ? AND s.expires_at > ? AND s.revoked_at IS NULL '
        'AND u.is_banned = 0 AND s.credential_version = COALESCE(c.version, 0)', (token_hash, now),
    ).fetchone()


def get_account_session(token_hash: str, now: int) -> dict | None:
    with get_db() as conn:
        row = _session_with_conn(conn, token_hash, now)
        return dict(row) if row else None


def revoke_account_session(token_hash: str, now: int) -> None:
    with get_db() as conn:
        conn.execute('UPDATE account_sessions SET revoked_at = ? WHERE token_hash = ?', (now, token_hash))


def consume_auth_limits(buckets: list[tuple[str, int, int]], now: int) -> bool:
    """Check and consume every shared budget atomically; keys contain no raw PII."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        states = []
        for key, limit, seconds in buckets:
            row = conn.execute('SELECT * FROM auth_rate_limits WHERE bucket = ?', (key,)).fetchone()
            start, count = (row['window_start'], row['count']) if row else (now, 0)
            if now - start >= seconds:
                start, count = now, 0
            if count >= limit:
                return False
            states.append((key, start, count + 1))
        conn.executemany(
            'INSERT INTO auth_rate_limits(bucket, window_start, count) VALUES (?, ?, ?) '
            'ON CONFLICT(bucket) DO UPDATE SET window_start = excluded.window_start, count = excluded.count',
            states,
        )
        conn.execute('DELETE FROM auth_rate_limits WHERE window_start < ?', (now - 86400,))
        return True
