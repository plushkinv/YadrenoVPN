"""
Module for automatic tasks.

Includes:
- Sending daily statistics to administrators
- Creating and sending an archive with backups (bot database + VPN panels)
- Synchronization of traffic with VPN servers (every 5 minutes)
- Notifications about ending traffic
"""

import asyncio
import json
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from io import BytesIO
from typing import Optional, Sequence

from aiogram import Bot
from aiogram.types import BufferedInputFile, ReplyKeyboardRemove

from config import ADMIN_IDS, GITHUB_REPO_URL
from database.requests import (
    get_all_servers, get_users_stats, get_keys_stats,
    get_daily_payments_stats, get_new_users_count_today,
    get_setting, get_expiring_keys, is_notification_sent_today, log_notification_sent,
    is_update_notifications_enabled, mark_user_bot_blocked
)
from database.db_backup import backup_bot_database_to
from bot.services.vpn_api import (
    calculate_panel_total_for_key,
    get_client_from_server_data,
    VPNAPIError,
    format_traffic,
    get_key_traffic_snapshot,
)
from bot.services.panels.base import PanelDatabaseBackup
from bot.utils.git_utils import check_for_updates
from bot.utils.update_block import is_update_blocked, get_blocked_message, try_unblock
from bot.utils.delivery import is_bot_blocked_error
from bot.utils.text import escape_html
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Path to the bot database
BOT_DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'vpn_bot.db')

# Project root folder and local backup folder
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKUP_DIR = os.path.join(PROJECT_ROOT, 'backup')

# How many days to store local backups
BACKUP_RETENTION_DAYS = 7


@dataclass(frozen=True)
class CollectedPanelBackup:
    """Backup of one VPN panel, downloaded once per daily cycle."""

    server_name: str
    filename: str
    backup: PanelDatabaseBackup


@dataclass(frozen=True)
class PanelBackupWarning:
    """Error downloading backup of a specific VPN panel."""

    server_name: str
    message: str


@dataclass(frozen=True)
class PanelBackupCollection:
    """The result of collecting backups of active VPN panels."""

    backups: tuple[CollectedPanelBackup, ...]
    warnings: tuple[PanelBackupWarning, ...]


def _safe_panel_backup_filename(server_name: str, extension: str) -> str:
    """Forms the name of the panel backup file according to the actual database format."""
    safe_name = str(server_name or "server").replace(" ", "_")
    safe_name = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in safe_name).strip(" .")
    if not safe_name:
        safe_name = "server"
    safe_extension = extension if extension.startswith(".") else f".{extension}"
    return f"server_{safe_name}_x-ui{safe_extension}"


def _short_panel_warning(message: str, limit: int = 140) -> str:
    """Limits the error text for Telegram caption."""
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


async def collect_panel_database_backups() -> PanelBackupCollection:
    """Downloads a backup of active VPN panels once per daily backup cycle."""
    backups: list[CollectedPanelBackup] = []
    warnings: list[PanelBackupWarning] = []

    for server in get_all_servers():
        if not server.get('is_active'):
            continue

        server_name = server.get('name') or f"server_{server.get('id', '')}".strip("_")
        try:
            client = get_client_from_server_data(server)
            backup = await client.get_database_backup()
            filename = _safe_panel_backup_filename(server_name, backup.extension)
            backups.append(
                CollectedPanelBackup(
                    server_name=server_name,
                    filename=filename,
                    backup=backup,
                )
            )
            logger.info(
                "Скачан бэкап панели %s: %s (%s, %s байт)",
                server_name,
                filename,
                backup.db_kind,
                len(backup.data),
            )
        except VPNAPIError as e:
            message = str(e)
            logger.warning("Не удалось скачать бэкап панели %s: %s", server_name, message)
            warnings.append(PanelBackupWarning(server_name=server_name, message=message))
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            logger.warning("Ошибка при скачивании бэкапа панели %s: %s", server_name, message)
            warnings.append(PanelBackupWarning(server_name=server_name, message=message))

    return PanelBackupCollection(backups=tuple(backups), warnings=tuple(warnings))


def build_backup_caption(today: str, panel_warnings: Sequence[PanelBackupWarning]) -> str:
    """Collects an HTML-safe caption for a Telegram document with a backup archive."""
    lines = [
        f"📦 <b>Ежедневный бэкап за {escape_html(today)}</b>",
        "",
        "Содержит базу данных бота и доступные бэкапы VPN-панелей.",
    ]
    if panel_warnings:
        lines.extend(["", "⚠️ <b>Предупреждения:</b>"])
        visible_warnings = panel_warnings[:3]
        for warning in visible_warnings:
            lines.append(
                "⚠️ Не удалось скачать бэкап панели "
                f"{escape_html(warning.server_name)}: "
                f"{escape_html(_short_panel_warning(warning.message))}"
            )
        hidden_count = len(panel_warnings) - len(visible_warnings)
        if hidden_count > 0:
            lines.append(f"⚠️ И ещё {hidden_count} ошибок panel backup; подробности в логах.")

    return "\n".join(lines)


