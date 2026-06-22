"""
Модуль для автоматических задач.

Включает:
- Отправку суточной статистики администраторам
- Создание и отправку архива с бэкапами (БД бота + VPN панелей)
- Синхронизацию трафика с VPN-серверами (каждые 5 минут)
- Уведомления о заканчивающемся трафике
"""

import asyncio
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, time as dt_time, timedelta
from io import BytesIO
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile, ReplyKeyboardRemove

from config import ADMIN_IDS, GITHUB_REPO_URL
from database.requests import (
    get_all_servers, get_users_stats, get_keys_stats,
    get_daily_payments_stats, get_new_users_count_today,
    get_setting, get_expiring_keys, is_notification_sent_today, log_notification_sent,
    is_update_notifications_enabled, mark_user_bot_blocked
)
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic
from bot.utils.git_utils import check_for_updates
from bot.utils.update_block import is_update_blocked, get_blocked_message, try_unblock
from bot.utils.delivery import is_bot_blocked_error
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Путь к базе данных бота
BOT_DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'vpn_bot.db')

# Корневая папка проекта и папка для локальных бэкапов
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKUP_DIR = os.path.join(PROJECT_ROOT, 'backup')

# Сколько дней хранить локальные бэкапы
BACKUP_RETENTION_DAYS = 7


async def collect_daily_stats() -> str:
    """
    Собирает суточную статистику для отчёта.
    
    Returns:
        Форматированный текст статистики
    """
    # Статистика пользователей
    users = get_users_stats()
    new_users = get_new_users_count_today()
    
    # Статистика ключей
    keys = get_keys_stats()
    
    # Статистика платежей
    payments = get_daily_payments_stats()
    
    # Статистика серверов
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
    
    # Формируем текст отчёта
    today = datetime.now().strftime("%d.%m.%Y")
    
    # Платежи
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
    Отправляет суточную статистику всем администраторам.
    
    Args:
        bot: Экземпляр бота
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


async def create_backup_archive() -> Optional[bytes]:
    """
    Создаёт ZIP-архив с бэкапами.
    
    Включает:
    - vpn_bot.db — база данных бота
    - server_NAME_x-ui.db — база каждого VPN-сервера
    
    Returns:
        Байты ZIP-архива или None при ошибке
    """
    try:
        archive_buffer = BytesIO()
        
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Добавляем базу данных бота
            bot_db_path = os.path.abspath(BOT_DB_PATH)
            if os.path.exists(bot_db_path):
                zf.write(bot_db_path, 'vpn_bot.db')
                logger.info(f"Добавлен в архив: vpn_bot.db ({os.path.getsize(bot_db_path)} байт)")
            else:
                logger.warning(f"База данных бота не найдена: {bot_db_path}")
            
            # Скачиваем и добавляем бэкапы VPN-серверов
            servers = get_all_servers()
            for server in servers:
                if not server.get('is_active'):
                    continue
                    
                try:
                    client = get_client_from_server_data(server)
                    backup_data = await client.get_database_backup()
                    
                    # Имя файла: server_НАЗВАНИЕ_x-ui.db
                    safe_name = server['name'].replace(' ', '_').replace('/', '_')
                    filename = f"server_{safe_name}_x-ui.db"
                    
                    zf.writestr(filename, backup_data)
                    logger.info(f"Добавлен в архив: {filename} ({len(backup_data)} байт)")
                    
                except VPNAPIError as e:
                    logger.warning(f"Не удалось скачать бэкап сервера {server['name']}: {e}")
                except Exception as e:
                    logger.error(f"Ошибка при скачивании бэкапа сервера {server['name']}: {e}")
        
        archive_buffer.seek(0)
        return archive_buffer.read()
        
    except Exception as e:
        logger.error(f"Ошибка при создании архива бэкапов: {e}")
        return None


