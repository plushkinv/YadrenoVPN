"""Apply a confirmed backup through the existing independent maintenance service."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path

from bot.services import update_rollback as maintenance
from bot.services.backup_restore import (
    PROJECT_ROOT, RESTORE_TREES, fail_job, load_job, require_admin, save_job,
    validate_restored_ui, verify_prepared,
)
from web_tools.paths import atomic_write, canonical, local_path

logger = logging.getLogger(__name__)
RESULT_FILENAME = 'backup_restore_result.json'


def schedule_restore(job_id: str, admin_id: int, *, root: Path = PROJECT_ROOT) -> None:
    require_admin(admin_id)
    if maintenance._repository_guard_is_active():
        raise ValueError('Дождитесь завершения текущей операции Yadreno Admin')
    with maintenance.update_operation_lock(root):
        folder, job = verify_prepared(root, job_id, admin_id, status='prepared')
        for request_path in (root / 'backup/pre_update').glob('*/service_request.json'):
            request = json.loads(local_path(request_path.parent, request_path.name).read_bytes())
            if request.get('status') in {'pending', 'running'}:
                raise ValueError('Уже выполняется обслуживание бота. Дождитесь его завершения.')
        maintenance.install_registered_updater_service(project_root=root)
        unit = maintenance._stage_registered_service_request(
            root, job_id, operation='restore_backup', admin_id=admin_id, start_delay=2,
        )
        try:
            job['status'] = 'scheduled'
            save_job(folder, job)
            result = maintenance._run_command(['systemctl', 'start', '--no-block', unit], cwd=root, timeout=30)
            if result.returncode:
                raise RuntimeError((result.stdout + result.stderr).strip() or 'Не удалось запустить восстановление')
        except Exception as exc:
            maintenance._discard_registered_service_request(root, job_id)
            fail_job(folder, exc)
            raise


def _check_destinations(root: Path, folder: Path, job: dict) -> None:
    database = local_path(root, 'database/vpn_bot.db')
    if database.exists() and not database.is_file():
        raise ValueError('Рабочая база не является обычным файлом')
    if database.parent.stat().st_dev != folder.stat().st_dev:
        raise ValueError('backup и database должны находиться на одной файловой системе')
    if any(tree not in RESTORE_TREES for tree in job['trees']):
        raise ValueError('Неизвестный каталог восстановления')
    for tree in job['trees']:
        destination = local_path(root, tree)
        if (destination.parent.stat().st_dev != folder.stat().st_dev or destination.exists()
                and (not destination.is_dir() or destination.stat().st_dev != folder.stat().st_dev)):
            raise ValueError(f'Каталог {tree} нельзя заменить на этой файловой системе')


def _apply_files(root: Path, folder: Path, job: dict) -> None:
    stage = folder / 'prepared'
    replaced = local_path(folder, 'replaced', directory=True)
    for tree in job['trees']:
        source, destination = local_path(stage, tree), local_path(root, tree)
        if destination.exists():
            os.replace(destination, replaced / tree)
        os.replace(source, destination)
    key = local_path(root, 'web_runtime/secrets/ed25519.key')
    if 'web_runtime' in job['trees'] and key.exists():
        key.parent.chmod(0o700)
        key.chmod(0o600)
    maintenance._restore_database_atomically(stage / 'vpn_bot.db', local_path(root, 'database/vpn_bot.db'))


def _write_result(root: Path, job_id: str, admin_id: int, success: bool, message: str) -> None:
    atomic_write(local_path(root, 'backup/pre_update/' + RESULT_FILENAME), canonical({
        'job_id': job_id, 'admin_id': admin_id, 'success': success, 'message': message,
    }))


def perform_restore(job_id: str, admin_id: int, *, root: Path = PROJECT_ROOT,
                    service_name: str = maintenance.SERVICE_NAME) -> maintenance.RollbackExecutionResult:
    """Stop, replace, and require the normal startup acknowledgement; no automatic rollback."""
    require_admin(admin_id)
    stopped = False
    folder = None
    with ExitStack() as locks:
        try:
            locks.enter_context(maintenance.update_operation_lock(root))
            folder, job = verify_prepared(root, job_id, admin_id, status='scheduled')
            _check_destinations(root, folder, job)
            maintenance._systemctl('stop', service_name, project_root=root)
            stopped = True
            _apply_files(root, folder, job)
            maintenance._prepare_update_health(root, snapshot_id=job_id, target_commit=job['commit'])
            maintenance._systemctl('start', service_name, project_root=root)
            if not maintenance._wait_for_update_health(
                service_name, project_root=root, snapshot_id=job_id, target_commit=job['commit'],
            ):
                raise RuntimeError('Бот не подтвердил успешный запуск после восстановления')
            validate_restored_ui(root)
            message = 'Данные восстановлены из бэкапа. Бот успешно запущен.'
            job['status'] = 'success'
            save_job(folder, job)
            _write_result(root, job_id, admin_id, True, message)
            maintenance._accept_update_health(root, snapshot_id=job_id, target_commit=job['commit'])
            return maintenance.RollbackExecutionResult(True, message)
        except Exception as exc:
            logger.exception('Backup restore failed: %s', job_id)
            stop_error = ''
            if stopped:
                try:
                    maintenance._systemctl('stop', service_name, project_root=root)
                except Exception as stop_exc:
                    stop_error = f' Не удалось подтвердить остановку службы: {stop_exc}.'
                maintenance._update_health_path(root).unlink(missing_ok=True)
            if folder is None:
                folder, _ = load_job(root, job_id, admin_id)
            fail_job(folder, exc)
            detail = maintenance._bounded_detail(exc)
            state = ('Служба остановлена; данные могли быть заменены. Автоматический откат не выполнялся.'
                     if stopped and not stop_error else
                     'Данные могли быть заменены; состояние службы требует проверки.' if stopped else
                     'Рабочие данные не заменялись.')
            message = (f'{detail}\n{state}{stop_error}\nАрхив и диагностика: '
                       f'backup/pre_update/{job_id}/')
            _write_result(root, job_id, admin_id, False, message)
            try:
                asyncio.run(_notify_from_worker(root))
            except Exception:
                logger.exception('Cannot deliver backup failure; saved result remains available')
            return maintenance.RollbackExecutionResult(False, message)


async def notify_pending_backup_restore_result(bot, *, root: Path = PROJECT_ROOT) -> bool:
    from bot.utils.text import escape_html
    result_path = local_path(root, 'backup/pre_update/' + RESULT_FILENAME)
    if not result_path.is_file():
        return False
    result = json.loads(result_path.read_bytes())
    require_admin(result['admin_id'])
    title = '✅ <b>Бэкап восстановлен</b>' if result['success'] else '❌ <b>Ошибка восстановления бэкапа</b>'
    await bot.send_message(result['admin_id'], title + '\n\n' + escape_html(result['message']), parse_mode='HTML')
    result_path.unlink(missing_ok=True)
    return True


async def _notify_from_worker(root: Path) -> None:
    from aiogram import Bot
    from bot.middlewares.parse_mode_fallback import SafeParseSession
    from config import BOT_TOKEN

    async with Bot(token=BOT_TOKEN, session=SafeParseSession()) as bot:
        await notify_pending_backup_restore_result(bot, root=root)
