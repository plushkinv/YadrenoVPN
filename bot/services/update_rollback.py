"""Pre-update database snapshots and full update rollback orchestration.

The module deliberately depends only on the Python standard library. A copy of
this file is stored inside every pre-update snapshot so a rollback worker keeps
running even after the Git worktree is reset to an older commit.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


logger = logging.getLogger(__name__)

MANIFEST_FORMAT_VERSION = 1
PRE_UPDATE_DIRNAME = "pre_update"
MANIFEST_FILENAME = "manifest.json"
DATABASE_BACKUP_FILENAME = "vpn_bot.db"
MIGRATION_CANDIDATE_FILENAME = "migration_candidate.db"
FAILED_MIGRATION_CANDIDATE_FILENAME = "migration_failed.db"
ROLLBACK_RUNNER_FILENAME = "rollback_runner.py"
ROLLBACK_RESULT_FILENAME = "rollback_result.json"
UPDATE_RESULT_FILENAME = "update_result.json"
UPDATE_HEALTH_FILENAME = "update_health.json"
OPERATION_LOCK_FILENAME = ".operation.lock"
MAX_ROLLBACK_POINTS = 3
ROLLBACK_RETENTION_DAYS = 7
SERVICE_NAME = "yadreno-vpn"
UNKNOWN_RELEASE = "unknown"
UPDATE_STARTUP_TIMEOUT_SECONDS = 120
UPDATE_STABLE_SECONDS = 10
UPDATE_ACTIVATION_TIMEOUT_SECONDS = 60
DATABASE_CHECK_ERROR_LIMIT = 5
ORDERED_UPDATE_MODES = frozenset({"admin_regular", "installer_update"})

ELIGIBLE_ROLLBACK_STATUSES = {"applied", "applied_with_errors"}
_RELEASE_PREFIX_RE = re.compile(
    r"^[!?]?\s*Версия\s+([0-9]+(?:\.[0-9]+)*)\b",
    flags=re.IGNORECASE,
)
_SNAPSHOT_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{12}Z_[0-9a-f]{8}$",
    flags=re.IGNORECASE,
)


class UpdateRollbackError(RuntimeError):
    """Raised when a snapshot or rollback operation cannot complete safely."""


class DatabaseIntegrityError(UpdateRollbackError):
    """Raised when an SQLite database fails a required integrity check."""


@dataclass(frozen=True)
class PreparedUpdateSnapshot:
    """A verified database snapshot created immediately before an update."""

    snapshot_id: str
    snapshot_dir: Path
    manifest_path: Path
    source_commit: str
    source_release: str


@dataclass(frozen=True)
class RollbackPoint:
    """A validated rollback target exposed to the administrator or installer."""

    snapshot_id: str
    snapshot_dir: Path
    manifest_path: Path
    database_path: Path
    created_at: datetime
    source_release: str
    source_commit: str
    source_short_commit: str
    applied_commit: str
    applied_release: str
    update_mode: str

    @property
    def display_release(self) -> str:
        """Return a human-readable release label."""
        return (
            f"Версия {self.source_release}"
            if self.source_release and self.source_release != UNKNOWN_RELEASE
            else "Версия не определена"
        )


@dataclass(frozen=True)
class RollbackExecutionResult:
    """Final result of a rollback worker."""

    success: bool
    message: str
    recovered: bool = False


@dataclass(frozen=True)
class UpdateExecutionResult:
    """Final result of a managed update transaction."""

    success: bool
    message: str
    snapshot_id: str
    rolled_back: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise UpdateRollbackError("Snapshot creation time is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateRollbackError("Snapshot creation time is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_detail(value: Any, *, limit: int = 1200) -> str:
    """Return a bounded single diagnostic suitable for journals and Telegram."""
    normalized = str(value or "Нет подробностей.").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _repository_guard_is_active() -> bool:
    """Return the in-process Yadreno Admin repository-fence state."""
    try:
        from bot.services.yadreno_admin_core_guard import is_repository_guard_active
    except ImportError:
        return False
    return bool(is_repository_guard_active())


def _resolve_project_root(project_root: str | Path | None = None) -> Path:
    root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    root = root.resolve()
    if not (root / ".git").exists():
        raise UpdateRollbackError(f"Git repository is missing: {root}")
    return root


def _pre_update_root(project_root: Path) -> Path:
    return project_root / "backup" / PRE_UPDATE_DIRNAME


def _database_path(project_root: Path) -> Path:
    return project_root / "database" / DATABASE_BACKUP_FILENAME


def _ensure_inside(path: Path, parent: Path) -> Path:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    if resolved_path != resolved_parent and resolved_parent not in resolved_path.parents:
        raise UpdateRollbackError(f"Path escapes the allowed directory: {resolved_path}")
    return resolved_path


def _safe_snapshot_dir(project_root: Path, snapshot_id: str) -> Path:
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id or ""):
        raise UpdateRollbackError("Invalid rollback snapshot identifier")
    raw_root = _pre_update_root(project_root)
    if raw_root.is_symlink():
        raise UpdateRollbackError("Pre-update backup root must not be a symbolic link")
    root = _ensure_inside(raw_root, project_root)
    return _ensure_inside(root / snapshot_id, root)


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise UpdateRollbackError(f"Command is unavailable: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateRollbackError(
            f"Command timed out after {timeout} seconds: {' '.join(args)}"
        ) from exc


def _run_checked(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 120,
    stage: str,
) -> str:
    result = _run_command(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise UpdateRollbackError(
            f"{stage} failed (exit {result.returncode}): {output or 'no output'}"
        )
    return (result.stdout + result.stderr).strip()


def _git_output(
    project_root: Path,
    args: Sequence[str],
    *,
    timeout: int = 120,
    stage: str,
) -> str:
    return _run_checked(
        ["git", *args],
        cwd=project_root,
        timeout=timeout,
        stage=stage,
    )


def _validated_backup_root(project_root: Path) -> Path:
    """Return the canonical ignored backup directory or fail closed."""
    raw_root = project_root / "backup"
    if raw_root.is_symlink():
        raise UpdateRollbackError("Каталог backup не должен быть символической ссылкой")
    raw_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_root = _ensure_inside(raw_root, project_root)

    tracked = _run_command(
        ["git", "ls-files", "--", "backup"],
        cwd=project_root,
        timeout=30,
    )
    if tracked.returncode != 0:
        raise UpdateRollbackError("Не удалось проверить tracked-файлы в backup")
    if tracked.stdout.strip():
        raise UpdateRollbackError(
            "Каталог backup содержит файлы под контролем Git; обновление отменено"
        )

    ignored = _run_command(
        ["git", "check-ignore", "-q", "--", "backup"],
        cwd=project_root,
        timeout=30,
    )
    if ignored.returncode != 0:
        raise UpdateRollbackError(
            "Каталог backup не игнорируется Git; обновление отменено"
        )
    return backup_root


def _validate_persistent_git_ignores(project_root: Path) -> None:
    """Require every installation-local persistent path to stay Git-ignored."""
    required_paths = (
        "backup/",
        "config.py",
        "database/vpn_bot.db",
        "database/vpn_bot.db-wal",
        "database/vpn_bot.db-shm",
        "custom_extensions/",
        "logs/",
        "venv/",
    )
    missing: list[str] = []
    for path in required_paths:
        ignored = _run_command(
            ["git", "check-ignore", "-q", "--", path],
            cwd=project_root,
            timeout=30,
        )
        if ignored.returncode != 0:
            missing.append(path)
    if missing:
        raise UpdateRollbackError(
            "Локальные persistent-пути не игнорируются Git: " + ", ".join(missing)
        )


def _current_commit(project_root: Path) -> str:
    commit = _git_output(
        project_root,
        ["rev-parse", "HEAD"],
        stage="Resolving current Git commit",
    ).splitlines()[0].strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise UpdateRollbackError("Current Git commit has an invalid hash")
    return commit.lower()


def _current_branch(project_root: Path) -> str:
    result = _run_command(
        ["git", "branch", "--show-current"],
        cwd=project_root,
        timeout=30,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _commit_exists(project_root: Path, commit: str) -> bool:
    result = _run_command(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=project_root,
        timeout=30,
    )
    return result.returncode == 0


def _commit_subject(project_root: Path, commit: str) -> str:
    result = _run_command(
        ["git", "show", "-s", "--format=%s", commit],
        cwd=project_root,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _first_blocking_commit_between(
    project_root: Path,
    *,
    source_commit: str,
    target_commit: str,
) -> str | None:
    """Return the first marked commit in update order, if one exists."""
    output = _git_output(
        project_root,
        [
            "log",
            f"{source_commit}..{target_commit}",
            "--format=%H|%s",
            "--reverse",
        ],
        timeout=30,
        stage="Resolving blocking update order",
    )
    for line in output.splitlines():
        commit, separator, subject = line.partition("|")
        if not separator or not subject.strip().startswith("!"):
            continue
        commit = commit.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise UpdateRollbackError("Blocking update has an invalid commit hash")
        return commit
    return None


def _plain_telegram_html(value: str) -> str:
    """Convert the small trusted blocking-message HTML subset for console output."""
    without_tags = re.sub(r"<[^>]+>", "", str(value or ""))
    return html.unescape(without_tags).strip()


def _ensure_ordered_update_unblocked() -> None:
    """Fail closed while the installed blocking-update condition is unmet."""
    try:
        from bot.utils.update_block import (
            get_blocked_message,
            is_update_blocked,
            try_unblock,
        )
    except Exception as exc:
        raise UpdateRollbackError(
            "Не удалось проверить условия штатного обновления"
        ) from exc

    try_unblock()
    if is_update_blocked():
        message = _plain_telegram_html(get_blocked_message())
        raise UpdateRollbackError(
            message or "Штатные обновления приостановлены до выполнения условия."
        )


def _resolve_ordered_update_stage(
    project_root: Path,
    *,
    update_mode: str,
    source_commit: str,
    target_commit: str,
    block_updates: bool,
) -> tuple[str, bool]:
    """Apply the marked-release gate to normal, non-emergency update modes."""
    if update_mode not in ORDERED_UPDATE_MODES:
        return target_commit, block_updates

    _ensure_ordered_update_unblocked()
    blocking_commit = _first_blocking_commit_between(
        project_root,
        source_commit=source_commit,
        target_commit=target_commit,
    )
    if blocking_commit is None:
        return target_commit, block_updates

    logger.info(
        "Ordered update %s stops at blocking commit %s",
        update_mode,
        blocking_commit[:8],
    )
    return blocking_commit, True


def _release_from_subject(subject: str) -> str:
    match = _RELEASE_PREFIX_RE.match((subject or "").strip())
    return match.group(1) if match else UNKNOWN_RELEASE


def get_current_version_identity(
    project_root: str | Path | None = None,
) -> tuple[str, str, str]:
    """Return ``(release, full_commit, short_commit)`` for the live worktree."""
    root = _resolve_project_root(project_root)
    commit = _current_commit(root)
    release = _release_from_subject(_commit_subject(root, commit))
    return release, commit, commit[:8]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_foreign_key_errors(rows: Sequence[Sequence[Any]]) -> str:
    details: list[str] = []
    for row in list(rows)[:DATABASE_CHECK_ERROR_LIMIT]:
        values = list(row)
        table = values[0] if len(values) > 0 else "?"
        row_id = values[1] if len(values) > 1 else "?"
        parent = values[2] if len(values) > 2 else "?"
        foreign_key_id = values[3] if len(values) > 3 else "?"
        details.append(
            f"table={table}, rowid={row_id}, parent={parent}, fk={foreign_key_id}"
        )
    suffix = "" if len(rows) <= DATABASE_CHECK_ERROR_LIMIT else f"; всего={len(rows)}"
    return "; ".join(details) + suffix


def _check_database_integrity(
    path: Path,
    *,
    check_foreign_keys: bool = True,
) -> None:
    """Validate SQLite structure and, optionally, all declared foreign keys."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise UpdateRollbackError(f"Database backup is missing or empty: {path}")
    connection: sqlite3.Connection | None = None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, timeout=30, uri=True)
        quick_rows = connection.execute("PRAGMA quick_check").fetchall()
        foreign_key_rows = (
            connection.execute("PRAGMA foreign_key_check").fetchall()
            if check_foreign_keys
            else []
        )
    except sqlite3.Error as exc:
        raise UpdateRollbackError(f"Cannot validate SQLite backup: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    if len(quick_rows) != 1 or quick_rows[0][0] != "ok":
        details = "; ".join(str(row[0]) for row in quick_rows[:DATABASE_CHECK_ERROR_LIMIT])
        raise DatabaseIntegrityError(
            "PRAGMA quick_check не пройден"
            + (f": {details}" if details else "")
        )
    if foreign_key_rows:
        raise DatabaseIntegrityError(
            "PRAGMA foreign_key_check обнаружил нарушения: "
            + _format_foreign_key_errors(foreign_key_rows)
        )


def _quick_check_database(path: Path) -> None:
    """Backward-compatible structural-only SQLite validation helper."""
    _check_database_integrity(path, check_foreign_keys=False)


def _copy_database(source_path: Path, destination_path: Path) -> None:
    """Create a consistent SQLite copy without deciding validation policy."""
    if not source_path.is_file():
        raise UpdateRollbackError(f"Bot database is missing: {source_path}")
    destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination_path.unlink(missing_ok=True)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(str(source_path), timeout=30)
        target = sqlite3.connect(str(destination_path), timeout=30)
        source.backup(target)
        try:
            destination_path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    if not destination_path.is_file():
        raise UpdateRollbackError("SQLite backup copy was not created")


def _backup_database(source_path: Path, destination_path: Path) -> None:
    """Create a consistent SQLite copy and require full integrity."""
    try:
        _copy_database(source_path, destination_path)
        _check_database_integrity(destination_path)
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Best-effort durability barrier for an atomic file/directory rename."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateRollbackError(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateRollbackError(f"JSON root must be an object: {path}")
    return data


def _load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    manifest = _load_json(snapshot_dir / MANIFEST_FILENAME)
    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        raise UpdateRollbackError("Unsupported rollback manifest format")
    if manifest.get("kind") != "pre_update":
        raise UpdateRollbackError("Snapshot is not marked as pre_update")
    if manifest.get("snapshot_id") != snapshot_dir.name:
        raise UpdateRollbackError("Snapshot directory and manifest identifiers differ")
    return manifest


def _point_from_manifest(
    project_root: Path,
    snapshot_dir: Path,
    manifest: dict[str, Any],
    *,
    verify_integrity: bool,
) -> RollbackPoint:
    source = manifest.get("source")
    update = manifest.get("update")
    database = manifest.get("database")
    if not isinstance(source, dict) or not isinstance(update, dict) or not isinstance(database, dict):
        raise UpdateRollbackError("Rollback manifest sections are missing")
    if update.get("status") not in ELIGIBLE_ROLLBACK_STATUSES:
        raise UpdateRollbackError("Snapshot update was not applied")

    source_commit = str(source.get("commit") or "").lower()
    source_short = str(source.get("short_commit") or "").lower()
    applied_commit = str(update.get("applied_commit") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
        raise UpdateRollbackError("Snapshot source commit is invalid")
    if source_short != source_commit[:8]:
        raise UpdateRollbackError("Snapshot short commit does not match the full commit")
    if not re.fullmatch(r"[0-9a-f]{40,64}", applied_commit):
        raise UpdateRollbackError("Snapshot applied commit is invalid")
    if not _commit_exists(project_root, source_commit):
        raise UpdateRollbackError("Snapshot source commit is unavailable locally")

    database_file = database.get("file")
    if database_file != DATABASE_BACKUP_FILENAME:
        raise UpdateRollbackError("Snapshot database filename is invalid")
    database_path = _ensure_inside(
        snapshot_dir / DATABASE_BACKUP_FILENAME,
        snapshot_dir,
    )
    expected_size = database.get("size")
    expected_hash = database.get("sha256")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise UpdateRollbackError("Snapshot database size is invalid")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise UpdateRollbackError("Snapshot database checksum is invalid")
    if not database_path.is_file() or database_path.stat().st_size != expected_size:
        raise UpdateRollbackError("Snapshot database file is missing or has a wrong size")
    runner_path = _ensure_inside(
        snapshot_dir / ROLLBACK_RUNNER_FILENAME,
        snapshot_dir,
    )
    if not runner_path.is_file() or runner_path.is_symlink():
        raise UpdateRollbackError("Snapshot rollback runner is missing")
    if verify_integrity:
        if _sha256_file(database_path) != expected_hash:
            raise UpdateRollbackError("Snapshot database checksum does not match")
        _check_database_integrity(database_path)

    return RollbackPoint(
        snapshot_id=snapshot_dir.name,
        snapshot_dir=snapshot_dir,
        manifest_path=snapshot_dir / MANIFEST_FILENAME,
        database_path=database_path,
        created_at=_parse_datetime(manifest.get("created_at")),
        source_release=str(source.get("release") or UNKNOWN_RELEASE),
        source_commit=source_commit,
        source_short_commit=source_short,
        applied_commit=applied_commit,
        applied_release=str(update.get("applied_release") or UNKNOWN_RELEASE),
        update_mode=str(update.get("mode") or "unknown"),
    )


def create_pre_update_snapshot(
    *,
    update_mode: str,
    requested_target: str | None = None,
    actor: str | None = None,
    project_root: str | Path | None = None,
) -> PreparedUpdateSnapshot:
    """Create and verify a database snapshot before a Git worktree mutation."""
    root = _resolve_project_root(project_root)
    backup_root = _validated_backup_root(root)
    _validate_persistent_git_ignores(root)
    commit = _current_commit(root)
    release = _release_from_subject(_commit_subject(root, commit))
    created_at = _utc_now()
    snapshot_id = (
        created_at.strftime("%Y%m%dT%H%M%S%fZ")
        + "_"
        + commit[:8]
    )
    raw_snapshot_root = backup_root / PRE_UPDATE_DIRNAME
    if raw_snapshot_root.is_symlink():
        raise UpdateRollbackError("Pre-update backup root must not be a symbolic link")
    raw_snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot_root = _ensure_inside(raw_snapshot_root, root)
    final_dir = _safe_snapshot_dir(root, snapshot_id)
    temp_dir = _ensure_inside(
        snapshot_root / f".{snapshot_id}.tmp-{os.getpid()}",
        snapshot_root,
    )
    if final_dir.exists() or temp_dir.exists():
        raise UpdateRollbackError("Pre-update snapshot identifier collision")

    temp_dir.mkdir(mode=0o700)
    preflight_error: Exception | None = None
    try:
        backup_path = temp_dir / DATABASE_BACKUP_FILENAME
        database_size: int | None = None
        database_hash: str | None = None
        try:
            _copy_database(_database_path(root), backup_path)
            database_size = backup_path.stat().st_size
            database_hash = _sha256_file(backup_path)
            _check_database_integrity(backup_path)
        except Exception as exc:
            preflight_error = exc

        runner_path = temp_dir / ROLLBACK_RUNNER_FILENAME
        shutil.copy2(Path(__file__).resolve(), runner_path)
        try:
            runner_path.chmod(0o700)
        except OSError:
            pass

        manifest = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "kind": "pre_update",
            "snapshot_id": snapshot_id,
            "created_at": _isoformat_utc(created_at),
            "source": {
                "release": release,
                "commit": commit,
                "short_commit": commit[:8],
                "branch": _current_branch(root),
            },
            "update": {
                "mode": str(update_mode or "unknown"),
                "requested_target": requested_target,
                "actor": actor,
                "status": "preflight_failed" if preflight_error else "prepared",
                "applied_at": None,
                "applied_commit": None,
                "applied_release": None,
            },
            "database": {
                "file": DATABASE_BACKUP_FILENAME if backup_path.is_file() else None,
                "size": database_size,
                "sha256": database_hash,
            },
            "checks": {
                "preflight": "failed" if preflight_error else "passed",
                "preflight_error": str(preflight_error) if preflight_error else None,
                "final_preflight": None,
                "migration": None,
                "post_migration": None,
            },
        }
        _atomic_write_json(temp_dir / MANIFEST_FILENAME, manifest)
        os.replace(temp_dir, final_dir)
        _fsync_directory(snapshot_root)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    if preflight_error is not None:
        raise DatabaseIntegrityError(
            "Обновление не началось: проверка базы данных не пройдена. "
            f"{preflight_error}. Диагностический snapshot: {snapshot_id}"
        ) from preflight_error

    return PreparedUpdateSnapshot(
        snapshot_id=snapshot_id,
        snapshot_dir=final_dir,
        manifest_path=final_dir / MANIFEST_FILENAME,
        source_commit=commit,
        source_release=release,
    )


def mark_snapshot_applied(
    snapshot_id: str,
    *,
    project_root: str | Path | None = None,
    with_errors: bool = False,
) -> RollbackPoint:
    """Mark a prepared snapshot as an available rollback point."""
    root = _resolve_project_root(project_root)
    snapshot_dir = _safe_snapshot_dir(root, snapshot_id)
    manifest = _load_manifest(snapshot_dir)
    applied_commit = _current_commit(root)
    applied_release = _release_from_subject(_commit_subject(root, applied_commit))
    update = manifest.get("update")
    if not isinstance(update, dict):
        raise UpdateRollbackError("Rollback manifest update section is missing")
    update.update(
        {
            "status": "applied_with_errors" if with_errors else "applied",
            "applied_at": _isoformat_utc(_utc_now()),
            "applied_commit": applied_commit,
            "applied_release": applied_release,
        }
    )
    _atomic_write_json(snapshot_dir / MANIFEST_FILENAME, manifest)
    cleanup_pre_update_snapshots(
        project_root=root,
        retention_days=ROLLBACK_RETENTION_DAYS,
        max_points=MAX_ROLLBACK_POINTS,
    )
    return _point_from_manifest(
        root,
        snapshot_dir,
        manifest,
        verify_integrity=True,
    )


def finalize_snapshot_after_git(
    snapshot: PreparedUpdateSnapshot,
    *,
    git_succeeded: bool,
    project_root: str | Path | None = None,
) -> bool:
    """Finalize a snapshot only when the update changed ``HEAD``."""
    root = _resolve_project_root(project_root)
    current = _current_commit(root)
    if current == snapshot.source_commit:
        discard_prepared_snapshot(snapshot.snapshot_id, project_root=root)
        return False
    mark_snapshot_applied(
        snapshot.snapshot_id,
        project_root=root,
        with_errors=not git_succeeded,
    )
    return True


def discard_prepared_snapshot(
    snapshot_id: str,
    *,
    project_root: str | Path | None = None,
) -> None:
    """Delete a non-applied snapshot after an update attempt changed no code."""
    root = _resolve_project_root(project_root)
    snapshot_dir = _safe_snapshot_dir(root, snapshot_id)
    if snapshot_dir.is_symlink():
        snapshot_dir.unlink(missing_ok=True)
        return
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)


