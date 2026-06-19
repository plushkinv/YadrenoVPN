"""Создание согласованных резервных копий основной SQLite-базы."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database import connection as db_connection


__all__ = ["create_bot_database_backup"]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = PROJECT_ROOT / "backup"


def create_bot_database_backup() -> str:
    """Создаёт и проверяет SQLite-бэкап, возвращая путь от корня проекта."""
    source_path = Path(db_connection.DB_PATH).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"База данных не найдена: {source_path}")

    project_root = PROJECT_ROOT.resolve()
    backup_dir = BACKUP_DIR.resolve()
    if backup_dir != project_root and project_root not in backup_dir.parents:
        raise RuntimeError("Каталог резервных копий находится вне проекта")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = (backup_dir / f"{timestamp}__database__vpn_bot.db").resolve()
    if backup_path.parent != backup_dir:
        raise RuntimeError("Некорректный путь резервной копии")

    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
            check_row = target.execute("PRAGMA quick_check").fetchone()
            if not check_row or check_row[0] != "ok":
                raise RuntimeError("Проверка целостности резервной копии не пройдена")
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise

    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("Создан пустой файл резервной копии")

    return backup_path.relative_to(PROJECT_ROOT).as_posix()
