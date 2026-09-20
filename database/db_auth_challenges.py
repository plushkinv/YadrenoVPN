"""Single-use SMS challenges; provider success alone never authenticates anyone."""
from __future__ import annotations

import hmac

from .connection import get_db

__all__ = ['create_auth_challenge', 'finish_auth_challenge_send', 'verify_auth_challenge']


def create_auth_challenge(
    *, challenge_id: str, phone: str, purpose: str, code_hash: str, now: int,
    session_hash: str | None = None, user_id: int | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            'INSERT INTO auth_challenges(id, phone, purpose, code_hash, state, created_at, '
            "expires_at, session_hash, user_id) VALUES (?, ?, ?, ?, 'sending', ?, ?, ?, ?)",
            (challenge_id, phone, purpose, code_hash, now, now + 300, session_hash, user_id),
        )
        conn.execute('DELETE FROM auth_challenges WHERE expires_at < ?', (now - 86400,))


def finish_auth_challenge_send(challenge_id: str, state: str) -> None:
    if state not in ('sent', 'failed', 'unknown'):
        raise ValueError('Invalid SMS send outcome')
    with get_db() as conn:
        conn.execute("UPDATE auth_challenges SET state = ? WHERE id = ? AND state = 'sending'",
                     (state, challenge_id))


def verify_auth_challenge(
    *, challenge_id: str, code_hash: str, proof_hash: str, now: int,
    session_hash: str | None = None,
) -> bool:
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM auth_challenges WHERE id = ?', (challenge_id,)).fetchone()
        # An unknown provider outcome may still have delivered the correct code.
        # Its possession, not the transport response, proves access to the phone.
        if (row is None or row['state'] not in ('sent', 'unknown', 'sending')
                or row['expires_at'] <= now or row['attempts'] >= 5
                or (row['session_hash'] is not None and row['session_hash'] != session_hash)):
            return False
        valid = hmac.compare_digest(row['code_hash'], code_hash)
        conn.execute(
            'UPDATE auth_challenges SET attempts = attempts + 1, state = ?, '
            'proof_hash = ?, verified_at = ? WHERE id = ?',
            ('verified' if valid else row['state'], proof_hash if valid else None,
             now if valid else None, challenge_id),
        )
        return valid