def _load_prepared_snapshot(
    snapshot_id: str,
    *,
    project_root: Path,
) -> PreparedUpdateSnapshot:
    """Load a prepared snapshot before any worktree mutation."""
    snapshot_dir = _safe_snapshot_dir(project_root, snapshot_id)
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        raise UpdateRollbackError("Подготовленный snapshot обновления недоступен")
    manifest = _load_manifest(snapshot_dir)
    update = manifest.get("update")
    source = manifest.get("source")
    if not isinstance(update, dict) or update.get("status") != "prepared":
        raise UpdateRollbackError("Snapshot не находится в состоянии prepared")
    if not isinstance(source, dict):
        raise UpdateRollbackError("В snapshot отсутствуют данные исходной версии")
    source_commit = str(source.get("commit") or "").lower()
    if source_commit != _current_commit(project_root):
        raise UpdateRollbackError(
            "Текущий Git commit изменился после создания snapshot; обновление отменено"
        )
    database_path = snapshot_dir / DATABASE_BACKUP_FILENAME
    if not database_path.is_file():
        raise UpdateRollbackError("В подготовленном snapshot отсутствует база данных")
    _check_database_integrity(database_path)
    return PreparedUpdateSnapshot(
        snapshot_id=snapshot_id,
        snapshot_dir=snapshot_dir,
        manifest_path=snapshot_dir / MANIFEST_FILENAME,
        source_commit=source_commit,
        source_release=str(source.get("release") or UNKNOWN_RELEASE),
    )


