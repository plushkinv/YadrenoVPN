"""Atomic, single-use phone challenges; delivery alone is not authentication."""
from __future__ import annotations

import hmac

from .connection import get_db

__all__ = [
    'create_auth_challenge', 'get_auth_challenge', 'finish_auth_challenge_send',
    'verify_auth_challenge', 'reserve_auth_challenge_attempt', 'verify_auth_provider_challenge',
    'claim_auth_provider_check', 'update_auth_provider_result', 'hint_auth_provider_check',
    'fail_exhausted_auth_challenge',
]


def create_auth_challenge(
    *, challenge_id: str, phone: str, purpose: str, method: str, code_hash: str | None,
    provider_config_hash: str, now: int,
    session_hash: str | None = None, user_id: int | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            'INSERT INTO auth_challenges(id, phone, purpose, method, code_hash, provider_config_hash, state, created_at, '
            "expires_at, session_hash, user_id) VALUES (?, ?, ?, ?, ?, ?, 'sending', ?, ?, ?, ?)",
            (challenge_id, phone, purpose, method, code_hash, provider_config_hash, now, now + 300, session_hash, user_id),
        )
        conn.execute('DELETE FROM auth_challenges WHERE expires_at < ?', (now - 86400,))


def get_auth_challenge(challenge_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute('SELECT * FROM auth_challenges WHERE id=?', (challenge_id,)).fetchone()
        return dict(row) if row else None


def finish_auth_challenge_send(challenge_id: str, state: str, provider_state: str, provider_id: str | None) -> None:
    if state not in ('sent', 'failed', 'unknown'):
        raise ValueError('Invalid verification send outcome')
    with get_db() as conn:
        conn.execute("UPDATE auth_challenges SET state=?, provider_state=?, provider_request_id=? "
                     "WHERE id=? AND state='sending'", (state, provider_state, provider_id, challenge_id))


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
                or row['code_hash'] is None or row['session_hash'] != session_hash):
            return False
        valid = hmac.compare_digest(row['code_hash'], code_hash)
        conn.execute(
            'UPDATE auth_challenges SET attempts = attempts + 1, state = ?, '
            'proof_hash = ?, verified_at = ? WHERE id = ?',
            ('verified' if valid else 'failed' if row['attempts'] == 4 else row['state'], proof_hash if valid else None,
             now if valid else None, challenge_id),
        )
        return valid


def reserve_auth_challenge_attempt(challenge_id: str, now: int, session_hash: str | None) -> bool:
    with get_db() as conn:
        return conn.execute(
            "UPDATE auth_challenges SET attempts=attempts+1 WHERE id=? AND method='smsaero_mobile' "
            "AND state IN ('sending','sent','unknown') AND expires_at>? AND session_hash IS ? AND attempts<5",
            (challenge_id, now, session_hash),
        ).rowcount == 1


def fail_exhausted_auth_challenge(challenge_id: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE auth_challenges SET state='failed' WHERE id=? AND attempts>=5 "
                     "AND state IN ('sending','sent','unknown') AND provider_state!='confirmed'", (challenge_id,))


def verify_auth_provider_challenge(*, challenge_id: str, proof_hash: str, now: int,
                                   session_hash: str | None) -> bool:
    with get_db() as conn:
        return conn.execute(
            "UPDATE auth_challenges SET state='verified', proof_hash=?, verified_at=? WHERE id=? "
            "AND method='smsaero_mobile' AND provider_state='confirmed' "
            "AND state IN ('sent','unknown') AND expires_at>? AND session_hash IS ?",
            (proof_hash, now, challenge_id, now, session_hash),
        ).rowcount == 1


def claim_auth_provider_check(challenge_id: str, now: int) -> bool:
    with get_db() as conn:
        return conn.execute(
            "UPDATE auth_challenges SET next_provider_check_at=? WHERE id=? "
            "AND method='smsaero_mobile' AND provider_request_id IS NOT NULL "
            "AND state IN ('sent','unknown') AND provider_state NOT IN ('confirmed','failed') "
            "AND expires_at>? AND next_provider_check_at<=?",
            (now + 5, challenge_id, now, now),
        ).rowcount == 1


def update_auth_provider_result(challenge_id: str, state: str, now: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE auth_challenges SET provider_state=?, state=CASE WHEN ?='failed' THEN 'failed' ELSE 'sent' END "
            "WHERE id=? AND state IN ('sent','unknown') AND provider_state NOT IN ('confirmed','failed') AND expires_at>?",
            (state, state, challenge_id, now),
        )


def hint_auth_provider_check(provider_id: str, phone: str, now: int) -> None:
    """An unsigned webhook may request rechecking, never attest phone ownership."""
    with get_db() as conn:
        conn.execute(
            "UPDATE auth_challenges SET next_provider_check_at=MIN(next_provider_check_at, ?) "
            "WHERE method='smsaero_mobile' AND provider_request_id=? AND phone=? "
            "AND state IN ('sent','unknown') AND provider_state NOT IN ('confirmed','failed') AND expires_at>?",
            (now + 1, provider_id, phone, now),
        )