async def collect_daily_stats() -> str:
    """
    Collects daily statistics for the report.
    
    Returns:
        Rich text statistics
    """
    # User statistics
    users = get_users_stats()
    new_users = get_new_users_count_today()
    
    # Key statistics
    keys = get_keys_stats()
    
    # Payment statistics
    payments = get_daily_payments_stats()
    
    # Server statistics
    servers = get_all_servers()
    servers_info = []
    
    for server in servers:
        if not server.get('is_active'):
            servers_info.append(f"  🔴 <b>{server['name']}</b> — выключен")
            continue
            
        try:
            client = get_client_from_server_data(server)
            stats = await client.get_stats()
            
            if stats.get('online'):
                traffic = format_traffic(stats.get('total_traffic_bytes', 0))
                cpu = stats.get('cpu_percent')
                cpu_text = f", CPU: {cpu}%" if cpu else ""
                online = stats.get('online_clients', 0)
                servers_info.append(
                    f"  🟢 <b>{server['name']}</b>: {online} онлайн, "
                    f"трафик: {traffic}{cpu_text}"
                )
            else:
                servers_info.append(f"  🔴 <b>{server['name']}</b> — недоступен")
        except Exception as e:
            logger.warning(f"Ошибка получения статистики сервера {server['name']}: {e}")
            servers_info.append(f"  ⚠️ <b>{server['name']}</b> — ошибка подключения")
    
    servers_text = "\n".join(servers_info) if servers_info else "  Нет серверов"
    
    # Generating the report text
    today = datetime.now().strftime("%d.%m.%Y")
    
    # Payments
    payments_total = payments.get('paid_count', 0)
    payments_cents = payments.get('paid_cents', 0)
    payments_stars = payments.get('paid_stars', 0)
    payments_rub = payments.get('paid_rub', 0)
    payments_pending = payments.get('pending_count', 0)
    
    payments_text = []
    if payments_cents > 0:
        payments_val = payments_cents / 100
        payments_str = f"{payments_val:g}".replace('.', ',')
        payments_text.append(f"${payments_str}")
    if payments_rub > 0:
        rub_str = f"{payments_rub:g}".replace('.', ',')
        payments_text.append(f"{rub_str} ₽")
    if payments_stars > 0:
        payments_text.append(f"⭐{payments_stars}")
    payments_sum = " + ".join(payments_text) if payments_text else "0"
    
    report = f"""📊 <b>Суточная статистика за {today}</b>

👥 <b>Пользователи:</b>
  Всего: {users.get('total', 0)}
  Активных: {users.get('active', 0)}
  Новых за сутки: {new_users}

🔑 <b>VPN-ключи:</b>
  Всего: {keys.get('total', 0)}
  Активных: {keys.get('active', 0)}
  Истёкших: {keys.get('expired', 0)}
  Создано за сутки: {keys.get('created_today', 0)}

💳 <b>Платежи за сутки:</b>
  Успешных: {payments_total}
  Ожидающих: {payments_pending}
  Сумма: {payments_sum}

🖥️ <b>Серверы:</b>
{servers_text}
"""
    return report


async def send_daily_stats(bot: Bot) -> None:
    """
    Sends daily statistics to all administrators.
    
    Args:
        bot: Bot instance
    """
    try:
        report = await collect_daily_stats()
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=report,
                    parse_mode="HTML",
                    reply_markup=ReplyKeyboardRemove()
                )
                logger.info(f"Статистика отправлена админу {admin_id}")
            except Exception as e:
                logger.warning(f"Не удалось отправить статистику админу {admin_id}: {e}")

        logger.info("✅ Суточная статистика отправлена")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке суточной статистики: {e}")


