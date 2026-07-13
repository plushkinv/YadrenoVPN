"""Production-safe reset of local bot customization."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import connection as db_connection
from database.db_backup import backup_bot_database_to
from database.requests import reset_customization_database

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backup"
CUSTOM_RESET_CONFIRMATION_PHRASE = "СБРОСИТЬ КАСТОМЫ"

PRESERVED_DATA_LABELS = (
    "users",
    "vpn_keys",
    "servers/server_groups",
    "tariffs/tariff_groups",
    "payments/payment_provider_orders",
    "promo/coupon/referral data",
    "support dialogs",
    "balance/key operation history",
    "payment provider credentials",
)


@dataclass
class CustomizationResetReport:
    """Result of a customization reset preview or apply run."""

    dry_run: bool
    backup_paths: list[Path] = field(default_factory=list)
    db_actions: list[str] = field(default_factory=list)
    file_actions: list[str] = field(default_factory=list)
    runtime_actions: list[str] = field(default_factory=list)


def _resolve_inside(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to touch path outside project: {resolved_path}") from exc
    return resolved_path


def _assert_tree_inside(path: Path, root: Path) -> None:
    _resolve_inside(path, root)
    if not path.is_dir():
        return
    for child in path.rglob("*"):
        _resolve_inside(child, root)


def _coerce_path(path: str | Path | None, default: Path, project_root: Path) -> Path:
    if path is None:
        return default
    result = Path(path)
    if not result.is_absolute():
        result = project_root / result
    return result


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _relative_display(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _create_database_backup(
    db_path: Path,
    backup_dir: Path,
) -> Path:
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_path = (backup_dir / f"{_timestamp()}__custom_reset__vpn_bot.db").resolve()
    original_db_path = db_connection.DB_PATH
    db_connection.DB_PATH = db_path
    try:
        return backup_bot_database_to(backup_path)
    finally:
        db_connection.DB_PATH = original_db_path


def _create_custom_extensions_backup(
    custom_extensions_dir: Path,
    backup_dir: Path,
) -> Path | None:
    if not custom_extensions_dir.exists() or not custom_extensions_dir.is_dir():
        return None
    if not any(custom_extensions_dir.iterdir()):
        return None

    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive_base = backup_dir / f"{_timestamp()}__custom_reset__custom_extensions"
    archive = shutil.make_archive(str(archive_base), "zip", custom_extensions_dir)
    return Path(archive)


def reset_customization_files(
    project_root: str | Path | None = None,
    backup_dir: str | Path | None = None,
    *,
    dry_run: bool = True,
    create_backup: bool = True,
) -> tuple[list[str], list[Path]]:
    """Removes local custom extension files and returns actions/backups."""
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT.resolve()
    backups_root = _resolve_inside(_coerce_path(backup_dir, root / "backup", root), root)
    custom_extensions_dir = _resolve_inside(root / "custom_extensions", root)
    actions: list[str] = []
    backups: list[Path] = []

    if not custom_extensions_dir.exists():
        return ["custom_extensions directory is missing; skipped"], backups
    if not custom_extensions_dir.is_dir():
        return ["custom_extensions path is not a directory; skipped"], backups
    _assert_tree_inside(custom_extensions_dir, root)

    if create_backup and not dry_run:
        backup = _create_custom_extensions_backup(custom_extensions_dir, backups_root)
        if backup is not None:
            backups.append(backup)

    entries = sorted(custom_extensions_dir.iterdir(), key=lambda item: item.name)
    actions.append(f"custom_extensions entries to delete: {len(entries)}")
    if dry_run:
        actions.extend(f"would delete {entry.relative_to(root)}" for entry in entries)
        return actions, backups

    for entry in entries:
        resolved_entry = _resolve_inside(entry, root)
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
            actions.append(f"deleted file {entry.relative_to(root)}")
        elif entry.is_dir():
            shutil.rmtree(resolved_entry)
            actions.append(f"deleted directory {entry.relative_to(root)}")
        else:
            entry.unlink(missing_ok=True)
            actions.append(f"deleted special path {entry.relative_to(root)}")

    try:
        custom_extensions_dir.rmdir()
        actions.append("removed empty custom_extensions directory")
    except OSError:
        actions.append("custom_extensions directory kept because it is not empty")

    return actions, backups


def reset_customization_runtime() -> list[str]:
    """Clears runtime registries populated by custom extensions."""
    from bot.utils.custom_extensions import reset_custom_extensions_runtime

    totals = reset_custom_extensions_runtime()
    before = totals["before"]
    after = totals["after"]
    changed = [
        f"{key}: {before.get(key, 0)} -> {after.get(key, 0)}"
        for key in sorted(before)
        if before.get(key, 0) != after.get(key, 0)
    ]
    if not changed:
        return ["custom extension runtime registries already clean"]
    return ["custom extension runtime registries cleared: " + ", ".join(changed)]


def run_customization_reset(
    *,
    dry_run: bool = True,
    project_root: str | Path | None = None,
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    skip_db: bool = False,
    skip_files: bool = False,
    create_backup: bool = True,
    reset_runtime: bool = False,
) -> CustomizationResetReport:
    """Previews or applies the customization reset."""
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT.resolve()
    db = _resolve_inside(_coerce_path(db_path, root / "database" / "vpn_bot.db", root), root)
    backups_root = _resolve_inside(_coerce_path(backup_dir, root / "backup", root), root)

    report = CustomizationResetReport(dry_run=dry_run)

    if create_backup and not dry_run:
        if not skip_db:
            report.backup_paths.append(_create_database_backup(db, backups_root))
        if not skip_files:
            custom_extensions_dir = _resolve_inside(root / "custom_extensions", root)
            if custom_extensions_dir.exists() and custom_extensions_dir.is_dir():
                _assert_tree_inside(custom_extensions_dir, root)
            file_backup = _create_custom_extensions_backup(custom_extensions_dir, backups_root)
            if file_backup is not None:
                report.backup_paths.append(file_backup)

    if not skip_db:
        report.db_actions = reset_customization_database(db, dry_run=dry_run)

    if not skip_files:
        report.file_actions, _ = reset_customization_files(
            root,
            backups_root,
            dry_run=dry_run,
            create_backup=False,
        )

    if reset_runtime and not dry_run:
        report.runtime_actions.extend(reset_customization_runtime())

    return report


async def run_customization_reset_for_bot(
    *,
    dry_run: bool = True,
    bot: Any = None,
    project_root: str | Path | None = None,
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> CustomizationResetReport:
    """Runs reset for Telegram flow and refreshes commands after apply."""
    report = await asyncio.to_thread(
        run_customization_reset,
        dry_run=dry_run,
        project_root=project_root,
        db_path=db_path,
        backup_dir=backup_dir,
        create_backup=True,
        reset_runtime=False,
    )

    if dry_run:
        return report

    report.runtime_actions.extend(reset_customization_runtime())
    if bot is not None:
        try:
            from bot.services.bot_commands import sync_bot_commands

            await sync_bot_commands(bot)
            report.runtime_actions.append("Telegram command menu synchronized")
        except Exception as exc:
            logger.exception("Failed to synchronize Telegram command menu after customization reset")
            report.runtime_actions.append(f"Telegram command menu sync failed: {exc}")
    return report


def format_report_for_cli(report: CustomizationResetReport, project_root: str | Path | None = None) -> str:
    """Formats a reset report for console output."""
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT.resolve()
    lines = [f"Customization reset: {'DRY RUN' if report.dry_run else 'APPLIED'}"]
    if report.backup_paths:
        lines.append("")
        lines.append("Backups:")
        lines.extend(f"  - {_relative_display(path, root)}" for path in report.backup_paths)
    if report.db_actions:
        lines.append("")
        lines.append("Database:")
        lines.extend(f"  - {action}" for action in report.db_actions)
    if report.file_actions:
        lines.append("")
        lines.append("Files:")
        lines.extend(f"  - {action}" for action in report.file_actions)
    if report.runtime_actions:
        lines.append("")
        lines.append("Runtime:")
        lines.extend(f"  - {action}" for action in report.runtime_actions)
    if report.dry_run:
        lines.append("")
        lines.append("No changes were made. Run with --apply to reset customization.")
    return "\n".join(lines)


__all__ = [
    "CUSTOM_RESET_CONFIRMATION_PHRASE",
    "CustomizationResetReport",
    "PRESERVED_DATA_LABELS",
    "format_report_for_cli",
    "reset_customization_files",
    "reset_customization_runtime",
    "run_customization_reset",
    "run_customization_reset_for_bot",
]
