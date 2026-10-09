"""Single-use browser-bound Telegram website authentication challenges."""
from .connection import get_db

__all__ = ['create_telegram_login_challenge', 'consume_telegram_login_challenge']


def create_telegram_login_challenge(browser_hash: str, nonce_hash: str, now: int) -> None:
    with get_db() as conn:
        conn.execute('DELETE FROM telegram_login_challenges WHERE expires_at <= ?', (now,))
        conn.execute('INSERT INTO telegram_login_challenges(browser_hash,nonce_hash,expires_at) '
                     'VALUES(?,?,?)', (browser_hash, nonce_hash, now + 300))


def consume_telegram_login_challenge(browser_hash: str, nonce_hash: str, now: int) -> bool:
    with get_db() as conn:
        return conn.execute('DELETE FROM telegram_login_challenges WHERE browser_hash=? '
                            'AND nonce_hash=? AND expires_at>?', (browser_hash, nonce_hash, now)).rowcount == 1