async def create_backup_archive(
    panel_backups: Optional[PanelBackupCollection] = None,
) -> Optional[bytes]:
    """
    Creates a ZIP archive with backups.
    
    Includes:
    - vpn_bot.db — bot database
    - server_NAME_x-ui.db/.dump — backup file of each available VPN panel
    
    Returns:
        ZIP archive bytes or None on error
    """
    temp_bot_db_backup = None
    try:
        if panel_backups is None:
            panel_backups = await collect_panel_database_backups()

        archive_buffer = BytesIO()
        
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Adding a bot database
            bot_db_path = os.path.abspath(BOT_DB_PATH)
            if os.path.exists(bot_db_path):
                os.makedirs(BACKUP_DIR, exist_ok=True)
                fd, temp_bot_db_backup = tempfile.mkstemp(
                    prefix='vpn_bot_snapshot_',
                    suffix='.db',
                    dir=BACKUP_DIR,
                )
                os.close(fd)
                snapshot_path = backup_bot_database_to(temp_bot_db_backup)
                zf.write(snapshot_path, 'vpn_bot.db')
                logger.info(f"Добавлен в архив: vpn_bot.db ({snapshot_path.stat().st_size} байт)")
            else:
                logger.warning(f"База данных бота не найдена: {bot_db_path}")
            
            # Add already downloaded backup files of VPN panels
            for item in panel_backups.backups:
                try:
                    zf.writestr(item.filename, item.backup.data)
                    logger.info(
                        f"Добавлен в архив: {item.filename} ({len(item.backup.data)} байт)"
                    )
                except Exception as e:
                    logger.warning(
                        f"Не удалось добавить бэкап панели {item.server_name} в архив: {e}"
                    )
        
        archive_buffer.seek(0)
        return archive_buffer.read()
        
    except Exception as e:
        logger.error(f"Ошибка при создании архива бэкапов: {e}")
        return None
    finally:
        if temp_bot_db_backup:
            try:
                os.unlink(temp_bot_db_backup)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"Не удалось удалить временный бэкап БД бота {temp_bot_db_backup}: {e}")