def _refresh_pre_update_snapshot(
    snapshot: PreparedUpdateSnapshot,
    *,
    project_root: Path,
) -> None:
    """Refresh the rollback database after the bot service has stopped."""
    live_database = _database_path(project_root)
    final_database = snapshot.snapshot_dir / DATABASE_BACKUP_FILENAME
    temp_database = snapshot.snapshot_dir / f".vpn_bot.final.{os.getpid()}.db"
    temp_database.unlink(missing_ok=True)
    try:
        _backup_database(live_database, temp_database)
        if temp_database.stat().st_dev != live_database.parent.stat().st_dev:
            raise UpdateRollbackError(
                "Каталоги backup и database находятся на разных файловых системах"
            )
        os.replace(temp_database, final_database)
        _fsync_directory(snapshot.snapshot_dir)
    except Exception as exc:
        temp_database.unlink(missing_ok=True)
        manifest = _load_manifest(snapshot.snapshot_dir)
        checks = manifest.setdefault("checks", {})
        if isinstance(checks, dict):
            checks["final_preflight"] = "failed"
            checks["final_preflight_error"] = str(exc)
        _atomic_write_json(snapshot.manifest_path, manifest)
        raise

    manifest = _load_manifest(snapshot.snapshot_dir)
    database = manifest.setdefault("database", {})
    if not isinstance(database, dict):
        raise UpdateRollbackError("Некорректная секция database в snapshot")
    database.update(
        {
            "file": DATABASE_BACKUP_FILENAME,
            "size": final_database.stat().st_size,
            "sha256": _sha256_file(final_database),
            "captured_at": _isoformat_utc(_utc_now()),
        }
    )
    checks = manifest.setdefault("checks", {})
    if isinstance(checks, dict):
        checks["final_preflight"] = "passed"
        checks["final_preflight_error"] = None
    _atomic_write_json(snapshot.manifest_path, manifest)


