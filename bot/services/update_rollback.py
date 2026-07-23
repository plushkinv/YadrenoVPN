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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


logger = logging.getLogger(__name__)

MANIFEST_FORMAT_VERSION = 1
PRE_UPDATE_DIRNAME = "pre_update"
MANIFEST_FILENAME = "manifest.json"
DATABASE_BACKUP_FILENAME = "vpn_bot.db"
ROLLBACK_RUNNER_FILENAME = "rollback_runner.py"
ROLLBACK_RESULT_FILENAME = "rollback_result.json"
OPERATION_LOCK_FILENAME = ".operation.lock"
MAX_ROLLBACK_POINTS = 3
ROLLBACK_RETENTION_DAYS = 7
SERVICE_NAME = "yadreno-vpn"
UNKNOWN_RELEASE = "unknown"

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


def _quick_check_database(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise UpdateRollbackError(f"Database backup is missing or empty: {path}")
    try:
        with sqlite3.connect(str(path), timeout=30) as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise UpdateRollbackError(f"Cannot validate SQLite backup: {exc}") from exc
    if not row or row[0] != "ok":
        raise UpdateRollbackError("SQLite backup integrity check failed")


def _backup_database(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file():
        raise UpdateRollbackError(f"Bot database is missing: {source_path}")
    destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination_path.unlink(missing_ok=True)
    try:
        with sqlite3.connect(str(source_path), timeout=30) as source:
            with sqlite3.connect(str(destination_path), timeout=30) as target:
                source.backup(target)
        _quick_check_database(destination_path)
        try:
            destination_path.chmod(0o600)
        except OSError:
            pass
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
        _quick_check_database(database_path)

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
    commit = _current_commit(root)
    release = _release_from_subject(_commit_subject(root, commit))
    created_at = _utc_now()
    snapshot_id = (
        created_at.strftime("%Y%m%dT%H%M%S%fZ")
        + "_"
        + commit[:8]
    )
    raw_snapshot_root = _pre_update_root(root)
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
    try:
        backup_path = temp_dir / DATABASE_BACKUP_FILENAME
        _backup_database(_database_path(root), backup_path)
        database_size = backup_path.stat().st_size
        database_hash = _sha256_file(backup_path)

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
                "status": "prepared",
                "applied_at": None,
                "applied_commit": None,
                "applied_release": None,
            },
            "database": {
                "file": DATABASE_BACKUP_FILENAME,
                "size": database_size,
                "sha256": database_hash,
            },
        }
        _atomic_write_json(temp_dir / MANIFEST_FILENAME, manifest)
        os.replace(temp_dir, final_dir)
        _fsync_directory(snapshot_root)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

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

    result_path = snapshot_root / ROLLBACK_RESULT_FILENAME
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
        logger.warning("Cannot remove stale rollback result %s: %s", result_path, exc)
    return removed


@contextmanager
def update_operation_lock(
    project_root: str | Path | None = None,
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

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateRollbackError(
                    "Another update or rollback operation is already running"
                ) from exc
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
        stage="Installing rollback dependencies",
    )


def _restore_database_atomically(source: Path, destination: Path) -> None:
    _quick_check_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    current_mode: int | None = None
    try:
        current_mode = destination.stat().st_mode & 0o777
    except OSError:
        pass
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=".vpn_bot.rollback.",
        suffix=".db",
        dir=str(destination.parent),
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        _quick_check_database(temp_path)
        if current_mode is not None:
            temp_path.chmod(current_mode)
        for suffix in ("-wal", "-shm"):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _rollback_result_path(project_root: Path) -> Path:
    return _pre_update_root(project_root) / ROLLBACK_RESULT_FILENAME


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
) -> RollbackExecutionResult:
    """Restore Git and the bot database to a selected pre-update snapshot."""
    root = _resolve_project_root(project_root)
    with update_operation_lock(root):
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
