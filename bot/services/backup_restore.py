"""Prepare Telegram backup data without touching the running installation."""
from __future__ import annotations

from localization import text as _tr

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backup_io import copy_stream, supported_zip_entry
from bot.services import update_rollback as maintenance
from database.db_backup import validate_backup_database
from web_tools.paths import atomic_write, canonical, local_path, relative_name

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESTORE_TREES = ('custom_extensions', 'custom_web', 'web_runtime')
JOB_FILENAME = 'backup_restore.json'


def require_admin(admin_id: int) -> None:
    from bot.utils.admin import is_admin
    if not is_admin(admin_id):
        raise PermissionError(_tr('backups.authorized.1'))


def job_directory(root: Path, job_id: str) -> Path:
    # Reuse the registered maintenance executor's directory and retention policy.
    maintenance._safe_snapshot_dir(root, job_id)
    return local_path(root, f'backup/pre_update/{job_id}')


def create_restore_job(admin_id: int, filename: str, *, root: Path = PROJECT_ROOT) -> tuple[str, Path]:
    require_admin(admin_id)
    job_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_') + uuid4().hex[:8]
    folder = job_directory(root, job_id)
    folder.mkdir(parents=True, mode=0o700)
    save_job(folder, {'admin_id': admin_id, 'filename': filename, 'status': 'downloading'})
    return job_id, folder


def save_job(folder: Path, job: dict) -> None:
    atomic_write(local_path(folder, JOB_FILENAME), canonical(job))


def load_job(root: Path, job_id: str, admin_id: int, *, status: str | None = None) -> tuple[Path, dict]:
    require_admin(admin_id)
    folder = job_directory(root, job_id)
    job = json.loads(local_path(folder, JOB_FILENAME).read_bytes())
    if job.get('admin_id') != admin_id:
        raise PermissionError(_tr('backup_restore.load_job.1'))
    if status is not None and job.get('status') != status:
        raise ValueError(_tr('backup_restore.load_job.2'))
    return folder, job


def fail_job(folder: Path, error: Exception) -> None:
    job = json.loads(local_path(folder, JOB_FILENAME).read_bytes())
    job.update(status='failed', error=str(error))
    save_job(folder, job)


