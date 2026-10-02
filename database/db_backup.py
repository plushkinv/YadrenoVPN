"""Creating consistent backups of the main SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from database import connection as db_connection


__all__ = ["backup_bot_database_to", "create_bot_database_backup", "validate_backup_database"]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = PROJECT_ROOT / "backup"


def validate_backup_database(path: str | Path) -> int:
    """Read-only integrity and supported-version check before candidate migration."""
    from database.migrations import INITIAL_VERSION, LATEST_VERSION

    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        rows = connection.execute('PRAGMA integrity_check').fetchall()
        if rows != [('ok',)]:
            raise ValueError('Проверка целостности базы не пройдена')
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Нарушена целостность связей в базе')
        rows = connection.execute('SELECT version FROM schema_version').fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not int:
            raise ValueError('Некорректная версия базы в бэкапе')
        version = rows[0][0]
        if not INITIAL_VERSION <= version <= LATEST_VERSION:
            raise ValueError(
                f'Версия базы {version} не поддерживается этой установкой '
                f'(поддерживаются {INITIAL_VERSION}–{LATEST_VERSION})'
            )
        return version


def backup_bot_database_to(
    destination_path: str | Path,
    *,
    backup_root: str | Path | None = None,
) -> Path:
    """Creates a consistent copy of the main database to the specified file."""
    source_path = Path(db_connection.DB_PATH).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"База данных не найдена: {source_path}")

    raw_backup_root = Path(backup_root) if backup_root is not None else BACKUP_DIR
    if raw_backup_root.is_symlink():
        raise RuntimeError("Каталог backup не должен быть символической ссылкой")
    allowed_root = raw_backup_root.resolve()
    backup_path = Path(destination_path).resolve()
    try:
        backup_path.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError(
            f"Копию базы данных можно создавать только внутри {allowed_root}"
        ) from exc
    if backup_path == source_path:
        raise RuntimeError("Нельзя создавать резервную копию поверх рабочей базы")
    allowed_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    backup_error: Exception | None = None
    try:
        backup_path.unlink(missing_ok=True)
        source = db_connection.get_connection()
        target = sqlite3.connect(backup_path)
        source.backup(target)
        check_row = target.execute("PRAGMA quick_check").fetchone()
        if not check_row or check_row[0] != "ok":
            raise RuntimeError("Проверка целостности резервной копии не пройдена")
    except Exception as exc:
        backup_error = exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    if backup_error is not None:
        backup_path.unlink(missing_ok=True)
        raise backup_error
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("Создан пустой файл резервной копии")

    return backup_path


def create_bot_database_backup() -> str:
    """Creates and checks a SQLite backup, returning the path from the project root."""
    project_root = PROJECT_ROOT.resolve()
    raw_backup_dir = Path(BACKUP_DIR)
    if raw_backup_dir.is_symlink():
        raise RuntimeError("Каталог backup не должен быть символической ссылкой")
    backup_dir = raw_backup_dir.resolve()
    if backup_dir != project_root and project_root not in backup_dir.parents:
        raise RuntimeError("Каталог резервных копий находится вне проекта")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = (backup_dir / f"{timestamp}__database__vpn_bot.db").resolve()
    if backup_path.parent != backup_dir:
        raise RuntimeError("Некорректный путь резервной копии")

    backup_bot_database_to(backup_path, backup_root=raw_backup_dir)
    return backup_path.relative_to(PROJECT_ROOT).as_posix()
