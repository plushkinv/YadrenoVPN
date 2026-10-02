"""Administrator backup creation and confirmation-driven restoration."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin_backups import backup_confirmation_kb, backup_settings_kb
from bot.services import backup_restore
from bot.services.backup_restore_worker import schedule_restore
from bot.services.scheduler import send_backup_archive
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, safe_edit_or_send

router = Router()
logger = logging.getLogger(__name__)
_creation_lock = asyncio.Lock()
_restore_lock = asyncio.Lock()


async def _authorized(callback: CallbackQuery) -> bool:
    if not is_admin(callback.from_user.id):
        await callback.answer('Доступно только администратору', show_alert=True)
        return False
    if not isinstance(callback.message, Message):
        await callback.answer('Сообщение недоступно. Откройте чат с ботом.', show_alert=True)
        return False
    return True


@router.callback_query(F.data == 'admin_backup_create')
async def create_backup(callback: CallbackQuery, bot: Bot) -> None:
    if not await _authorized(callback):
        return
    if _creation_lock.locked():
        await callback.answer('Бэкап уже создаётся', show_alert=True)
        return
    async with _creation_lock:
        await callback.answer()
        status = await safe_edit_or_send(callback.message, '📦 <b>Создание бэкапа</b>\n\nСобираю данные…')
        delivered = await send_backup_archive(bot, recipient_id=callback.from_user.id)
        text = ('📦 <b>Бэкап создан</b>\n\nАрхив отправлен вам в личный чат.' if delivered else
                '❌ <b>Бэкап не отправлен</b>\n\nНе удалось создать или отправить архив. Подробности в логах.')
        await safe_edit_or_send(status, text, reply_markup=backup_settings_kb())


def _preview(job: dict) -> str:
    composition = ['• База бота: пользователи, настройки, шаблоны, страницы и данные расширений.']
    if 'custom_extensions' in job['trees']:
        composition.append('• Файлы расширений.')
    if 'custom_web' in job['trees'] or 'web_runtime' in job['trees']:
        composition.append('• Файлы веб-интерфейса и Mini App.')
    if job['signing_key']:
        composition.append('• Ключ подписи Mini App и его публичная идентичность.')
    text = (f'📥 <b>Восстановление бэкапа</b>\n\nАрхив: <code>{escape_html(job["filename"])}</code>\n\n'
            'Будут восстановлены:\n' + '\n'.join(composition))
    if job['source_schema'] != job['schema']:
        text += '\n\nКопия базы обновлена до версии этой установки.'
    text += ('\n\n⚠️ Текущие данные из перечисленных разделов будут заменены. '
             'Изменения после создания бэкапа будут потеряны. Бот перезапустится.\n\n'
             'Копии VPN-панелей останутся в архиве. Код и config.py сохранятся. '
             'После переноса подключите домен и HTTPS через установщик.')
    return text


@router.callback_query(F.data == 'admin_backup_download')
async def download_backup(callback: CallbackQuery, bot: Bot) -> None:
    if not await _authorized(callback):
        return
    document = callback.message.document
    sender = callback.message.from_user
    if document is None or sender is None or sender.id != bot.id:
        await callback.answer('Откройте сообщение с архивом, отправленное этим ботом.', show_alert=True)
        return
    if _restore_lock.locked():
        await callback.answer('Бэкап уже обрабатывается. Дождитесь результата.', show_alert=True)
        return
    async with _restore_lock:
        await callback.answer()
        # Keep the original Telegram document and its reusable file_id intact.
        status = await safe_edit_or_send(
            callback.message, '📥 <b>Загрузка бэкапа</b>\n\nСкачиваю и проверяю архив…', force_new=True,
        )
        folder = None
        downloaded = False
        try:
            job_id, folder = backup_restore.create_restore_job(
                callback.from_user.id, document.file_name or 'backup.zip',
            )
            archive = folder / 'original.zip'
            with archive.open('xb') as target:
                archive.chmod(0o600)
                await bot.download(document.file_id, destination=target)
            downloaded = True
            job = await asyncio.to_thread(backup_restore.prepare_restore, job_id, callback.from_user.id)
        except Exception as exc:
            logger.exception('Cannot prepare Telegram backup')
            if folder is not None:
                backup_restore.fail_job(folder, exc)
            text = ('❌ <b>Не удалось подготовить бэкап</b>\n\n' + escape_html(str(exc)[:1000]) if downloaded else
                    '❌ <b>Не удалось скачать бэкап</b>\n\nTelegram не выдал файл. '
                    'Стандартный Bot API позволяет скачивать файлы до 20 МБ.')
            text += '\n\nРабочие данные не изменены.'
            await safe_edit_or_send(status, text, reply_markup=backup_settings_kb())
            return
        await safe_edit_or_send(status, _preview(job), reply_markup=backup_confirmation_kb(job_id))


@router.callback_query(F.data.startswith('backup_apply:'))
async def confirm_backup(callback: CallbackQuery) -> None:
    if not await _authorized(callback):
        return
    if _restore_lock.locked():
        await callback.answer('Бэкап уже обрабатывается. Дождитесь результата.', show_alert=True)
        return
    async with _restore_lock:
        await callback.answer()
        job_id = callback.data.split(':', 1)[1]
        try:
            await asyncio.to_thread(schedule_restore, job_id, callback.from_user.id)
        except Exception as exc:
            logger.exception('Cannot schedule backup restore')
            await safe_edit_or_send(callback.message,
                '❌ <b>Восстановление не запущено</b>\n\n' + escape_html(str(exc)[:1000]),
                reply_markup=backup_settings_kb())
            return
        await safe_edit_or_send(callback.message,
            '📥 <b>Восстановление запущено</b>\n\nБот перезапустится. Сообщу результат после проверки запуска.')


@router.callback_query(F.data.startswith('backup_cancel:'))
async def cancel_backup(callback: CallbackQuery) -> None:
    if not await _authorized(callback):
        return
    if _restore_lock.locked():
        await callback.answer('Дождитесь завершения текущей операции.', show_alert=True)
        return
    async with _restore_lock:
        await callback.answer()
        try:
            backup_restore.cancel_restore(callback.data.split(':', 1)[1], callback.from_user.id)
        except Exception as exc:
            await safe_edit_or_send(callback.message,
                '⚠️ <b>Запрос уже обработан</b>\n\n' + escape_html(str(exc)[:1000]),
                reply_markup=backup_settings_kb())
            return
        await safe_edit_or_send(callback.message,
            '📥 <b>Восстановление отменено</b>\n\nРабочие данные не изменены. Архив сохранён.',
            reply_markup=backup_settings_kb())