def _archive_entries(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    entries, names = [], set()
    for info in archive.infolist():
        name = info.orig_filename[:-1] if info.is_dir() else info.orig_filename
        # Use the same path contract as installation-owned UI files, with hidden sources allowed.
        relative_name(name, hidden=True)
        if any(part.endswith(('.', ' ')) for part in name.split('/')):
            raise ValueError(_tr('backup_restore.archive_entries.1'))
        if name.casefold() in names:
            raise ValueError(_tr('backup_restore.archive_entries.2'))
        names.add(name.casefold())
        if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFDIR and not info.is_dir():
            raise ValueError(_tr('backup_restore.archive_entries.3'))
        if not supported_zip_entry(info, directories=True):
            raise ValueError(_tr('backup_restore.archive_entries.4'))
        top = name.split('/')[0]
        panel = re.fullmatch(r'server_[^/]+_x-ui\.(db|dump)', name)
        if name != 'vpn_bot.db' and top not in RESTORE_TREES and not panel:
            raise ValueError(_tr('backup_restore.archive_entries.5', p0=f'{name}'))
        if (top in RESTORE_TREES and name == top and not info.is_dir()
                or top not in RESTORE_TREES and info.is_dir()):
            raise ValueError(_tr('backup_restore.archive_entries.6'))
        if name.startswith('web_runtime/secrets/') and name != 'web_runtime/secrets/ed25519.key':
            raise ValueError(_tr('backup_restore.archive_entries.7'))
        entries.append((info, name))
    if 'vpn_bot.db' not in names:
        raise ValueError(_tr('backup_restore.archive_entries.8'))
    return entries


def _extract_archive(folder: Path) -> tuple[Path, list[str], int]:
    stage = local_path(folder, 'prepared', directory=True)
    trees, panels = set(), 0
    with zipfile.ZipFile(local_path(folder, 'original.zip')) as archive:
        entries = _archive_entries(archive)
        if sum(info.file_size for info, _ in entries) > shutil.disk_usage(folder).free:
            raise ValueError(_tr('backup_restore.extract_archive.1'))
        for info, name in entries:
            top = name.split('/')[0]
            panel = top not in RESTORE_TREES and name != 'vpn_bot.db'
            if top in RESTORE_TREES:
                trees.add(top)
            if info.is_dir():
                local_path(stage, name, directory=True, hidden=True)
                continue
            destination = local_path(stage, name, hidden=True)
            if panel:
                # Verify CRC without applying or expanding remote panel backups.
                with archive.open(info) as source:
                    copy_stream(source)
                panels += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(info) as source, destination.open('xb') as target:
                mode = 0o600 | ((info.external_attr >> 16) & stat.S_IXUSR)
                os.chmod(destination, 0o600 if name == 'web_runtime/secrets/ed25519.key' else mode)
                copy_stream(source, target)
    return stage, sorted(trees), panels


def validate_restored_ui(stage: Path) -> bool:
    from web_tools.compatibility import current_capabilities
    from web_tools.package import signing_identity
    from web_tools.publication import read_pointer
    from web_tools.release import publication
    from web_tools.paths import publication_lock

    runtime = local_path(stage, 'web_runtime')
    key = local_path(runtime, 'secrets/ed25519.key')
    identity = local_path(runtime, 'identity.json')
    has_identity = key.exists() or identity.exists()
    if has_identity:
        with publication_lock(runtime):
            signing_identity(runtime)
    pointer = read_pointer(runtime)
    if pointer['current']:
        if not has_identity:
            raise ValueError(_tr('backup_restore.validate_restored_ui.1'))
        publication(runtime, pointer['current'], current_capabilities())
    return has_identity


def _inventory(stage: Path) -> dict[str, str | None]:
    inventory = {}
    for path in sorted(stage.rglob('*')):
        name = path.relative_to(stage).as_posix()
        checked = local_path(stage, name, hidden=True)
        if not checked.is_dir() and not stat.S_ISREG(checked.stat().st_mode):
            raise ValueError(_tr('backup_restore.inventory.1'))
        inventory[name] = None if checked.is_dir() else maintenance._sha256_file(checked)
    return inventory


def prepare_restore(job_id: str, admin_id: int, *, root: Path = PROJECT_ROOT) -> dict:
    folder, job = load_job(root, job_id, admin_id, status='downloading')
    try:
        stage, trees, panels = _extract_archive(folder)
        database = local_path(stage, 'vpn_bot.db')
        original_version = validate_backup_database(database)
        result = subprocess.run(
            [sys.executable, '-m', 'database.migration_runner', '--project-root', str(root),
             '--database', str(database)],
            cwd=root, capture_output=True, timeout=300,
        )
        atomic_write(folder / 'migration.log', result.stdout + result.stderr)
        if result.returncode:
            raise ValueError(_tr('backup_restore.prepare_restore.1'))
        version = validate_backup_database(database)
        signing_key = validate_restored_ui(stage)
        job.update(status='prepared', trees=trees, panels=panels, signing_key=signing_key,
                   source_schema=original_version, schema=version, inventory=_inventory(stage),
                   commit=maintenance._current_commit(root),
                   archive_sha256=maintenance._sha256_file(folder / 'original.zip'))
        save_job(folder, job)
        return job
    except Exception as exc:
        fail_job(folder, exc)
        raise


def verify_prepared(root: Path, job_id: str, admin_id: int, *, status: str) -> tuple[Path, dict]:
    folder, job = load_job(root, job_id, admin_id, status=status)
    if job['commit'] != maintenance._current_commit(root):
        raise ValueError(_tr('backup_restore.verify_prepared.1'))
    if (job['inventory'] != _inventory(folder / 'prepared')
            or job['archive_sha256'] != maintenance._sha256_file(folder / 'original.zip')):
        raise ValueError(_tr('backup_restore.verify_prepared.2'))
    return folder, job


def cancel_restore(job_id: str, admin_id: int, *, root: Path = PROJECT_ROOT) -> None:
    folder, job = load_job(root, job_id, admin_id, status='prepared')
    job['status'] = 'cancelled'
    save_job(folder, job)
