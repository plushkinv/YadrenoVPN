"""
Модуль подключения к базе данных SQLite.

Предоставляет контекстный менеджер для безопасной работы с БД.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import config
except ModuleNotFoundError as e:
    if e.name != "config":
        raise
    config = None

# Путь к файлу базы данных
DB_PATH = Path(__file__).parent / "vpn_bot.db"

DEFAULT_SQLITE_JOURNAL_MODE = "WAL"
DEFAULT_SQLITE_SYNCHRONOUS = "NORMAL"
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 10000
DEFAULT_SQLITE_CACHE_SIZE_KB = 32768
DEFAULT_SQLITE_TEMP_STORE = "MEMORY"
DEFAULT_SQLITE_MMAP_SIZE_BYTES = 134217728

_ALLOWED_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
_ALLOWED_SYNCHRONOUS = {"OFF", "NORMAL", "FULL", "EXTRA"}
_ALLOWED_TEMP_STORE = {"DEFAULT", "FILE", "MEMORY"}


def _config_value(name: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, name, default)


def _int_config(name: str, default: int, *, min_value: int = 0) -> int:
    value = _config_value(name, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    if value < min_value:
        return default
    return value


def _string_config(name: str, default: str, allowed: set[str]) -> str:
    value = str(_config_value(name, default)).upper()
    if value not in allowed:
        return default
    return value


def get_sqlite_busy_timeout_ms() -> int:
    """Возвращает настроенный таймаут ожидания освобождения SQLite."""
    return _int_config(
        "SQLITE_BUSY_TIMEOUT_MS",
        DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    )


def _apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    """Применяет безопасные PRAGMA-настройки к новому подключению."""
    journal_mode = _string_config(
        "SQLITE_JOURNAL_MODE",
        DEFAULT_SQLITE_JOURNAL_MODE,
        _ALLOWED_JOURNAL_MODES,
    )
    synchronous = _string_config(
        "SQLITE_SYNCHRONOUS",
        DEFAULT_SQLITE_SYNCHRONOUS,
        _ALLOWED_SYNCHRONOUS,
    )
    temp_store = _string_config(
        "SQLITE_TEMP_STORE",
        DEFAULT_SQLITE_TEMP_STORE,
        _ALLOWED_TEMP_STORE,
    )
    busy_timeout_ms = get_sqlite_busy_timeout_ms()
    cache_size_kb = _int_config(
        "SQLITE_CACHE_SIZE_KB",
        DEFAULT_SQLITE_CACHE_SIZE_KB,
        min_value=1,
    )
    mmap_size_bytes = _int_config(
        "SQLITE_MMAP_SIZE_BYTES",
        DEFAULT_SQLITE_MMAP_SIZE_BYTES,
    )

    conn.execute(f"PRAGMA journal_mode = {journal_mode}")
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    conn.execute(f"PRAGMA cache_size = {-cache_size_kb}")
    conn.execute(f"PRAGMA temp_store = {temp_store}")
    conn.execute(f"PRAGMA mmap_size = {mmap_size_bytes}")
    conn.execute("PRAGMA foreign_keys = ON")


def get_connection() -> sqlite3.Connection:
    """
    Создаёт новое соединение с БД.
    
    Returns:
        sqlite3.Connection: Соединение с БД
    """
    timeout_seconds = get_sqlite_busy_timeout_ms() / 1000
    conn = sqlite3.connect(DB_PATH, timeout=timeout_seconds)
    conn.row_factory = sqlite3.Row  # Доступ к полям по имени
    _apply_connection_pragmas(conn)
    return conn


@contextmanager
def get_db():
    """
    Контекстный менеджер для работы с БД.
    
    Автоматически делает commit при успехе и rollback при ошибке.
    
    Пример:
        with get_db() as conn:
            cursor = conn.execute("SELECT * FROM users")
            users = cursor.fetchall()
    
    Yields:
        sqlite3.Connection: Соединение с БД
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
