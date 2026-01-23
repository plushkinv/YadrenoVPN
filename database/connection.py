"""
Модуль подключения к базе данных SQLite.

Предоставляет контекстный менеджер для безопасной работы с БД.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Путь к файлу базы данных
DB_PATH = Path(__file__).parent / "vpn_bot.db"


def get_connection() -> sqlite3.Connection:
    """
    Создаёт новое соединение с БД.
    
    Returns:
        sqlite3.Connection: Соединение с БД
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Доступ к полям по имени
    conn.execute("PRAGMA foreign_keys = ON")  # Включаем FK
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
