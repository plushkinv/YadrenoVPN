"""Persistent phone-account names, allocated in the caller's write transaction."""
from __future__ import annotations

import json
import secrets


DEFAULT_ACCOUNT_NAME_POOL = (
    'Ядрёный енот',
    'Реактивный боббер',
    'Неуловимый кот',
    'Ядрён батон',
    'Ядрёный овощ',
    'Турбированный хомяк',
    'Шальной пельмень',
    'Космический карась',
    'Бронебойный кабачок',
    'Атомный огурец',
    'Сверхзвуковой гусь',
    'Заряженный барсук',
)


def parse_account_name_pool(value: str) -> list[str]:
    """Validate the stored JSON list and trim each phrase for allocation."""
    pool = json.loads(value)
    if not isinstance(pool, list) or len(pool) < 3:
        raise ValueError('account_name_pool must be a JSON array with at least three distinct names')
    if any(not isinstance(name, str) or not name.strip() for name in pool):
        raise ValueError('account_name_pool names must be nonempty strings')
    names = [name.strip() for name in pool]
    if len(set(names)) != len(names):
        raise ValueError('account_name_pool names must be distinct after trimming')
    return names


def account_name_pool_with_conn(conn) -> list[str]:
    """Read the current value without caching or a separate fallback setting."""
    value = conn.execute("SELECT value FROM settings WHERE key='account_name_pool'").fetchone()[0]
    return parse_account_name_pool(value)


def available_account_name_with_conn(conn, phone: str, *, pool: list[str] | None = None) -> str:
    """Choose an unused full name; the caller saves it before committing its write lock."""
    names = account_name_pool_with_conn(conn) if pool is None else pool

    def available(name):
        return conn.execute("SELECT 1 FROM users WHERE account_name=? AND account_name<>''", (name,)).fetchone() is None

    for name in secrets.SystemRandom().sample(names, len(names)):
        candidate = f'{name} {phone[-3:]}'
        if available(candidate):
            return candidate
    digits = 4
    while True:
        number = 10 ** (digits - 1) + secrets.randbelow(9 * 10 ** (digits - 1))
        candidate = f'{secrets.choice(names)} {number}'
        if available(candidate):
            return candidate
        digits = min(digits + 1, 10)