async def save_local_backup() -> None:
    """
    Сохраняет локальные копии всех баз данных в папку backup/YYYY-MM-DD/.
    
    Файлы хранятся неархивированными (.db) для прямого доступа
    через sqlite3 из Python без необходимости распаковки.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(BACKUP_DIR, today)
    
    try:
        os.makedirs(day_dir, exist_ok=True)
        
        # Сохраняем базу данных бота
        bot_db_path = os.path.abspath(BOT_DB_PATH)
        if os.path.exists(bot_db_path):
            dest = os.path.join(day_dir, 'vpn_bot.db')
            shutil.copy2(bot_db_path, dest)
            logger.info(f"Локальный бэкап: vpn_bot.db ({os.path.getsize(dest)} байт)")
        else:
            logger.warning(f"База данных бота не найдена: {bot_db_path}")
        
        # Скачиваем базы VPN-серверов
        servers = get_all_servers()
        for server in servers:
            if not server.get('is_active'):
                continue
            
            try:
                client = get_client_from_server_data(server)
                backup_data = await client.get_database_backup()
                
                safe_name = server['name'].replace(' ', '_').replace('/', '_')
                filename = f"server_{safe_name}_x-ui.db"
                dest = os.path.join(day_dir, filename)
                
                with open(dest, 'wb') as f:
                    f.write(backup_data)
                
                logger.info(f"Локальный бэкап: {filename} ({len(backup_data)} байт)")
                
            except VPNAPIError as e:
                logger.warning(f"Не удалось скачать бэкап сервера {server['name']}: {e}")
            except Exception as e:
                logger.error(f"Ошибка при скачивании бэкапа сервера {server['name']}: {e}")
        
        logger.info(f"✅ Локальные бэкапы сохранены в {day_dir}")
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении локальных бэкапов: {e}")


def cleanup_old_backups() -> None:
    """
    Рекурсивно удаляет любые файлы и ссылки старше срока хранения.

    Имена и расширения не учитываются: каталог backup предназначен только
    для временных резервных копий. Символические ссылки не обходятся.
    После удаления файлов очищаются опустевшие подпапки.
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
    Создаёт и отправляет архив бэкапов всем администраторам.
    Также сохраняет локальные копии и чистит старые бэкапы.
    
    Args:
        bot: Экземпляр бота
    """
    try:
        # Сохраняем локальные бэкапы (неархивированные .db файлы)
        await save_local_backup()
        
        # Удаляем бэкапы старше 7 дней
        cleanup_old_backups()
        
        # Создаём ZIP-архив для отправки в Telegram
        archive_data = await create_backup_archive()
        
        if not archive_data:
            logger.error("Не удалось создать архив бэкапов")
            return
        
        # Имя файла с датой
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"backup_{today}.zip"
        
        # Отправляем админам
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=BufferedInputFile(archive_data, filename=filename),
                    caption=f"📦 <b>Ежедневный бэкап за {today}</b>\n\nСодержит базы данных бота и VPN-серверов.",
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
    Проверяет и отправляет уведомления об истекающих ключах.
    
    Использует единый HTML-контракт. Динамические подстановки 
    экранируются через escape_html().
    """
    logger.info("⏳ Запуск проверки истекающих ключей...")
    try:
        from bot.utils.placeholders import apply_placeholder_replacements
        from bot.utils.text import escape_html, send_media_or_text
        days = int(get_setting('notification_days', '3'))
        from bot.utils.message_editor import get_message_data
        
        # Дефолтный текст в HTML
        default_notification = (
            '⚠️ <b>Ваш VPN-ключ %имяключа% скоро истекает!</b>\n\n'
            'Через %дней% дней закончится срок действия вашего ключа.\n\n'
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
            
            # Проверяем, отправляли ли мы сегодня
            if is_notification_sent_today(vpn_key_id):
                continue
            
            # Подстановка с экранированием динамических значений
            text = apply_placeholder_replacements(notification_text, {
                '%дней%': escape_html(str(days_left)),
                '%имяключа%': escape_html(str(keyname)),
            })
            
            # Клавиатура с кнопками "Мои ключи" и "На главную"
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
            
            # Небольшая задержка между сообщениями
            await asyncio.sleep(0.3)
        
        if sent_count > 0:
            logger.info(f"📬 Отправлено {sent_count} уведомлений об истечении ключей")
        else:
            logger.info("Нет ключей требующих уведомления")
    
    except Exception as e:
        logger.error(f"Ошибка в check_and_send_expiry_notifications: {e}")


def get_seconds_until(target_hour: int, target_minute: int = 0) -> int:
    """
    Вычисляет количество секунд до указанного времени суток.
    
    Args:
        target_hour: Целевой час (0-23)
        target_minute: Целевая минута (0-59)
    
    Returns:
        Количество секунд до целевого времени
    """
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    # Если время уже прошло сегодня, планируем на завтра
    if target <= now:
        target += timedelta(days=1)
    
    return int((target - now).total_seconds())


async def run_daily_tasks(bot: Bot) -> None:
    """
    Фоновая задача для запуска ежедневных заданий.
    
    Расписание:
    - 03:00 — Суточная статистика
    - 03:05 — Архив с бэкапами
    
    Args:
        bot: Экземпляр бота
    """
    logger.info("🕐 Планировщик ежедневных задач запущен")
    
    while True:
        try:
            # Читаем время из настроек или используем по умолчанию 03:00
            time_str = get_setting('daily_tasks_time', '03:00')
            try:
                target_hour, target_minute = map(int, time_str.split(':'))
            except Exception as e:
                logger.error(f"Некорректный формат настройки daily_tasks_time '{time_str}': {e}. Используем 03:00")
                target_hour, target_minute = 3, 0

            # Ждём до заданного времени
            seconds_to_wait = get_seconds_until(target_hour, target_minute)
            logger.info(f"Следующий запуск задач ({time_str}) через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
            await asyncio.sleep(seconds_to_wait)
            
            # Отправляем статистику
            logger.info("📊 Запуск отправки суточной статистики...")
            await send_daily_stats(bot)
            
            # Ждём 5 минут
            await asyncio.sleep(300)
            
            # Отправляем бэкап
            logger.info("📦 Запуск создания и отправки бэкапа...")
            await send_backup_archive(bot)
            
            # Ждём 5 минут
            await asyncio.sleep(300)
            
            # Отправляем уведомления пользователям
            await check_and_send_expiry_notifications(bot)
            
            # Ежемесячный сброс трафика (1-е число каждого месяца)
            if datetime.now().day == 1:
                await monthly_traffic_reset(bot)
            
            # Ждём немного чтобы не запуститься повторно в ту же минуту
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            logger.info("Планировщик ежедневных задач остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике ежедневных задач: {e}")
            # Ждём час и пробуем снова
            await asyncio.sleep(3600)


async def check_and_notify_updates(bot: Bot) -> None:
    """
    Проверяет обновления и уведомляет администраторов, если они есть.
    
    Args:
        bot: Экземпляр бота
    """
    if not is_update_notifications_enabled():
        logger.info("🔕 Уведомления о новых версиях отключены, фоновая проверка пропущена")
        return

    logger.info("🔍 Ежедневная проверка обновлений...")
    
    # Проверяем настроен ли GitHub URL
    if not GITHUB_REPO_URL:
        logger.warning("GitHub URL не настроен, пропускаем проверку обновлений")
        return

    # Проверяем условия разблокировки
    try_unblock()

    if is_update_blocked():
        logger.info("🔒 Обновления заблокированы, отправляем уведомление")
        msg = get_blocked_message()
        # Кнопка OK для закрытия уведомления
        from aiogram.types import InlineKeyboardButton
        from aiogram.utils.keyboard import InlineKeyboardBuilder
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
        # Проверяем обновления
        success, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only = check_for_updates()
        
        if success and commits_behind > 0:
            if is_beta_only:
                logger.info(f"📦 Найдено {commits_behind} новых коммитов, но все они бета-версии (начинаются с '?'). Уведомление не отправляется.")
                return
                
            logger.info(f"📦 Найдено {commits_behind} новых коммитов")
            
            # Кнопка обновления (та же callback_data, что в админке)
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔄 Обновить бота", 
                    callback_data="admin_update_bot"
                )
            )
            
            kb = builder.as_markup()
            
            # Формируем текст уведомления
            notify_text = f"📦 <b>Доступно обновление!</b>\n\n{log_text}"
            
            # Если есть блокирующий коммит — добавляем предупреждение
            if has_blocking and blocking_commit:
                blocking_msg = blocking_commit['message'].lstrip('!')
                notify_text += f"\n\n⚠️ Среди обновлений есть <b>блокирующий коммит</b>.\n<code>{blocking_msg}</code>"
            
            # Отправляем уведомления админам
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
    Фоновая задача для ежедневной проверки обновлений.
    
    Расписание:
    - 12:00 — Проверка обновлений
    
    Args:
        bot: Экземпляр бота
    """
    logger.info("🕐 Планировщик обновлений запущен")
    
    while True:
        try:
            # Читаем время из настроек или используем по умолчанию 12:00
            time_str = get_setting('update_check_time', '12:00')
            try:
                target_hour, target_minute = map(int, time_str.split(':'))
            except Exception as e:
                logger.error(f"Некорректный формат настройки update_check_time '{time_str}': {e}. Используем 12:00")
                target_hour, target_minute = 12, 0

            # Ждём до заданного времени
            seconds_to_wait = get_seconds_until(target_hour, target_minute)
            logger.info(f"Следующая проверка обновлений ({time_str}) через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
            await asyncio.sleep(seconds_to_wait)
            
            # Проверяем обновления
            await check_and_notify_updates(bot)
            
            # Ждём 5 минут чтобы не запуститься повторно
            await asyncio.sleep(300)
            
        except asyncio.CancelledError:
            logger.info("Планировщик обновлений остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике обновлений: {e}")
            # Ждём час и пробуем снова
            await asyncio.sleep(3600)


# ============================================================================
# СИНХРОНИЗАЦИЯ ТРАФИКА (каждые 5 минут)
# ============================================================================

# Пороги уведомлений о трафике (% оставшегося трафика)
TRAFFIC_THRESHOLDS = [10, 5, 3, 2, 1, 0]


async def monthly_traffic_reset(bot: Bot) -> None:
    """
    Ежемесячные задачи (1-е число каждого месяца):
    
    1. Сброс трафика (если monthly_traffic_reset_enabled = 1)
    2. Сверка БД и панели (ВСЕГДА) — исправление расхождений expiryTime и totalGB
    
    Args:
        bot: Экземпляр бота
    """
    from database.requests import (
        get_all_active_keys_with_server,
        reset_key_traffic_notification,
        update_key_traffic_limit,
        get_tariff_by_id
    )
    from bot.services.vpn_api import sync_key_to_panel_state
    
    reset_enabled = get_setting('monthly_traffic_reset_enabled', '0') == '1'
    
    # === ЧАСТЬ 1: Сброс трафика (если включён) ===
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
                
                # Обновляем БД
                update_key_traffic_limit(key['id'], tariff_limit)
                reset_key_traffic_notification(key['id'])
                
                # Пушим на панель (сброс up/down + правильные данные из БД)
                await sync_key_to_panel_state(key['id'], reset_traffic=True)
                reset_success += 1
            except Exception as e:
                reset_errors += 1
                logger.error(f"Ошибка сброса трафика для ключа {key['id']}: {e}")
    else:
        logger.info("🔄 Ежемесячный сброс трафика отключён")
    
    # === ЧАСТЬ 2: Сверка БД↔панель (ВСЕГДА) ===
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
                
                # Карта email → данные на панели
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
                    
                    # Проверяем expiryTime
                    expires_at = key.get('expires_at')
                    panel_ms = panel['expiryTime']
                    if expires_at:
                        dt = datetime.fromisoformat(str(expires_at))
                        expected_ms = int(dt.timestamp() * 1000)
                        
                        # Расхождение > 1 день
                        if panel_ms > 0 and abs(expected_ms - panel_ms) > 86400 * 1000:
                            needs_fix = True
                        elif panel_ms == 0 and expected_ms > 0:
                            needs_fix = True
                    else:
                        expected_ms = 0
                        if panel_ms > 0:
                            needs_fix = True
                    
                    # Проверяем totalGB
                    traffic_limit = key.get('traffic_limit', 0) or 0
                    panel_total = panel['totalGB']
                    if traffic_limit > 0 and (panel_total == 0 or abs(panel_total - traffic_limit) > 1024**3):
                        needs_fix = True
                    elif traffic_limit == 0 and panel_total > 0:
                        needs_fix = True
                    
                    if needs_fix:
                        # Пропускаем те, что уже обновились при сбросе трафика
                        already_pushed = reset_enabled and (traffic_limit > 0)
                        if not already_pushed:
                            try:
                                await sync_key_to_panel_state(key['id'])
                                sync_fixed += 1
                            except Exception as e:
                                sync_errors += 1
                                logger.error(f"Ошибка сверки ключа {key['id']} ({email}): {e}")
                        else:
                            sync_fixed += 1  # Уже исправлен при сбросе
            except Exception as e:
                logger.error(f"Ошибка сверки сервера {server.get('name', server_id)}: {e}")
    
    # === Отчёт админам ===
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
    Опрашивает все серверы и обновляет кеш трафика для каждого ключа.
    Проверяет пороги уведомлений и отправляет уведомления пользователям.
    
    Graceful degradation: при недоступности сервера — логируем WARNING,
    не обнуляем трафик, продолжаем обработку остальных серверов.
    """
    from database.requests import (
        get_all_active_keys_with_server, bulk_update_traffic,
        update_key_notified_pct, get_setting
    )
    
    keys = get_all_active_keys_with_server()
    if not keys:
        return
    
    # Группируем ключи по серверам
    keys_by_server: dict = {}
    for key in keys:
        sid = key['server_id']
        if sid not in keys_by_server:
            keys_by_server[sid] = []
        keys_by_server[sid].append(key)
    
    # Получаем серверы
    servers = get_all_servers()
    server_map = {s['id']: s for s in servers}
    
    # Собираем обновления трафика
    traffic_updates = []  # (traffic_used, key_id)
    
    for server_id, server_keys in keys_by_server.items():
        server = server_map.get(server_id)
        if not server or not server.get('is_active'):
            continue
        
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            # Строим словарь email -> агрегированные {total, up, down} по всем inbound
            # В режиме subscription один клиент существует во всех inbound с одним
            # email и subId — агрегация даёт реальный суммарный расход.
            stats_map = {}
            for inbound in inbounds:
                for stats in inbound.get("clientStats", []):
                    email = stats.get("email")
                    if not email:
                        continue
                    if email not in stats_map:
                        stats_map[email] = {'total': 0, 'up': 0, 'down': 0}
                    stats_map[email]['up'] += stats.get('up', 0) or 0
                    stats_map[email]['down'] += stats.get('down', 0) or 0
                    # totalGB одинаковый на каждом inbound — берём максимум
                    stats_map[email]['total'] = max(
                        stats_map[email]['total'],
                        stats.get('total', 0) or 0,
                    )

            # Сопоставляем с ключами
            for key in server_keys:
                email = key.get('panel_email')
                if email and email in stats_map:
                    s = stats_map[email]
                    used_on_server = s['up'] + s['down']
                    total_on_server = s['total']
                    traffic_limit = key.get('traffic_limit', 0) or 0
                    sub_mode_key = bool(key.get('sub_id'))

                    if sub_mode_key:
                        # Subscription: прямая сумма up+down по всем inbound.
                        # Формула «через остаток» здесь некорректна — totalGB
                        # одинаковый на N inbound, но фактический расход общий.
                        traffic_used = used_on_server
                    elif traffic_limit > 0 and total_on_server > 0:
                        # Legacy keys-mode: формула через остаток
                        remaining_on_server = max(0, total_on_server - used_on_server)
                        traffic_used = max(0, traffic_limit - remaining_on_server)
                    else:
                        traffic_used = used_on_server

                    traffic_updates.append((traffic_used, key['id']))
                    key['_new_traffic_used'] = traffic_used

        except Exception as e:
            # Graceful degradation: не трогаем данные, продолжаем
            logger.warning(f"⚠️ Синхронизация трафика: сервер {server.get('name', server_id)} недоступен: {e}")
            continue
    
    # Массовое обновление трафика в БД
    if traffic_updates:
        bulk_update_traffic(traffic_updates)
    
    # Проверяем пороги уведомлений
    notification_text_template = get_setting(
        'traffic_notification_text',
        '⚠️ По ключу <b>{keyname}</b> осталось {percent}% трафика ({used} из {limit})'
    )
    
    for key in keys:
        traffic_limit = key.get('traffic_limit', 0) or 0
        if traffic_limit == 0:
            continue  # Безлимит — пропускаем
        
        # Используем обновлённое значение или из БД
        traffic_used = key.get('_new_traffic_used', key.get('traffic_used', 0) or 0)
        notified_pct = key.get('traffic_notified_pct', 100)
        
        # Вычисляем оставшийся процент
        remaining_pct = max(0, (1 - traffic_used / traffic_limit) * 100)
        
        # Проверяем пороги
        for threshold in TRAFFIC_THRESHOLDS:
            if remaining_pct <= threshold and notified_pct > threshold:
                # Отправляем уведомление
                telegram_id = key.get('telegram_id')
                if telegram_id:
                    # Формируем имя ключа
                    if key.get('custom_name'):
                        keyname = key['custom_name']
                    elif key.get('client_uuid'):
                        uuid = key['client_uuid']
                        keyname = f"{uuid[:4]}...{uuid[-4:]}" if len(uuid) >= 8 else uuid
                    else:
                        keyname = f"Ключ #{key['id']}"
                    
                    msg = notification_text_template.format(
                        keyname=keyname,
                        percent=threshold,
                        used=format_traffic(traffic_used),
                        limit=format_traffic(traffic_limit)
                    )
                    
                    try:
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=msg,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление о трафике пользователю {telegram_id}: {e}")
                
                # Обновляем порог в БД
                update_key_notified_pct(key['id'], threshold)
                key['traffic_notified_pct'] = threshold
                break  # Только одно уведомление за раз
    
    # Для subscription-ключей: если по нашему счётчику трафик исчерпан или
    # ключ истёк — отключаем ВСЕХ клиентов с этим email на сервере немедленно.
    # totalGB на отдельных inbound одинаковый, но клиенты сами не отключатся
    # пока их собственный счётчик не достигнет лимита, поэтому делаем это руками.
    from database.db_keys import is_key_active, is_traffic_exhausted
    from bot.services.vpn_api import ensure_subscription_keys_on_server, is_subscription_mode

    sub_mode_active = is_subscription_mode()
    for key in keys:
        if not key.get('sub_id'):
            continue
        # Подменяем traffic_used на свежее значение для проверки
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
    Полный проход по всем активным ключам с вызовом
    ensure_subscription_keys_on_server() — добивает отсутствующих клиентов
    в режиме subscription и удаляет лишние в режиме keys.

    Запускается раз в ~30 минут (каждые 6 циклов traffic-sync).
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
    Фоновая задача для синхронизации трафика каждые 5 минут.
    Каждые 6 циклов (≈30 мин) дополнительно вызывает
    materialize_subscription_state() для подгонки клиентов на панелях
    под текущий bot_mode.

    Args:
        bot: Экземпляр бота
    """
    logger.info("📊 Планировщик синхронизации трафика запущен (каждые 5 мин, materialize каждые 30 мин)")

    # Первый запуск через 30 секунд после старта бота
    await asyncio.sleep(30)

    cycle = 0
    while True:
        try:
            await sync_traffic_stats(bot)
            cycle += 1
            # Раз в 6 циклов (≈30 мин) — материализация subscription-состояния
            if cycle % 6 == 0:
                try:
                    await materialize_subscription_state()
                except Exception as e:
                    logger.error(f"Ошибка в materialize_subscription_state: {e}")

            # Ждём 5 минут
            await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Планировщик синхронизации трафика остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в планировщике синхронизации трафика: {e}")
            # Ждём 2 минуты и пробуем снова
            await asyncio.sleep(120)
