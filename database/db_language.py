"""Read the installation language before or after migration without creating a database."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from localization import DEFAULT_LANGUAGE, normalize_language


def initial_language() -> str:
    """Configuration is an input only while creating a genuinely new database."""
    from database import connection
    return normalize_language(getattr(connection.config, 'INSTALLATION_LANGUAGE', DEFAULT_LANGUAGE))


@lru_cache(maxsize=8)
def _read_language(path: str, seed: str, exists: bool) -> str:
    if not exists:
        return normalize_language(seed)
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as conn:
        has_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if not has_settings:
            return normalize_language(seed)
        row = conn.execute("SELECT value FROM settings WHERE key='installation_language'").fetchone()
        # A supported installation created before localization always remains Russian.
        return normalize_language(row[0]) if row else DEFAULT_LANGUAGE


def get_installation_language(*, conn=None) -> str:
    if conn is not None:
        row = conn.execute("SELECT value FROM settings WHERE key='installation_language'").fetchone()
        return normalize_language(row[0]) if row else DEFAULT_LANGUAGE
    from database import connection
    path = Path(connection.DB_PATH).resolve()
    # Do not validate a changed bootstrap value when the database already owns its language.
    seed = getattr(connection.config, 'INSTALLATION_LANGUAGE', DEFAULT_LANGUAGE)
    return _read_language(str(path), str(seed), path.is_file())


def refresh_installation_language() -> str:
    """Refresh after schema initialization; restoration restarts the owning process."""
    _read_language.cache_clear()
    return get_installation_language()


__all__ = ['get_installation_language']