async def save_local_backup(
    panel_backups: Optional[PanelBackupCollection] = None,
) -> None:
    """
    Saves local copies of all databases to the backup/YYYY-MM-DD/ folder.
    
    Panel files are saved in the actual format: SQLite .db
    or PostgreSQL .dump.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(BACKUP_DIR, today)
    
    try:
        if panel_backups is None:
            panel_backups = await collect_panel_database_backups()

        os.makedirs(day_dir, exist_ok=True)
        
        # Saving the bot database
        bot_db_path = os.path.abspath(BOT_DB_PATH)
        if os.path.exists(bot_db_path):
            dest = os.path.join(day_dir, 'vpn_bot.db')
            backup_bot_database_to(dest)
            logger.info(f"Локальный бэкап: vpn_bot.db ({os.path.getsize(dest)} байт)")
        else:
            logger.warning(f"База данных бота не найдена: {bot_db_path}")
        
        # We save already downloaded backup files of VPN panels
        for item in panel_backups.backups:
            try:
                dest = os.path.join(day_dir, item.filename)
                
                with open(dest, 'wb') as f:
                    f.write(item.backup.data)
                
                logger.info(f"Локальный бэкап: {item.filename} ({len(item.backup.data)} байт)")
            except Exception as e:
                logger.warning(
                    f"Не удалось сохранить локальный бэкап панели {item.server_name}: {e}"
                )
        
        logger.info(f"✅ Локальные бэкапы сохранены в {day_dir}")
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении локальных бэкапов: {e}")


def cleanup_old_backups() -> None:
    """
    Recursively deletes any files and links older than the retention period.

    Names and extensions are not taken into account: the backup directory is only for
    for temporary backups. Symbolic links are not bypassed.
    After deleting files, empty subfolders are cleared.
    """
    if not os.path.exists(BACKUP_DIR):
        return

    backup_root = os.path.abspath(BACKUP_DIR)
    if os.path.islink(backup_root):
        logger.error("Очистка backup отменена: корневой каталог является ссылкой")
        return
    cutoff_timestamp = (
        datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
    ).timestamp()
    removed_count = 0

    try:
        for current_root, dirnames, filenames in os.walk(
            backup_root,
            topdown=False,
            followlinks=False,
        ):
            current_root = os.path.abspath(current_root)
            if os.path.commonpath([backup_root, current_root]) != backup_root:
                logger.error("Пропущен путь за пределами backup: %s", current_root)
                continue

            for filename in filenames:
                file_path = os.path.join(current_root, filename)
                try:
                    if os.lstat(file_path).st_mtime < cutoff_timestamp:
                        os.unlink(file_path)
                        removed_count += 1
                        logger.info("Удалён старый бэкап: %s", file_path)
                except FileNotFoundError:
                    continue
                except OSError as e:
                    logger.warning("Не удалось удалить старый бэкап %s: %s", file_path, e)

            for dirname in dirnames:
                dir_path = os.path.join(current_root, dirname)
                try:
                    if os.path.islink(dir_path):
                        if os.lstat(dir_path).st_mtime < cutoff_timestamp:
                            os.unlink(dir_path)
                            removed_count += 1
                            logger.info("Удалена старая ссылка из backup: %s", dir_path)
                        continue
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except FileNotFoundError:
                    continue
                except OSError as e:
                    logger.warning("Не удалось очистить каталог бэкапа %s: %s", dir_path, e)

        if removed_count > 0:
            logger.info("🗑️ Удалено старых файлов и ссылок бэкапов: %s", removed_count)

    except Exception as e:
        logger.error(f"Ошибка при очистке локальных бэкапов: {e}")


async def send_backup_archive(bot: Bot) -> None:
    """
    Creates and sends a backup archive to all administrators.
    It also saves local copies and cleans up old backups.
    
    Args:
        bot: Bot instance
    """
    try:
        panel_backups = await collect_panel_database_backups()

        # We save local backups from already collected data
        await save_local_backup(panel_backups)
        
        # Deleting backups older than 7 days
        cleanup_old_backups()
        
        # Create a ZIP archive for sending to Telegram
        archive_data = await create_backup_archive(panel_backups)
        
        if not archive_data:
            logger.error("Не удалось создать архив бэкапов")
            return
        
        # File name with date
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"backup_{today}.zip"
        caption = build_backup_caption(today, panel_backups.warnings)
        
        # Sent to admins
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=BufferedInputFile(archive_data, filename=filename),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=ReplyKeyboardRemove()
                )
                logger.info(f"Бэкап отправлен админу {admin_id}")
            except Exception as e:
                logger.warning(f"Не удалось отправить бэкап админу {admin_id}: {e}")
        
        logger.info(f"✅ Бэкап отправлен ({len(archive_data)} байт)")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке бэкапа: {e}")


async def check_and_send_expiry_notifications(bot: Bot) -> None:
    """
    Checks and sends notifications about expiring keys.
    
    Uses a single HTML contract. Dynamic substitutions 
    are escaped via escape_html().
    """
    logger.info("⏳ Запуск проверки истекающих ключей...")
    try:
        from bot.utils.event_placeholders import build_user_event_context, render_event_placeholders
        from bot.utils.text import send_media_or_text
        days = int(get_setting('notification_days', '3'))
        from bot.utils.message_editor import get_message_data
        
        # Default text in HTML
        default_notification = (
            '⚠️ <b>Ваш VPN-ключ %ключ_имя% скоро истекает!</b>\n\n'
            'Через %ключ_дней_до_окончания% дней закончится срок действия вашего ключа.\n\n'
            'Продлите подписку, чтобы сохранить доступ к VPN без перерыва!'
        )
        notification_data = get_message_data('notification_text', default_notification)
        notification_text = notification_data.get('text', default_notification)
        notification_media = notification_data.get('media_file_id')
        notification_media_type = notification_data.get('media_type')
        
        expiring_keys = get_expiring_keys(days)
        sent_count = 0
        
        for key_info in expiring_keys:
            vpn_key_id = key_info['vpn_key_id']
            user_telegram_id = key_info['user_telegram_id']
            days_left = key_info['days_left']
            keyname = key_info.get('custom_name', f"Key #{vpn_key_id}")
            
            # Checking if we sent today
            if is_notification_sent_today(vpn_key_id):
                continue
            
            event_context = build_user_event_context(user_telegram_id)
            event_context.update({
                'key_name': keyname,
                'key_days_left': days_left,
            })
            text = render_event_placeholders(
                notification_text,
                'key_expiring',
                event_context,
                mode='html',
            )
            
            # Keyboard with "My keys" and "Home" buttons
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"))
            builder.row(InlineKeyboardButton(text="🈴 На главную", callback_data="start"))
            kb = builder.as_markup()
            
            try:
                await send_media_or_text(
                    bot,
                    chat_id=user_telegram_id,
                    text=text,
                    media=notification_media,
                    media_type=notification_media_type,
                    reply_markup=kb,
                )
                log_notification_sent(vpn_key_id)
                sent_count += 1
            except Exception as e:
                if is_bot_blocked_error(e):
                    mark_user_bot_blocked(user_telegram_id)
                    logger.info(f"Пользователь {user_telegram_id} помечен как заблокировавший бота")
                else:
                    logger.warning(f"Не удалось отправить уведомление пользователю {user_telegram_id}: {e}")
            
            # Slight delay between messages
            await asyncio.sleep(0.3)
        
        if sent_count > 0:
            logger.info(f"📬 Отправлено {sent_count} уведомлений об истечении ключей")
        else:
            logger.info("Нет ключей требующих уведомления")
    
    except Exception as e:
        logger.error(f"Ошибка в check_and_send_expiry_notifications: {e}")


def get_seconds_until(target_hour: int, target_minute: int = 0) -> int:
    """
    Calculates the number of seconds until the specified time of day.
    
    Args:
        target_hour: Target hour (0-23)
        target_minute: Target minute (0-59)
    
    Returns:
        Number of seconds until target time
    """
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    # If time has already passed today, we plan for tomorrow
    if target <= now:
        target += timedelta(days=1)
    
    return int((target - now).total_seconds())


async def run_daily_tasks(bot: Bot) -> None:
    """
    Background task for running daily tasks.
    
    Schedule:
    - 03:00 — Daily statistics
    - 03:05 — Archive with backups
    
    Args:
        bot: Bot instance
    """
    logger.info("🕐 Планировщик ежедневных задач запущен")
    
    while True:
        try:
            # Read the time from the settings or use the default 03:00
            time_str = get_setting('daily_tasks_time', '03:00')
            try:
                target_hour, target_minute = map(int, time_str.split(':'))
            except Exception as e:
                logger.error(f"Некорректный формат настройки daily_tasks_time '{time_str}': {e}. Используем 03:00")
                target_hour, target_minute = 3, 0

            # We wait until the specified time
            seconds_to_wait = get_seconds_until(target_hour, target_minute)
            logger.info(f"Следующий запуск задач ({time_str}) через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
            await asyncio.sleep(seconds_to_wait)
            
            # Sending statistics
            logger.info("📊 Запуск отправки суточной статистики...")
            await send_daily_stats(bot)
            
            # Wait 5 minutes
            await asyncio.sleep(300)
            
            # Sending a backup
            logger.info("📦 Запуск создания и отправки бэкапа...")
            await send_backup_archive(bot)
            
            # Wait 5 minutes
            await asyncio.sleep(300)
            
            # Sending notifications to users
            await check_and_send_expiry_notifications(bot)
            
            # Monthly traffic reset (1st day of every month)
            if datetime.now().day == 1:
                await monthly_traffic_reset(bot)
            
            # We wait a little so as not to start again at the same minute
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            logger.info("Планировщик ежедневных задач остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике ежедневных задач: {e}")
            # We wait an hour and try again
            await asyncio.sleep(3600)


async def check_and_notify_updates(bot: Bot) -> None:
    """
    Checks for updates and notifies administrators if there are any.
    
    Args:
        bot: Bot instance
    """
    if not is_update_notifications_enabled():
        logger.info("🔕 Уведомления о новых версиях отключены, фоновая проверка пропущена")
        return

    logger.info("🔍 Ежедневная проверка обновлений...")
    
    # Checking if GitHub URL is configured
    if not GITHUB_REPO_URL:
        logger.warning("GitHub URL не настроен, пропускаем проверку обновлений")
        return

    # Checking the unlock conditions
    try_unblock()

    if is_update_blocked():
        logger.info("🔒 Обновления заблокированы, отправляем уведомление")
        msg = get_blocked_message()
        # OK button to close the notification
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ OK", callback_data="dismiss_msg"))
        kb = builder.as_markup()
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=msg,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление о блокировке админу {admin_id}: {e}")
        return
        
    try:
        # Checking for updates
        success, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only = check_for_updates()
        
        if success and commits_behind > 0:
            if is_beta_only:
                logger.info(f"📦 Найдено {commits_behind} новых коммитов, но все они бета-версии (начинаются с '?'). Уведомление не отправляется.")
                return
                
            logger.info(f"📦 Найдено {commits_behind} новых коммитов")
            
            # Update button (same callback_data as in the admin panel)
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔄 Обновить бота", 
                    callback_data="admin_update_bot"
                )
            )
            
            kb = builder.as_markup()
            
            # Generating the notification text
            notify_text = f"📦 <b>Доступно обновление!</b>\n\n{log_text}"
            
            # If there is a blocking commit, add a warning
            if has_blocking and blocking_commit:
                blocking_msg = blocking_commit['message'].lstrip('!')
                notify_text += f"\n\n⚠️ Среди обновлений есть <b>блокирующий коммит</b>.\n<code>{blocking_msg}</code>"
            
            # Sending notifications to admins
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=notify_text,
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление об обновлении админу {admin_id}: {e}")
        else:
            logger.info("✅ Обновлений не найдено")
            
    except Exception as e:
        logger.error(f"Ошибка при проверке обновлений: {e}")


async def run_update_check_scheduler(bot: Bot) -> None:
    """
    Background task for checking updates daily.
    
    Schedule:
    - 12:00 — Checking for updates
    
    Args:
        bot: Bot instance
    """
    logger.info("🕐 Планировщик обновлений запущен")
    
    while True:
        try:
            # Read the time from the settings or use the default 12:00
            time_str = get_setting('update_check_time', '12:00')
            try:
                target_hour, target_minute = map(int, time_str.split(':'))
            except Exception as e:
                logger.error(f"Некорректный формат настройки update_check_time '{time_str}': {e}. Используем 12:00")
                target_hour, target_minute = 12, 0

            # We wait until the specified time
            seconds_to_wait = get_seconds_until(target_hour, target_minute)
            logger.info(f"Следующая проверка обновлений ({time_str}) через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
            await asyncio.sleep(seconds_to_wait)
            
            # Checking for updates
            await check_and_notify_updates(bot)
            
            # We wait 5 minutes so as not to start again
            await asyncio.sleep(300)
            
        except asyncio.CancelledError:
            logger.info("Планировщик обновлений остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике обновлений: {e}")
            # We wait an hour and try again
            await asyncio.sleep(3600)


# ============================================================================
# TRAFFIC SYNCHRONIZATION (every 5 minutes)
# ============================================================================

# Traffic notification thresholds (% of remaining traffic)
TRAFFIC_THRESHOLDS = [10, 5, 3, 2, 1, 0]


async def monthly_traffic_reset(bot: Bot) -> None:
    """
    Monthly tasks (1st day of each month):
    
    1. Reset traffic (if monthly_traffic_reset_enabled = 1)
    2. Reconciliation of the database and the panel (ALWAYS) - correction of discrepancies between expiryTime and totalGB
    
    Args:
        bot: Bot instance
    """
    from database.requests import (
        get_all_active_keys_with_server,
        reset_key_traffic_notification,
        update_key_traffic_limit,
        get_tariff_by_id
    )
    from bot.services.vpn_api import sync_key_to_panel_state
    
    reset_enabled = get_setting('monthly_traffic_reset_enabled', '0') == '1'
    
    # === PART 1: Traffic reset (if enabled) ===
    reset_success = 0
    reset_errors = 0
    
    if reset_enabled:
        logger.info("🔄 Запуск ежемесячного сброса трафика...")
        keys = get_all_active_keys_with_server()
        keys_with_limit = [k for k in keys if (k.get('traffic_limit', 0) or 0) > 0] if keys else []
        
        for key in keys_with_limit:
            try:
                tariff_limit = key.get('traffic_limit', 0) or 0
                tariff_id = key.get('tariff_id')
                if tariff_id:
                    tariff = get_tariff_by_id(tariff_id)
                    if tariff and (tariff.get('traffic_limit_gb', 0) or 0) > 0:
                        tariff_limit = tariff['traffic_limit_gb'] * (1024**3)
                
                # Updating the database
                update_key_traffic_limit(key['id'], tariff_limit)
                reset_key_traffic_notification(key['id'])
                
                # Push to the panel (up/down reset + correct data from the database)
                await sync_key_to_panel_state(key['id'], reset_traffic=True)
                reset_success += 1
            except Exception as e:
                reset_errors += 1
                logger.error(f"Ошибка сброса трафика для ключа {key['id']}: {e}")
    else:
        logger.info("🔄 Ежемесячный сброс трафика отключён")
    
    # === PART 2: Database reconciliation↔panel (ALWAYS) ===
    logger.info("🔍 Запуск ежемесячной сверки БД↔панель...")
    sync_fixed = 0
    sync_errors = 0
    
    all_keys = get_all_active_keys_with_server()
    if all_keys:
        keys_by_server: dict = {}
        for key in all_keys:
            sid = key['server_id']
            if sid not in keys_by_server:
                keys_by_server[sid] = []
            keys_by_server[sid].append(key)
        
        servers = get_all_servers()
        server_map = {s['id']: s for s in servers}
        
        for server_id, server_keys in keys_by_server.items():
            server = server_map.get(server_id)
            if not server or not server.get('is_active'):
                continue
            try:
                client = get_client_from_server_data(server)
                inbounds = await client.get_inbounds()
                
                # Email card → data on the panel
                panel_map = {}
                for inbound in inbounds:
                    settings = json.loads(inbound.get('settings', '{}'))
                    for cl in settings.get('clients', []):
                        panel_map[cl.get('email', '')] = {
                            'expiryTime': cl.get('expiryTime', 0),
                            'totalGB': cl.get('totalGB', 0)
                        }
                
                for key in server_keys:
                    email = key.get('panel_email')
                    if not email or email not in panel_map:
                        continue
                    
                    panel = panel_map[email]
                    needs_fix = False
                    
                    # Check expiryTime
                    expires_at = key.get('expires_at')
                    panel_ms = panel['expiryTime']
                    if expires_at:
                        dt = datetime.fromisoformat(str(expires_at))
                        expected_ms = int(dt.timestamp() * 1000)
                        
                        # Discrepancy > 1 day
                        if panel_ms > 0 and abs(expected_ms - panel_ms) > 86400 * 1000:
                            needs_fix = True
                        elif panel_ms == 0 and expected_ms > 0:
                            needs_fix = True
                    else:
                        expected_ms = 0
                        if panel_ms > 0:
                            needs_fix = True
                    
                    # Checking totalGB
                    traffic_limit = key.get('traffic_limit', 0) or 0
                    panel_total = panel['totalGB']
                    if traffic_limit > 0:
                        snapshot = await get_key_traffic_snapshot(client, key, inbounds)
                        panel_used = snapshot.get('panel_traffic_used', 0) if snapshot else 0
                        panel_total = snapshot.get('totalGB', panel_total) if snapshot else panel_total
                        expected_total = calculate_panel_total_for_key(key, panel_used)
                        if panel_total == 0 or abs(panel_total - expected_total) > 1024**3:
                            needs_fix = True
                    elif traffic_limit == 0 and panel_total > 0:
                        needs_fix = True
                    
                    if needs_fix:
                        # We skip those that have already been updated when the traffic is reset
                        already_pushed = reset_enabled and (traffic_limit > 0)
                        if not already_pushed:
                            try:
                                await sync_key_to_panel_state(key['id'])
                                sync_fixed += 1
                            except Exception as e:
                                sync_errors += 1
                                logger.error(f"Ошибка сверки ключа {key['id']} ({email}): {e}")
                        else:
                            sync_fixed += 1  # Already fixed on reset
            except Exception as e:
                logger.error(f"Ошибка сверки сервера {server.get('name', server_id)}: {e}")
    
    # ===Report to admins ===
    report_parts = ["🔄 <b>Ежемесячное обслуживание</b>\n"]
    if reset_enabled:
        report_parts.append(f"📊 <b>Сброс трафика:</b> ✅ {reset_success}")
        if reset_errors > 0:
            report_parts.append(f"  ❌ Ошибок: {reset_errors}")
    report_parts.append(f"🔍 <b>Сверка БД↔панель:</b> 🔧 {sync_fixed}")
    if sync_errors > 0:
        report_parts.append(f"  ❌ Ошибок: {sync_errors}")
    
    report = "\n".join(report_parts)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report,
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить отчёт админу {admin_id}: {e}")

async def sync_traffic_stats(bot: Bot) -> None:
    """
    Queries all servers and updates the traffic cache for each key.
    Checks notification thresholds and sends notifications to users.
    
    Graceful degradation: if the server is unavailable, log WARNING,
    We do not reset the traffic, we continue processing the remaining servers.
    """
    from database.requests import (
        get_all_active_keys_with_server, bulk_update_traffic,
        update_key_notified_pct, get_setting
    )
    
    keys = get_all_active_keys_with_server()
    if not keys:
        return
    
    # Grouping keys by server
    keys_by_server: dict = {}
    for key in keys:
        sid = key['server_id']
        if sid not in keys_by_server:
            keys_by_server[sid] = []
        keys_by_server[sid].append(key)
    
    # Getting servers
    servers = get_all_servers()
    server_map = {s['id']: s for s in servers}
    
    # Collecting traffic updates
    traffic_updates = []  # (traffic_used, key_id)
    
    for server_id, server_keys in keys_by_server.items():
        server = server_map.get(server_id)
        if not server or not server.get('is_active'):
            continue
        
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            for key in server_keys:
                snapshot = await get_key_traffic_snapshot(client, key, inbounds)
                if snapshot:
                    traffic_used = snapshot['traffic_used']

                    traffic_updates.append((traffic_used, key['id']))
                    key['_new_traffic_used'] = traffic_used

        except Exception as e:
            # Graceful degradation: don’t touch the data, continue
            logger.warning(f"⚠️ Синхронизация трафика: сервер {server.get('name', server_id)} недоступен: {e}")
            continue
    
    # Mass update of traffic in the database
    if traffic_updates:
        bulk_update_traffic(traffic_updates)
    
    # Checking notification thresholds
    notification_text_template = get_setting(
        'traffic_notification_text',
        '⚠️ По ключу <b>%ключ_имя%</b> осталось %ключ_трафик_процент_остатка%% трафика (%ключ_трафик_использовано% из %ключ_трафик_лимит%)'
    )
    
    for key in keys:
        traffic_limit = key.get('traffic_limit', 0) or 0
        if traffic_limit == 0:
            continue  # Unlimited - skip it
        
        # We use the updated value or from the database
        traffic_used = key.get('_new_traffic_used', key.get('traffic_used', 0) or 0)
        notified_pct = key.get('traffic_notified_pct', 100)
        
        # Calculate the remaining percentage
        remaining_pct = max(0, (1 - traffic_used / traffic_limit) * 100)
        
        # Checking the thresholds
        for threshold in TRAFFIC_THRESHOLDS:
            if remaining_pct <= threshold and notified_pct > threshold:
                # Sending a notification
                telegram_id = key.get('telegram_id')
                if telegram_id:
                    # Forming the key name
                    if key.get('custom_name'):
                        keyname = key['custom_name']
                    elif key.get('client_uuid'):
                        uuid = key['client_uuid']
                        keyname = f"{uuid[:4]}...{uuid[-4:]}" if len(uuid) >= 8 else uuid
                    else:
                        keyname = f"Ключ #{key['id']}"
                    
                    from bot.utils.event_placeholders import build_user_event_context, render_event_placeholders

                    event_context = build_user_event_context(int(telegram_id))
                    event_context.update(
                        {
                            'key_name': keyname,
                            'key_traffic_remaining_percent': threshold,
                            'key_traffic_used_text': format_traffic(traffic_used),
                            'key_traffic_limit_text': format_traffic(traffic_limit),
                        }
                    )
                    msg = render_event_placeholders(
                        notification_text_template,
                        'key_traffic_low',
                        event_context,
                        mode='html',
                    )
                    
                    try:
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=msg,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление о трафике пользователю {telegram_id}: {e}")
                
                # Update the threshold in the database
                update_key_notified_pct(key['id'], threshold)
                key['traffic_notified_pct'] = threshold
                break  # Only one notification at a time
    
    # For subscription keys: if according to our traffic counter the traffic is exhausted or
    # the key has expired - we disconnect ALL clients with this email on the server immediately.
    # totalGB on individual inbounds is the same, but clients will not disconnect themselves
    # until their own counter reaches the limit, so we do it manually.
    from database.db_keys import is_key_active, is_traffic_exhausted
    from bot.services.vpn_api import ensure_subscription_keys_on_server, is_subscription_mode

    sub_mode_active = is_subscription_mode()
    for key in keys:
        if not key.get('sub_id'):
            continue
        # Replace traffic_used with a fresh value for verification
        merged = dict(key)
        if '_new_traffic_used' in key:
            merged['traffic_used'] = key['_new_traffic_used']
        if is_traffic_exhausted(merged) or not is_key_active(merged):
            try:
                await ensure_subscription_keys_on_server(key['id'])
            except Exception as e:
                logger.warning(
                    f"sync_traffic_stats: ensure_subscription_keys для key {key['id']} "
                    f"при истечении не удался: {e}"
                )

    logger.debug(f"Синхронизация трафика завершена: обновлено {len(traffic_updates)} ключей")


async def materialize_subscription_state() -> None:
    """
    Full pass through all active keys with challenge
    ensure_subscription_keys_on_server() - finishes missing clients
    in subscription mode and deletes unnecessary ones in keys mode.

    Runs once every ~30 minutes (every 6 traffic-sync cycles).
    """
    from database.requests import get_all_active_keys_with_server
    from bot.services.vpn_api import ensure_subscription_keys_on_server

    keys = get_all_active_keys_with_server()
    if not keys:
        return

    logger.info(f"🔁 materialize_subscription_state: проход по {len(keys)} ключам")
    stats_total = {'created': 0, 'deleted': 0, 'enabled': 0, 'disabled': 0}
    for key in keys:
        try:
            res = await ensure_subscription_keys_on_server(key['id'])
            for k, v in res.items():
                stats_total[k] = stats_total.get(k, 0) + v
        except Exception as e:
            logger.warning(
                f"materialize_subscription_state: ключ {key['id']} не обработан: {e}"
            )
    if any(stats_total.values()):
        logger.info(f"🔁 materialize_subscription_state завершён: {stats_total}")


async def run_traffic_sync_scheduler(bot: Bot) -> None:
    """
    Background task to synchronize traffic every 5 minutes.
    Every 6 cycles (≈30 min) additionally causes
    materialize_subscription_state() to fit clients on panels
    under the current bot_mode.

    Args:
        bot: Bot instance
    """
    logger.info("📊 Планировщик синхронизации трафика запущен (каждые 5 мин, materialize каждые 30 мин)")

    # First launch 30 seconds after the bot starts
    await asyncio.sleep(30)

    cycle = 0
    while True:
        try:
            await sync_traffic_stats(bot)
            try:
                from bot.services.key_lifecycle import process_expired_key_lifecycle_events

                await process_expired_key_lifecycle_events()
            except Exception as e:
                logger.error(f"Ошибка обработки key_expired lifecycle events: {e}")
            try:
                from bot.services.custom_payments import auto_check_custom_payment_orders

                payment_summary = await auto_check_custom_payment_orders(bot=bot, limit=25)
                if (
                    payment_summary.get('checked')
                    or payment_summary.get('completed')
                    or payment_summary.get('canceled')
                    or payment_summary.get('errors')
                ):
                    logger.info("Custom payment auto-check: %s", payment_summary)
            except Exception as e:
                logger.error(f"Ошибка автопроверки custom payment providers: {e}")
            cycle += 1
            # Once every 6 cycles (≈30 min) - materialization of the subscription state
            if cycle % 6 == 0:
                try:
                    await materialize_subscription_state()
                except Exception as e:
                    logger.error(f"Ошибка в materialize_subscription_state: {e}")

            # Wait 5 minutes
            await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Планировщик синхронизации трафика остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике синхронизации трафика: {e}")
            # Wait 2 minutes and try again
            await asyncio.sleep(120)
