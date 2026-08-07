"""Run schema migrations against an update candidate database only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence


def _resolve_candidate(project_root: Path, database_path: Path) -> Path:
    root = project_root.resolve()
    backup_root = (root / "backup").resolve()
    candidate = database_path.resolve()
    if (root / "backup").is_symlink():
        raise RuntimeError("backup must not be a symbolic link")
    try:
        candidate.relative_to(backup_root)
    except ValueError as exc:
        raise RuntimeError("migration candidate must be located inside backup") from exc
    live_database = (root / "database" / "vpn_bot.db").resolve()
    if candidate == live_database:
        raise RuntimeError("refusing to migrate the live database")
    if not candidate.is_file() or candidate.is_symlink():
        raise RuntimeError("migration candidate is missing or is a symbolic link")
    return candidate


def _validate_database(path: Path) -> None:
    with sqlite3.connect(str(path), timeout=30) as connection:
        quick_rows = connection.execute("PRAGMA quick_check").fetchall()
        if len(quick_rows) != 1 or quick_rows[0][0] != "ok":
            raise RuntimeError(f"quick_check failed: {quick_rows[:5]}")
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise RuntimeError(f"foreign_key_check failed: {foreign_key_rows[:5]}")


def _finalize_candidate_file(path: Path) -> None:
    """Checkpoint WAL data so the candidate is a single movable SQLite file."""
    with sqlite3.connect(str(path), timeout=30) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
        if not journal_mode or str(journal_mode[0]).lower() != "delete":
            raise RuntimeError("candidate journal mode could not be finalized")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size > 0:
            raise RuntimeError(f"candidate sidecar was not checkpointed: {sidecar.name}")
        sidecar.unlink(missing_ok=True)


def run_candidate_migrations(project_root: Path, database_path: Path) -> int:
    """Migrate one validated candidate and verify the resulting schema."""
    candidate = _resolve_candidate(project_root, database_path)
    _validate_database(candidate)

    from database import connection as db_connection

    db_connection.DB_PATH = candidate
    from database import migrations

    migrations.run_migrations()
    _finalize_candidate_file(candidate)
    _validate_database(candidate)
    with sqlite3.connect(str(candidate), timeout=30) as connection:
        row = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    version = int(row[0]) if row else 0
    if version != migrations.LATEST_VERSION:
        raise RuntimeError(
            f"schema version mismatch: expected {migrations.LATEST_VERSION}, got {version}"
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": version,
                "database": str(candidate),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate a staged YadrenoVPN database")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--database", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return run_candidate_migrations(
            Path(args.project_root),
            Path(args.database),
        )
    except Exception as exc:
        print(f"Migration candidate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