def _record_snapshot_check(
    snapshot_dir: Path,
    name: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Persist one additive update-check result in the snapshot manifest."""
    manifest = _load_manifest(snapshot_dir)
    checks = manifest.setdefault("checks", {})
    if not isinstance(checks, dict):
        checks = {}
        manifest["checks"] = checks
    checks[name] = status
    checks[f"{name}_error"] = error
    _atomic_write_json(snapshot_dir / MANIFEST_FILENAME, manifest)


def list_rollback_points(
    *,
    project_root: str | Path | None = None,
    verify_integrity: bool = True,
    now: datetime | None = None,
) -> list[RollbackPoint]:
    """Return up to three newest valid, non-expired rollback points."""
    root = _resolve_project_root(project_root)
    raw_snapshot_root = _pre_update_root(root)
    if not raw_snapshot_root.is_dir() or raw_snapshot_root.is_symlink():
        return []
    try:
        snapshot_root = _ensure_inside(raw_snapshot_root, root)
    except UpdateRollbackError:
        return []
    cutoff = (now or _utc_now()).astimezone(timezone.utc) - timedelta(
        days=ROLLBACK_RETENTION_DAYS
    )
    current = _current_commit(root)
    points: list[RollbackPoint] = []
    for snapshot_dir in snapshot_root.iterdir():
        if (
            not snapshot_dir.is_dir()
            or snapshot_dir.is_symlink()
            or not _SNAPSHOT_ID_RE.fullmatch(snapshot_dir.name)
        ):
            continue
        try:
            manifest = _load_manifest(snapshot_dir)
            point = _point_from_manifest(
                root,
                snapshot_dir,
                manifest,
                verify_integrity=verify_integrity,
            )
            if point.created_at < cutoff or point.source_commit == current:
                continue
            points.append(point)
        except UpdateRollbackError as exc:
            logger.warning("Skipping invalid rollback snapshot %s: %s", snapshot_dir, exc)
    points.sort(key=lambda item: item.created_at, reverse=True)
    return points[:MAX_ROLLBACK_POINTS]


def get_rollback_point(
    snapshot_id: str,
    *,
    project_root: str | Path | None = None,
    verify_integrity: bool = True,
) -> RollbackPoint:
    """Load and fully validate one rollback point by its opaque identifier."""
    root = _resolve_project_root(project_root)
    snapshot_dir = _safe_snapshot_dir(root, snapshot_id)
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        raise UpdateRollbackError("Rollback snapshot is unavailable")
    manifest = _load_manifest(snapshot_dir)
    source = manifest.get("source")
    source_commit = (
        str(source.get("commit") or "").lower()
        if isinstance(source, dict)
        else ""
    )
    if (
        re.fullmatch(r"[0-9a-f]{40,64}", source_commit)
        and not _commit_exists(root, source_commit)
    ):
        try:
            _git_output(
                root,
                ["fetch", "origin"],
                timeout=120,
                stage="Fetching rollback commit",
            )
        except UpdateRollbackError:
            pass
    point = _point_from_manifest(
        root,
        snapshot_dir,
        manifest,
        verify_integrity=verify_integrity,
    )
    cutoff = _utc_now() - timedelta(days=ROLLBACK_RETENTION_DAYS)
    if point.created_at < cutoff:
        raise UpdateRollbackError("Rollback snapshot has expired")
    return point


def cleanup_pre_update_snapshots(
    *,
    project_root: str | Path | None = None,
    retention_days: int = ROLLBACK_RETENTION_DAYS,
    max_points: int = MAX_ROLLBACK_POINTS,
    now: datetime | None = None,
) -> int:
    """Delete expired snapshot bundles and applied points beyond the limit."""
    root = _resolve_project_root(project_root)
    raw_snapshot_root = _pre_update_root(root)
    if not raw_snapshot_root.exists():
        return 0
    if raw_snapshot_root.is_symlink():
        logger.error("Pre-update snapshot cleanup refused: root is a symbolic link")
        return 0
    try:
        snapshot_root = _ensure_inside(raw_snapshot_root, root)
    except UpdateRollbackError:
        logger.error("Pre-update snapshot cleanup refused: root escapes project")
        return 0
    cutoff = (now or _utc_now()).astimezone(timezone.utc) - timedelta(
        days=max(0, int(retention_days))
    )
    entries: list[tuple[Path, datetime, bool]] = []
    for snapshot_dir in snapshot_root.iterdir():
        if (
            snapshot_dir.is_dir()
            and not snapshot_dir.is_symlink()
            and (
                snapshot_dir.name.startswith(".rollback-rescue-")
                or ".tmp-" in snapshot_dir.name
            )
        ):
            modified_at = datetime.fromtimestamp(
                snapshot_dir.stat().st_mtime,
                tz=timezone.utc,
            )
            if modified_at < cutoff:
                try:
                    shutil.rmtree(snapshot_dir)
                except OSError as exc:
                    logger.warning(
                        "Cannot remove stale rollback temporary directory %s: %s",
                        snapshot_dir,
                        exc,
                    )
            continue
        if (
            not snapshot_dir.is_dir()
            or snapshot_dir.is_symlink()
            or not _SNAPSHOT_ID_RE.fullmatch(snapshot_dir.name)
        ):
            continue
        created_at: datetime
        eligible = False
        try:
            manifest = _load_manifest(snapshot_dir)
            created_at = _parse_datetime(manifest.get("created_at"))
            update = manifest.get("update")
            eligible = (
                isinstance(update, dict)
                and update.get("status") in ELIGIBLE_ROLLBACK_STATUSES
            )
        except UpdateRollbackError:
            created_at = datetime.fromtimestamp(
                snapshot_dir.stat().st_mtime,
                tz=timezone.utc,
            )
        entries.append((snapshot_dir, created_at, eligible))

    remove: set[Path] = {
        path for path, created_at, _ in entries if created_at < cutoff
    }
    eligible_entries = sorted(
        (
            (path, created_at)
            for path, created_at, eligible in entries
            if eligible and path not in remove
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    for path, _ in eligible_entries[max(0, int(max_points)) :]:
        remove.add(path)

    removed = 0
    for path in sorted(remove, key=lambda item: item.name):
        try:
            _ensure_inside(path, snapshot_root)
            if path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.exists():
                shutil.rmtree(path)
            removed += 1
            logger.info("Removed pre-update snapshot: %s", path)
        except OSError as exc:
            logger.warning("Cannot remove pre-update snapshot %s: %s", path, exc)

    for filename in (
        ROLLBACK_RESULT_FILENAME,
        UPDATE_RESULT_FILENAME,
        UPDATE_HEALTH_FILENAME,
    ):
        result_path = snapshot_root / filename
        try:
            if (
                result_path.is_file()
                and datetime.fromtimestamp(
                    result_path.stat().st_mtime,
                    tz=timezone.utc,
                )
                < cutoff
            ):
                result_path.unlink()
        except OSError as exc:
            logger.warning("Cannot remove stale operation file %s: %s", result_path, exc)
    return removed


@contextmanager
def update_operation_lock(
    project_root: str | Path | None = None,
    *,
    wait_seconds: float = 0,
) -> Iterator[None]:
    """Serialize update and rollback mutations on Linux production hosts."""
    root = _resolve_project_root(project_root)
    lock_root = _ensure_inside(_pre_update_root(root), root)
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_root / OPERATION_LOCK_FILENAME
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "posix":
            import fcntl

            deadline = time.monotonic() + max(0.0, float(wait_seconds))
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise UpdateRollbackError(
                            "Another update or rollback operation is already running"
                        ) from exc
                    time.sleep(0.2)
        yield
    finally:
        if os.name == "posix":
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()


def _systemctl(
    action: str,
    service_name: str,
    *,
    project_root: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = _run_command(
        ["systemctl", action, service_name],
        cwd=project_root,
        timeout=60,
    )
    if check and result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise UpdateRollbackError(
            f"systemctl {action} failed: {output or 'no output'}"
        )
    return result


def _wait_for_service(
    service_name: str,
    *,
    project_root: Path,
    timeout_seconds: int = 30,
    stable_seconds: int = 5,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    stable_since: float | None = None
    while time.monotonic() < deadline:
        result = _systemctl(
            "is-active",
            service_name,
            project_root=project_root,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "active":
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= stable_seconds:
                return True
        else:
            stable_since = None
        time.sleep(1)
    return False


def _service_main_pid(
    service_name: str,
    *,
    project_root: Path,
) -> int | None:
    result = _run_command(
        ["systemctl", "show", service_name, "--property=MainPID", "--value"],
        cwd=project_root,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        value = int(result.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _wait_for_update_health(
    service_name: str,
    *,
    project_root: Path,
    snapshot_id: str,
    target_commit: str,
    timeout_seconds: int = UPDATE_STARTUP_TIMEOUT_SECONDS,
    stable_seconds: int = UPDATE_STABLE_SECONDS,
) -> bool:
    """Wait for an application-level startup acknowledgement and stable PID."""
    deadline = time.monotonic() + max(1, timeout_seconds)
    stable_since: float | None = None
    health_path = _update_health_path(project_root)
    while time.monotonic() < deadline:
        active = _systemctl(
            "is-active",
            service_name,
            project_root=project_root,
            check=False,
        )
        if active.returncode != 0 or active.stdout.strip() != "active":
            stable_since = None
            time.sleep(1)
            continue
        try:
            payload = _load_json(health_path)
        except UpdateRollbackError:
            stable_since = None
            time.sleep(1)
            continue
        ready_pid = payload.get("pid")
        try:
            ready_pid = int(ready_pid)
        except (TypeError, ValueError):
            ready_pid = 0
        ready = (
            payload.get("status") == "ready"
            and payload.get("snapshot_id") == snapshot_id
            and str(payload.get("target_commit") or "").lower() == target_commit.lower()
            and ready_pid > 0
            and _service_main_pid(service_name, project_root=project_root) == ready_pid
        )
        if not ready:
            stable_since = None
            time.sleep(1)
            continue
        if stable_since is None:
            stable_since = time.monotonic()
        if time.monotonic() - stable_since >= stable_seconds:
            return True
        time.sleep(1)
    return False


def _resolve_update_target(project_root: Path, target: str) -> str:
    """Fetch and resolve an update target without changing the worktree."""
    target = str(target or "").strip()
    if not target:
        raise UpdateRollbackError("Цель обновления не указана")
    fetch = _run_command(
        ["git", "fetch", "origin"],
        cwd=project_root,
        timeout=120,
    )
    resolved = _run_command(
        ["git", "rev-parse", "--verify", f"{target}^{{commit}}"],
        cwd=project_root,
        timeout=30,
    )
    if resolved.returncode != 0:
        fetch_error = (fetch.stdout + fetch.stderr).strip()
        resolve_error = (resolved.stdout + resolved.stderr).strip()
        raise UpdateRollbackError(
            "Целевой commit обновления недоступен: "
            + (resolve_error or fetch_error or target)
        )
    commit = resolved.stdout.splitlines()[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise UpdateRollbackError("Git вернул некорректный hash целевого commit")
    return commit


def _validate_update_strategy(
    project_root: Path,
    *,
    source_commit: str,
    target_commit: str,
    strategy: str,
) -> None:
    if strategy not in {"pull", "reset"}:
        raise UpdateRollbackError(f"Неподдерживаемая стратегия обновления: {strategy}")
    if source_commit == target_commit:
        raise UpdateRollbackError("Выбранная версия уже установлена")
    protected_paths = (
        "backup",
        "config.py",
        "database/vpn_bot.db",
        "database/vpn_bot.db-wal",
        "database/vpn_bot.db-shm",
        "custom_extensions",
        "logs",
        "venv",
    )
    tracked_protected = _run_checked(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            target_commit,
            "--",
            *protected_paths,
        ],
        cwd=project_root,
        timeout=30,
        stage="Checking target persistent-path boundary",
    )
    if tracked_protected.strip():
        raise UpdateRollbackError(
            "Целевая версия содержит tracked-файлы в локальных persistent-путях: "
            + ", ".join(tracked_protected.splitlines()[:5])
        )
    if strategy == "pull":
        status = _run_checked(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project_root,
            timeout=30,
            stage="Checking Git worktree",
        )
        if status.strip():
            raise UpdateRollbackError(
                "Есть локальные изменения tracked-файлов; безопасное обновление отменено"
            )
        ancestor = _run_command(
            ["git", "merge-base", "--is-ancestor", source_commit, target_commit],
            cwd=project_root,
            timeout=30,
        )
        if ancestor.returncode != 0:
            raise UpdateRollbackError(
                "Мягкое обновление не является fast-forward; используйте жёсткую перезапись"
            )


def _apply_git_target(
    project_root: Path,
    *,
    target_commit: str,
    clean_untracked: bool,
) -> None:
    _git_output(
        project_root,
        ["reset", "--hard", target_commit],
        stage="Applying target Git commit",
    )
    if clean_untracked:
        _git_output(
            project_root,
            [
                "clean",
                "-fd",
                "-e",
                "backup/",
                "-e",
                "config.py",
                "-e",
                "custom_extensions/",
                "-e",
                "database/vpn_bot.db",
                "-e",
                "database/vpn_bot.db-wal",
                "-e",
                "database/vpn_bot.db-shm",
                "-e",
                "logs/",
                "-e",
                "venv/",
            ],
            stage="Cleaning untracked project files",
        )
    _validated_backup_root(project_root)
    _validate_persistent_git_ignores(project_root)


def _create_migration_candidate(
    snapshot: PreparedUpdateSnapshot,
) -> Path:
    candidate = snapshot.snapshot_dir / MIGRATION_CANDIDATE_FILENAME
    failed_candidate = snapshot.snapshot_dir / FAILED_MIGRATION_CANDIDATE_FILENAME
    candidate.unlink(missing_ok=True)
    failed_candidate.unlink(missing_ok=True)
    _backup_database(
        snapshot.snapshot_dir / DATABASE_BACKUP_FILENAME,
        candidate,
    )
    return candidate


def _run_candidate_migrations(
    *,
    project_root: Path,
    snapshot: PreparedUpdateSnapshot,
    block_updates: bool,
) -> Path:
    candidate = _create_migration_candidate(snapshot)
    try:
        _run_checked(
            [
                sys.executable,
                "-m",
                "database.migration_runner",
                "--project-root",
                str(project_root),
                "--database",
                str(candidate),
            ],
            cwd=project_root,
            timeout=900,
            stage="Running database migrations on candidate",
        )
        if block_updates:
            with sqlite3.connect(str(candidate), timeout=30) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise UpdateRollbackError(
                        "Не удалось включить foreign_keys для blocking update"
                    )
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES ('update_blocked', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                )
        _check_database_integrity(candidate)
        _record_snapshot_check(
            snapshot.snapshot_dir,
            "migration",
            status="passed",
        )
        _record_snapshot_check(
            snapshot.snapshot_dir,
            "post_migration",
            status="passed",
        )
        return candidate
    except Exception as exc:
        _record_snapshot_check(
            snapshot.snapshot_dir,
            "migration",
            status="failed",
            error=str(exc),
        )
        failed_candidate = snapshot.snapshot_dir / FAILED_MIGRATION_CANDIDATE_FILENAME
        try:
            if candidate.exists():
                os.replace(candidate, failed_candidate)
        except OSError:
            pass
        raise


def _promote_database_candidate(
    candidate: Path,
    destination: Path,
) -> None:
    """Atomically promote a fully validated candidate to the live database."""
    _check_database_integrity(candidate)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if candidate.stat().st_dev != destination.parent.stat().st_dev:
        raise UpdateRollbackError(
            "Невозможно атомарно применить БД: backup и database на разных файловых системах"
        )
    for suffix in ("-wal", "-shm"):
        Path(str(destination) + suffix).unlink(missing_ok=True)
    os.replace(candidate, destination)
    _fsync_directory(destination.parent)


def _install_requirements(project_root: Path) -> None:
    requirements = project_root / "requirements.txt"
    if not requirements.is_file():
        raise UpdateRollbackError("requirements.txt is missing after Git reset")
    _run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "-r",
            str(requirements),
        ],
        cwd=project_root,
        timeout=600,
        stage="Installing Python dependencies",
    )


def _restore_database_atomically(source: Path, destination: Path) -> None:
    _check_database_integrity(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    current_mode: int | None = None
    try:
        current_mode = destination.stat().st_mode & 0o777
    except OSError:
        pass
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=".vpn_bot.rollback.",
        suffix=".db",
        dir=str(source.parent),
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        _check_database_integrity(temp_path)
        if temp_path.stat().st_dev != destination.parent.stat().st_dev:
            raise UpdateRollbackError(
                "Невозможно атомарно восстановить БД: backup и database "
                "на разных файловых системах"
            )
        if current_mode is not None:
            temp_path.chmod(current_mode)
        for suffix in ("-wal", "-shm"):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _rollback_result_path(project_root: Path) -> Path:
    return _pre_update_root(project_root) / ROLLBACK_RESULT_FILENAME


def _update_result_path(project_root: Path) -> Path:
    return _pre_update_root(project_root) / UPDATE_RESULT_FILENAME


def _update_health_path(project_root: Path) -> Path:
    return _pre_update_root(project_root) / UPDATE_HEALTH_FILENAME


def _write_update_result(
    project_root: Path,
    *,
    admin_id: int | None,
    status: str,
    message: str,
    snapshot_id: str,
    target_commit: str | None = None,
) -> None:
    """Write a durable update result consumed by the restored/new bot."""
    _atomic_write_json(
        _update_result_path(project_root),
        {
            "format_version": 1,
            "created_at": _isoformat_utc(_utc_now()),
            "admin_id": int(admin_id) if admin_id is not None else None,
            "status": status,
            "message": str(message),
            "snapshot_id": snapshot_id,
            "target_commit": target_commit,
        },
    )


def _try_write_update_result(
    project_root: Path,
    **kwargs: Any,
) -> bool:
    """Persist a notification without letting delivery metadata block recovery."""
    try:
        _write_update_result(project_root, **kwargs)
        return True
    except Exception:
        logger.exception("Cannot persist managed-update result")
        return False


def _prepare_update_health(
    project_root: Path,
    *,
    snapshot_id: str,
    target_commit: str,
) -> None:
    _atomic_write_json(
        _update_health_path(project_root),
        {
            "format_version": 1,
            "snapshot_id": snapshot_id,
            "target_commit": target_commit,
            "status": "pending",
            "pid": None,
            "ready_at": None,
        },
    )


def pending_update_health_exists(
    *,
    project_root: str | Path | None = None,
) -> bool:
    """Return whether startup is currently owned by a managed update worker."""
    try:
        root = _resolve_project_root(project_root)
    except UpdateRollbackError:
        return False
    return _update_health_path(root).is_file()


def acknowledge_pending_update(
    *,
    project_root: str | Path | None = None,
) -> bool:
    """Acknowledge that target startup finished all required initialization."""
    try:
        root = _resolve_project_root(project_root)
    except UpdateRollbackError:
        return False
    health_path = _update_health_path(root)
    if not health_path.is_file():
        return False
    try:
        payload = _load_json(health_path)
        target_commit = str(payload.get("target_commit") or "").lower()
        if target_commit != _current_commit(root):
            return False
        if payload.get("status") == "accepted":
            return True
        payload.update(
            {
                "status": "ready",
                "pid": os.getpid(),
                "ready_at": _isoformat_utc(_utc_now()),
            }
        )
        _atomic_write_json(health_path, payload)
        return True
    except Exception:
        logger.exception("Cannot acknowledge pending managed update")
        return False


def _accept_update_health(
    project_root: Path,
    *,
    snapshot_id: str,
    target_commit: str,
) -> None:
    """Release the initialized bot only after the worker's stability window."""
    health_path = _update_health_path(project_root)
    payload = _load_json(health_path)
    if (
        payload.get("status") != "ready"
        or payload.get("snapshot_id") != snapshot_id
        or str(payload.get("target_commit") or "").lower() != target_commit.lower()
    ):
        raise UpdateRollbackError("Update health acknowledgement changed unexpectedly")
    payload.update(
        {
            "status": "accepted",
            "accepted_at": _isoformat_utc(_utc_now()),
        }
    )
    _atomic_write_json(health_path, payload)


async def wait_for_pending_update_acceptance(
    *,
    project_root: str | Path | None = None,
    timeout_seconds: int = UPDATE_ACTIVATION_TIMEOUT_SECONDS,
) -> bool:
    """Keep polling closed until the independent worker accepts startup."""
    try:
        root = _resolve_project_root(project_root)
    except UpdateRollbackError:
        return False
    health_path = _update_health_path(root)
    try:
        current_commit = _current_commit(root)
    except UpdateRollbackError:
        return False
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    while time.monotonic() < deadline:
        if not health_path.is_file():
            return False
        try:
            payload = _load_json(health_path)
            accepted = (
                payload.get("status") == "accepted"
                and str(payload.get("target_commit") or "").lower()
                == current_commit
            )
        except Exception:
            logger.exception("Cannot read managed-update activation gate")
            accepted = False
        if accepted:
            health_path.unlink(missing_ok=True)
            return True
        await asyncio.sleep(0.2)
    return False


def _write_rollback_result(
    project_root: Path,
    *,
    admin_id: int | None,
    status: str,
    message: str,
    snapshot_id: str,
) -> None:
    if admin_id is None:
        return
    _atomic_write_json(
        _rollback_result_path(project_root),
        {
            "format_version": 1,
            "created_at": _isoformat_utc(_utc_now()),
            "admin_id": int(admin_id),
            "status": status,
            "message": str(message),
            "snapshot_id": snapshot_id,
        },
    )


def _prepare_rescue_snapshot(project_root: Path, current_commit: str) -> Path:
    rescue_root = Path(
        tempfile.mkdtemp(
            prefix=".rollback-rescue-",
            dir=str(_pre_update_root(project_root)),
        )
    )
    try:
        _backup_database(
            _database_path(project_root),
            rescue_root / DATABASE_BACKUP_FILENAME,
        )
        _atomic_write_json(
            rescue_root / "rescue.json",
            {
                "created_at": _isoformat_utc(_utc_now()),
                "commit": current_commit,
            },
        )
        return rescue_root
    except Exception:
        shutil.rmtree(rescue_root, ignore_errors=True)
        raise


def _recover_failed_rollback(
    *,
    project_root: Path,
    service_name: str,
    rescue_root: Path,
    rescue_commit: str,
    manage_service: bool,
) -> bool:
    try:
        if manage_service:
            _systemctl("stop", service_name, project_root=project_root, check=False)
        _git_output(
            project_root,
            ["reset", "--hard", rescue_commit],
            stage="Restoring pre-rollback Git commit",
        )
        _install_requirements(project_root)
        _restore_database_atomically(
            rescue_root / DATABASE_BACKUP_FILENAME,
            _database_path(project_root),
        )
        if manage_service:
            _systemctl("start", service_name, project_root=project_root)
            return _wait_for_service(
                service_name,
                project_root=project_root,
            )
        return True
    except Exception:
        logger.exception("Automatic recovery after failed rollback also failed")
        return False


def perform_rollback(
    snapshot_id: str,
    *,
    project_root: str | Path | None = None,
    service_name: str = SERVICE_NAME,
    admin_id: int | None = None,
    manage_service: bool = True,
    _lock_held: bool = False,
) -> RollbackExecutionResult:
    """Restore Git and the bot database to a selected pre-update snapshot."""
    root = _resolve_project_root(project_root)
    lock_context = (
        nullcontext()
        if _lock_held
        else update_operation_lock(root, wait_seconds=30)
    )
    with lock_context:
        point = get_rollback_point(
            snapshot_id,
            project_root=root,
            verify_integrity=True,
        )
        current_commit = _current_commit(root)
        if current_commit == point.source_commit:
            raise UpdateRollbackError("The bot is already at the selected commit")
        if not _commit_exists(root, point.source_commit):
            try:
                _git_output(
                    root,
                    ["fetch", "origin"],
                    timeout=120,
                    stage="Fetching rollback commit",
                )
            except UpdateRollbackError:
                pass
        if not _commit_exists(root, point.source_commit):
            raise UpdateRollbackError("Selected rollback commit is unavailable")

        rescue_root: Path | None = None
        service_stopped = False
        try:
            if manage_service:
                _systemctl("stop", service_name, project_root=root)
                service_stopped = True
            rescue_root = _prepare_rescue_snapshot(root, current_commit)
            _git_output(
                root,
                ["reset", "--hard", point.source_commit],
                stage="Resetting Git worktree",
            )
            _install_requirements(root)
            _restore_database_atomically(
                point.database_path,
                _database_path(root),
            )
            success_message = (
                f"Откат выполнен: {point.display_release} "
                f"({point.source_short_commit}). База данных восстановлена "
                f"на {_isoformat_utc(point.created_at)}."
            )
            if manage_service:
                _write_rollback_result(
                    root,
                    admin_id=admin_id,
                    status="pending",
                    message=success_message,
                    snapshot_id=snapshot_id,
                )
                _systemctl("start", service_name, project_root=root)
                if not _wait_for_service(service_name, project_root=root):
                    raise UpdateRollbackError(
                        "Bot service did not become stably active after rollback"
                    )
            _write_rollback_result(
                root,
                admin_id=admin_id,
                status="success",
                message=success_message,
                snapshot_id=snapshot_id,
            )
            if rescue_root is not None:
                shutil.rmtree(rescue_root, ignore_errors=True)
            return RollbackExecutionResult(True, success_message)
        except Exception as exc:
            logger.exception("Rollback to snapshot %s failed", snapshot_id)
            recovered = False
            _write_rollback_result(
                root,
                admin_id=admin_id,
                status="pending",
                message=f"Откат не выполнен: {exc}. Выполняется восстановление.",
                snapshot_id=snapshot_id,
            )
            if rescue_root is not None:
                recovered = _recover_failed_rollback(
                    project_root=root,
                    service_name=service_name,
                    rescue_root=rescue_root,
                    rescue_commit=current_commit,
                    manage_service=manage_service,
                )
            elif manage_service and service_stopped:
                try:
                    _systemctl("start", service_name, project_root=root)
                    recovered = _wait_for_service(
                        service_name,
                        project_root=root,
                    )
                except Exception:
                    logger.exception(
                        "Cannot restart service after rescue snapshot failure"
                    )
            failure_message = (
                f"Откат не выполнен: {exc}. "
                + (
                    "Исходные код и база данных автоматически восстановлены."
                    if recovered
                    else "Автоматически восстановить исходное состояние не удалось."
                )
            )
            _write_rollback_result(
                root,
                admin_id=admin_id,
                status="failed",
                message=failure_message,
                snapshot_id=snapshot_id,
            )
            if rescue_root is not None and recovered:
                shutil.rmtree(rescue_root, ignore_errors=True)
            return RollbackExecutionResult(False, failure_message, recovered=recovered)


def _emergency_restore_pre_update_state(
    *,
    project_root: Path,
    snapshot: PreparedUpdateSnapshot,
    service_name: str,
    manage_service: bool,
) -> RollbackExecutionResult:
    """Restore a prepared snapshot when normal rollback-point loading failed."""
    try:
        if manage_service:
            _systemctl("stop", service_name, project_root=project_root, check=False)
        _git_output(
            project_root,
            ["reset", "--hard", snapshot.source_commit],
            stage="Emergency restoring source Git commit",
        )
        _install_requirements(project_root)
        _restore_database_atomically(
            snapshot.snapshot_dir / DATABASE_BACKUP_FILENAME,
            _database_path(project_root),
        )
        if manage_service:
            _systemctl("start", service_name, project_root=project_root)
            if not _wait_for_service(
                service_name,
                project_root=project_root,
                timeout_seconds=60,
                stable_seconds=10,
            ):
                raise UpdateRollbackError(
                    "Bot service did not become stable after emergency restore"
                )
        return RollbackExecutionResult(
            True,
            "Исходные код и база данных восстановлены аварийным путём.",
        )
    except Exception as exc:
        logger.exception("Emergency update rollback failed")
        return RollbackExecutionResult(
            False,
            f"Аварийное восстановление не удалось: {exc}",
        )


def perform_update_transaction(
    snapshot_id: str,
    *,
    target: str,
    strategy: str,
    project_root: str | Path | None = None,
    service_name: str = SERVICE_NAME,
    admin_id: int | None = None,
    clean_untracked: bool = False,
    block_updates: bool = False,
    manage_service: bool = True,
    _lock_held: bool = False,
) -> UpdateExecutionResult:
    """Apply one prepared update and automatically roll it back on failure."""
    root = _resolve_project_root(project_root)
    lock_context = (
        nullcontext()
        if _lock_held
        else update_operation_lock(root, wait_seconds=30)
    )
    with lock_context:
        snapshot = _load_prepared_snapshot(snapshot_id, project_root=root)
        target_commit = ""
        service_stopped = False
        git_changed = False
        try:
            target_commit = _resolve_update_target(root, target)
            _validate_update_strategy(
                root,
                source_commit=snapshot.source_commit,
                target_commit=target_commit,
                strategy=strategy,
            )

            if manage_service:
                _systemctl("stop", service_name, project_root=root)
                service_stopped = True

            _refresh_pre_update_snapshot(snapshot, project_root=root)
            _apply_git_target(
                root,
                target_commit=target_commit,
                clean_untracked=clean_untracked,
            )
            git_changed = True
            mark_snapshot_applied(
                snapshot.snapshot_id,
                project_root=root,
                with_errors=True,
            )

            _install_requirements(root)
            candidate = _run_candidate_migrations(
                project_root=root,
                snapshot=snapshot,
                block_updates=block_updates,
            )
            _promote_database_candidate(candidate, _database_path(root))
            mark_snapshot_applied(
                snapshot.snapshot_id,
                project_root=root,
                with_errors=False,
            )

            pending_message = (
                "Новая версия установлена на диск. Проверяется запуск бота "
                f"на commit {target_commit[:8]}. Snapshot: {snapshot.snapshot_id}."
            )
            _try_write_update_result(
                root,
                admin_id=admin_id,
                status="pending",
                message=pending_message,
                snapshot_id=snapshot.snapshot_id,
                target_commit=target_commit,
            )
            if manage_service:
                _prepare_update_health(
                    root,
                    snapshot_id=snapshot.snapshot_id,
                    target_commit=target_commit,
                )
                _systemctl("start", service_name, project_root=root)
                service_stopped = False
                if not _wait_for_update_health(
                    service_name,
                    project_root=root,
                    snapshot_id=snapshot.snapshot_id,
                    target_commit=target_commit,
                ):
                    raise UpdateRollbackError(
                        "Новая версия не подтвердила успешный запуск за 120 секунд"
                    )
                _accept_update_health(
                    root,
                    snapshot_id=snapshot.snapshot_id,
                    target_commit=target_commit,
                )
            else:
                _update_health_path(root).unlink(missing_ok=True)
            success_message = (
                f"Обновление успешно установлено: commit {target_commit[:8]}. "
                "Миграции и целостность базы данных проверены. "
                f"Snapshot для ручного отката: {snapshot.snapshot_id}."
            )
            _try_write_update_result(
                root,
                admin_id=admin_id,
                status="success",
                message=success_message,
                snapshot_id=snapshot.snapshot_id,
                target_commit=target_commit,
            )
            return UpdateExecutionResult(
                True,
                success_message,
                snapshot.snapshot_id,
            )
        except Exception as exc:
            failure_detail = _bounded_detail(exc)
            logger.exception(
                "Managed update %s failed at target %s",
                snapshot.snapshot_id,
                target_commit or target,
            )
            _update_health_path(root).unlink(missing_ok=True)
            try:
                git_changed = _current_commit(root) != snapshot.source_commit
            except Exception:
                logger.exception("Cannot determine Git state after update failure")
            if git_changed:
                try:
                    mark_snapshot_applied(
                        snapshot.snapshot_id,
                        project_root=root,
                        with_errors=True,
                    )
                except Exception:
                    logger.exception(
                        "Cannot mark failed update snapshot %s as rollback eligible",
                        snapshot.snapshot_id,
                    )
                _try_write_update_result(
                    root,
                    admin_id=admin_id,
                    status="pending",
                    message=(
                        f"Обновление не установлено: {failure_detail}. "
                        "Выполняется автоматический откат."
                    ),
                    snapshot_id=snapshot.snapshot_id,
                    target_commit=target_commit or None,
                )
                try:
                    rollback = perform_rollback(
                        snapshot.snapshot_id,
                        project_root=root,
                        service_name=service_name,
                        admin_id=None,
                        manage_service=manage_service,
                        _lock_held=True,
                    )
                except Exception:
                    logger.exception(
                        "Normal automatic rollback failed to start; using emergency restore"
                    )
                    rollback = _emergency_restore_pre_update_state(
                        project_root=root,
                        snapshot=snapshot,
                        service_name=service_name,
                        manage_service=manage_service,
                    )
                if rollback.success:
                    message = (
                        f"Обновление не установлено: {failure_detail}. "
                        f"Автоматически восстановлена {snapshot.source_release} "
                        f"({snapshot.source_commit[:8]}), данные сохранены. "
                        f"Snapshot: {snapshot.snapshot_id}."
                    )
                    status = "rolled_back"
                else:
                    message = (
                        f"Обновление не установлено: {failure_detail}. "
                        f"Автоматический откат завершился ошибкой: {rollback.message}. "
                        f"Snapshot: {snapshot.snapshot_id}."
                    )
                    status = "failed"
                _try_write_update_result(
                    root,
                    admin_id=admin_id,
                    status=status,
                    message=message,
                    snapshot_id=snapshot.snapshot_id,
                    target_commit=target_commit or None,
                )
                return UpdateExecutionResult(
                    False,
                    message,
                    snapshot.snapshot_id,
                    rolled_back=rollback.success,
                )

            recovered = True
            if manage_service and service_stopped:
                try:
                    _systemctl("start", service_name, project_root=root)
                    recovered = _wait_for_service(
                        service_name,
                        project_root=root,
                        timeout_seconds=60,
                        stable_seconds=10,
                    )
                except Exception:
                    logger.exception("Cannot restart unchanged bot after update failure")
                    recovered = False
            message = (
                f"Обновление не началось: {failure_detail}. "
                + (
                    "Текущая версия продолжает работать. "
                    if recovered
                    else "Не удалось подтвердить повторный запуск текущей версии. "
                )
                + f"Snapshot: {snapshot.snapshot_id}."
            )
            _try_write_update_result(
                root,
                admin_id=admin_id,
                status="not_started" if recovered else "failed",
                message=message,
                snapshot_id=snapshot.snapshot_id,
                target_commit=target_commit or None,
            )
            return UpdateExecutionResult(False, message, snapshot.snapshot_id)


def run_managed_update(
    *,
    update_mode: str,
    target: str,
    strategy: str,
    actor: str | None = None,
    project_root: str | Path | None = None,
    service_name: str = SERVICE_NAME,
    admin_id: int | None = None,
    clean_untracked: bool = False,
    block_updates: bool = False,
    manage_service: bool = True,
) -> UpdateExecutionResult:
    """Prepare and apply a managed update in the current worker process."""
    root = _resolve_project_root(project_root)
    with update_operation_lock(root):
        source_commit = _current_commit(root)
        target_commit = _resolve_update_target(root, target)
        target_commit, block_updates = _resolve_ordered_update_stage(
            root,
            update_mode=update_mode,
            source_commit=source_commit,
            target_commit=target_commit,
            block_updates=block_updates,
        )
        _validate_update_strategy(
            root,
            source_commit=source_commit,
            target_commit=target_commit,
            strategy=strategy,
        )
        snapshot = create_pre_update_snapshot(
            update_mode=update_mode,
            requested_target=target_commit,
            actor=actor,
            project_root=root,
        )
        return perform_update_transaction(
            snapshot.snapshot_id,
            target=target_commit,
            strategy=strategy,
            project_root=root,
            service_name=service_name,
            admin_id=admin_id,
            clean_untracked=clean_untracked,
            block_updates=block_updates,
            manage_service=manage_service,
            _lock_held=True,
        )


def schedule_admin_update(
    *,
    update_mode: str,
    target: str,
    strategy: str,
    admin_id: int,
    actor: str,
    project_root: str | Path | None = None,
    service_name: str = SERVICE_NAME,
    clean_untracked: bool = False,
    block_updates: bool = False,
) -> tuple[bool, str]:
    """Prepare an update and launch its worker outside the bot service cgroup."""
    root = _resolve_project_root(project_root)
    if _repository_guard_is_active():
        return False, (
            "Обновление временно недоступно: Yadreno Admin проверяет изменения "
            "защищённого tool call. Повторите после его завершения."
        )
    snapshot: PreparedUpdateSnapshot | None = None
    try:
        with update_operation_lock(root):
            source_commit = _current_commit(root)
            target_commit = _resolve_update_target(root, target)
            target_commit, block_updates = _resolve_ordered_update_stage(
                root,
                update_mode=update_mode,
                source_commit=source_commit,
                target_commit=target_commit,
                block_updates=block_updates,
            )
            _validate_update_strategy(
                root,
                source_commit=source_commit,
                target_commit=target_commit,
                strategy=strategy,
            )
            snapshot = create_pre_update_snapshot(
                update_mode=update_mode,
                requested_target=target_commit,
                actor=actor,
                project_root=root,
            )
            runner = snapshot.snapshot_dir / ROLLBACK_RUNNER_FILENAME
            command = [
                "systemd-run",
                "--quiet",
                "--collect",
                f"--unit=yadreno-vpn-update-{snapshot.snapshot_id[:23].lower()}",
                "--property=Type=exec",
                sys.executable,
                str(runner),
                "apply-update",
                "--project-root",
                str(root),
                "--snapshot-id",
                snapshot.snapshot_id,
                "--target",
                target_commit,
                "--strategy",
                strategy,
                "--service-name",
                service_name,
                "--admin-id",
                str(int(admin_id)),
                "--start-delay",
                "0",
            ]
            if clean_untracked:
                command.append("--clean-untracked")
            if block_updates:
                command.append("--block-updates")
            result = _run_command(command, cwd=root, timeout=30)
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                discard_prepared_snapshot(snapshot.snapshot_id, project_root=root)
                return False, output or "Не удалось запустить update worker"
    except Exception as exc:
        logger.exception("Cannot schedule managed administrator update")
        return False, str(exc)
    if snapshot is None:
        return False, "Не удалось подготовить snapshot обновления"
    return True, snapshot.snapshot_id


def schedule_admin_rollback(
    snapshot_id: str,
    admin_id: int,
    *,
    project_root: str | Path | None = None,
    service_name: str = SERVICE_NAME,
) -> tuple[bool, str]:
    """Start a rollback worker in a transient systemd unit."""
    root = _resolve_project_root(project_root)
    point = get_rollback_point(
        snapshot_id,
        project_root=root,
        verify_integrity=True,
    )
    runner = point.snapshot_dir / ROLLBACK_RUNNER_FILENAME
    if not runner.is_file():
        return False, "Автономный исполнитель отката отсутствует в backup."
    unit = f"yadreno-vpn-rollback-{snapshot_id[:23].lower()}"
    result = _run_command(
        [
            "systemd-run",
            "--quiet",
            "--collect",
            f"--unit={unit}",
            "--property=Type=exec",
            sys.executable,
            str(runner),
            "rollback",
            "--project-root",
            str(root),
            "--snapshot-id",
            snapshot_id,
            "--service-name",
            service_name,
            "--admin-id",
            str(int(admin_id)),
            "--start-delay",
            "2",
        ],
        cwd=root,
        timeout=30,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        return False, output or "Не удалось запустить transient systemd unit."
    return True, unit


async def notify_pending_rollback_result(
    bot: Any,
    *,
    project_root: str | Path | None = None,
    pending_timeout_seconds: int = 30,
) -> bool:
    """Deliver a rollback worker result after the bot service starts."""
    try:
        root = _resolve_project_root(project_root)
    except UpdateRollbackError:
        return False
    result_path = _rollback_result_path(root)
    if not result_path.is_file():
        return False

    deadline = time.monotonic() + max(0, pending_timeout_seconds)
    payload: dict[str, Any]
    while True:
        try:
            payload = _load_json(result_path)
        except UpdateRollbackError:
            logger.exception("Cannot read pending rollback result")
            return False
        if payload.get("status") != "pending" or time.monotonic() >= deadline:
            break
        await asyncio.sleep(1)

    if payload.get("status") == "pending":
        return False
    try:
        admin_id = int(payload["admin_id"])
    except (KeyError, TypeError, ValueError):
        logger.error("Rollback result has an invalid administrator id")
        return False
    success = payload.get("status") == "success"
    title = "✅ <b>Откат обновления завершён</b>" if success else "❌ <b>Ошибка отката обновления</b>"
    text = f"{title}\n\n{html.escape(str(payload.get('message') or 'Нет подробностей.'))}"
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Cannot deliver rollback result to administrator %s", admin_id)
        return False
    result_path.unlink(missing_ok=True)
    return True


async def notify_pending_update_result(
    bot: Any,
    *,
    project_root: str | Path | None = None,
    pending_timeout_seconds: int = 30,
) -> bool:
    """Deliver a managed-update result after the new/restored bot starts."""
    try:
        root = _resolve_project_root(project_root)
    except UpdateRollbackError:
        return False
    result_path = _update_result_path(root)
    if not result_path.is_file():
        return False

    deadline = time.monotonic() + max(0, pending_timeout_seconds)
    payload: dict[str, Any]
    while True:
        try:
            payload = _load_json(result_path)
        except UpdateRollbackError:
            logger.exception("Cannot read pending update result")
            return False
        if payload.get("status") != "pending" or time.monotonic() >= deadline:
            break
        await asyncio.sleep(1)
    if payload.get("status") == "pending":
        return False

    raw_admin_id = payload.get("admin_id")
    admin_ids: list[int] = []
    if raw_admin_id is not None:
        try:
            admin_ids = [int(raw_admin_id)]
        except (TypeError, ValueError):
            logger.error("Update result has an invalid administrator id")
            return False
    else:
        try:
            from config import ADMIN_IDS

            admin_ids = [int(item) for item in ADMIN_IDS]
        except Exception:
            logger.exception("Cannot resolve administrators for installer update result")
            return False

    status = str(payload.get("status") or "failed")
    if status == "success":
        title = "✅ <b>Обновление успешно установлено</b>"
    elif status == "rolled_back":
        title = "⚠️ <b>Обновление отменено и выполнен откат</b>"
    elif status == "not_started":
        title = "⚠️ <b>Обновление не началось</b>"
    else:
        title = "❌ <b>Ошибка обновления</b>"
    message = html.escape(_bounded_detail(payload.get("message"), limit=3000))
    text = f"{title}\n\n{message}"
    delivered = True
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            delivered = False
            logger.exception("Cannot deliver update result to administrator %s", admin_id)
    if delivered:
        result_path.unlink(missing_ok=True)
    return delivered


def _interactive_rollback(
    *,
    project_root: Path,
    service_name: str,
) -> int:
    points = list_rollback_points(
        project_root=project_root,
        verify_integrity=True,
    )
    if not points:
        print("Доступных точек отката нет.")
        return 1

    print("\nДоступные точки отката:")
    for index, point in enumerate(points, start=1):
        local_time = point.created_at.astimezone()
        print(
            f"  {index}) {point.display_release} · "
            f"{point.source_short_commit} · "
            f"{local_time:%d.%m.%Y %H:%M:%S}"
        )
    try:
        selected_raw = input(f"\nВыберите точку [1-{len(points)}]: ").strip()
        selected_index = int(selected_raw)
    except (EOFError, ValueError):
        print("Некорректный выбор.")
        return 1
    if selected_index < 1 or selected_index > len(points):
        print("Некорректный выбор.")
        return 1

    point = points[selected_index - 1]
    print(
        "\nВНИМАНИЕ: база данных будет полностью восстановлена на момент "
        f"{point.created_at.astimezone():%d.%m.%Y %H:%M:%S}.\n"
        "Все добавленные после этого пользователи, оплаты, ключи, настройки "
        "и другие изменения базы данных будут потеряны.\n"
        "Локальные изменения Git-контролируемых файлов также будут перезаписаны."
    )
    try:
        confirmation = input("\nВведите ОТКАТИТЬ для подтверждения: ").strip()
    except EOFError:
        confirmation = ""
    if confirmation != "ОТКАТИТЬ":
        print("Откат отменён.")
        return 0

    result = perform_rollback(
        point.snapshot_id,
        project_root=project_root,
        service_name=service_name,
        manage_service=True,
    )
    print(result.message)
    return 0 if result.success else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YadrenoVPN update rollback manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--project-root", required=True)
    prepare.add_argument("--mode", required=True)
    prepare.add_argument("--requested-target")
    prepare.add_argument("--actor")

    mark = subparsers.add_parser("mark-applied")
    mark.add_argument("--project-root", required=True)
    mark.add_argument("--snapshot-id", required=True)
    mark.add_argument("--with-errors", action="store_true")

    interactive = subparsers.add_parser("interactive")
    interactive.add_argument("--project-root", required=True)
    interactive.add_argument("--service-name", default=SERVICE_NAME)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--project-root", required=True)
    rollback.add_argument("--snapshot-id", required=True)
    rollback.add_argument("--service-name", default=SERVICE_NAME)
    rollback.add_argument("--admin-id", type=int)
    rollback.add_argument("--start-delay", type=float, default=0)

    update = subparsers.add_parser("update")
    update.add_argument("--project-root", required=True)
    update.add_argument("--mode", required=True)
    update.add_argument("--target", required=True)
    update.add_argument("--strategy", choices=("pull", "reset"), required=True)
    update.add_argument("--actor", default="installer")
    update.add_argument("--service-name", default=SERVICE_NAME)
    update.add_argument("--admin-id", type=int)
    update.add_argument("--clean-untracked", action="store_true")
    update.add_argument("--block-updates", action="store_true")

    apply_update = subparsers.add_parser("apply-update")
    apply_update.add_argument("--project-root", required=True)
    apply_update.add_argument("--snapshot-id", required=True)
    apply_update.add_argument("--target", required=True)
    apply_update.add_argument("--strategy", choices=("pull", "reset"), required=True)
    apply_update.add_argument("--service-name", default=SERVICE_NAME)
    apply_update.add_argument("--admin-id", type=int)
    apply_update.add_argument("--clean-untracked", action="store_true")
    apply_update.add_argument("--block-updates", action="store_true")
    apply_update.add_argument("--start-delay", type=float, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point used by ``install.sh`` and systemd workers."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            snapshot = create_pre_update_snapshot(
                update_mode=args.mode,
                requested_target=args.requested_target,
                actor=args.actor,
                project_root=args.project_root,
            )
            print(snapshot.snapshot_id)
            return 0
        if args.command == "mark-applied":
            mark_snapshot_applied(
                args.snapshot_id,
                project_root=args.project_root,
                with_errors=args.with_errors,
            )
            print(args.snapshot_id)
            return 0
        if args.command == "interactive":
            return _interactive_rollback(
                project_root=_resolve_project_root(args.project_root),
                service_name=args.service_name,
            )
        if args.command == "rollback":
            if args.start_delay > 0:
                time.sleep(min(args.start_delay, 10))
            result = perform_rollback(
                args.snapshot_id,
                project_root=args.project_root,
                service_name=args.service_name,
                admin_id=args.admin_id,
                manage_service=True,
            )
            print(result.message)
            return 0 if result.success else 1
        if args.command == "update":
            result = run_managed_update(
                update_mode=args.mode,
                target=args.target,
                strategy=args.strategy,
                actor=args.actor,
                project_root=args.project_root,
                service_name=args.service_name,
                admin_id=args.admin_id,
                clean_untracked=args.clean_untracked,
                block_updates=args.block_updates,
                manage_service=True,
            )
            print(result.message)
            return 0 if result.success else 1
        if args.command == "apply-update":
            if args.start_delay > 0:
                time.sleep(min(args.start_delay, 10))
            result = perform_update_transaction(
                args.snapshot_id,
                target=args.target,
                strategy=args.strategy,
                project_root=args.project_root,
                service_name=args.service_name,
                admin_id=args.admin_id,
                clean_untracked=args.clean_untracked,
                block_updates=args.block_updates,
                manage_service=True,
            )
            print(result.message)
            return 0 if result.success else 1
    except UpdateRollbackError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        logger.exception("Unexpected update rollback error")
        print(f"Критическая ошибка: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
