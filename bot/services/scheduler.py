"""
Модуль для ежедневных автоматических задач.

Включает:
- Отправку суточной статистики администраторам
- Создание и отправку архива с бэкапами (БД бота + VPN панелей)
"""

import asyncio
import logging
import os
import zipfile
from datetime import datetime, time as dt_time, timedelta
from io import BytesIO
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from config import ADMIN_IDS, GITHUB_REPO_URL
from database.requests import (
    get_all_servers, get_users_stats, get_keys_stats,
    get_daily_payments_stats, get_new_users_count_today,
    get_setting, get_expiring_keys, is_notification_sent_today, log_notification_sent
)
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic
from bot.utils.git_utils import check_for_updates
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Путь к базе данных бота
BOT_DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'vpn_bot.db')


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
            servers_info.append(f"  🔴 *{server['name']}* — выключен")
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
                    f"  🟢 *{server['name']}*: {online} онлайн, "
                    f"трафик: {traffic}{cpu_text}"
                )
            else:
                servers_info.append(f"  🔴 *{server['name']}* — недоступен")
        except Exception as e:
            logger.warning(f"Ошибка получения статистики сервера {server['name']}: {e}")
            servers_info.append(f"  ⚠️ *{server['name']}* — ошибка подключения")
    
    servers_text = "\n".join(servers_info) if servers_info else "  Нет серверов"
    
    # Формируем текст отчёта
    today = datetime.now().strftime("%d.%m.%Y")
    
    # Платежи
    payments_total = payments.get('paid_count', 0)
    payments_cents = payments.get('paid_cents', 0)
    payments_stars = payments.get('paid_stars', 0)
    payments_pending = payments.get('pending_count', 0)
    
    payments_text = []
    if payments_cents > 0:
        payments_text.append(f"${payments_cents / 100:.2f}")
    if payments_stars > 0:
        payments_text.append(f"⭐{payments_stars}")
    payments_sum = " + ".join(payments_text) if payments_text else "0"
    
    report = f"""📊 *Суточная статистика за {today}*

👥 *Пользователи:*
  Всего: {users.get('total', 0)}
  Активных: {users.get('active', 0)}
  Новых за сутки: {new_users}

🔑 *VPN-ключи:*
  Всего: {keys.get('total', 0)}
  Активных: {keys.get('active', 0)}
  Истёкших: {keys.get('expired', 0)}
  Создано за сутки: {keys.get('created_today', 0)}

💳 *Платежи за сутки:*
  Успешных: {payments_total}
  Ожидающих: {payments_pending}
  Сумма: {payments_sum}

🖥️ *Серверы:*
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
                    parse_mode="Markdown"
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


async def send_backup_archive(bot: Bot) -> None:
    """
    Создаёт и отправляет архив бэкапов всем администраторам.
    
    Args:
        bot: Экземпляр бота
    """
    try:
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
                    caption=f"📦 *Ежедневный бэкап за {today}*\n\nСодержит базы данных бота и VPN-серверов.",
                    parse_mode="Markdown"
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
    """
    logger.info("⏳ Запуск проверки истекающих ключей...")
    try:
        days = int(get_setting('notification_days', '3'))
        notification_text = get_setting('notification_text', 
            '⚠️ *Ваш VPN-ключ скоро истекает!*\n\n'
            'Через {days} дней закончится срок действия вашего ключа.\n\n'
            'Продлите подписку, чтобы сохранить доступ к VPN без перерыва!'
        )
        
        expiring_keys = get_expiring_keys(days)
        sent_count = 0
        
        for key_info in expiring_keys:
            vpn_key_id = key_info['vpn_key_id']
            user_telegram_id = key_info['user_telegram_id']
            days_left = key_info['days_left']
            
            # Проверяем, отправляли ли мы сегодня
            if is_notification_sent_today(vpn_key_id):
                continue
            
            # Формируем текст с подстановкой дней
            text = notification_text.format(days=days_left)
            
            try:
                await bot.send_message(
                    chat_id=user_telegram_id,
                    text=text,
                    parse_mode="Markdown"
                )
                log_notification_sent(vpn_key_id)
                sent_count += 1
            except Exception as e:
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
            # Ждём до 03:00
            seconds_to_wait = get_seconds_until(3, 0)
            logger.info(f"Следующий запуск задач через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
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
    logger.info("🔍 Ежедневная проверка обновлений...")
    
    # Проверяем настроен ли GitHub URL
    if not GITHUB_REPO_URL:
        logger.warning("GitHub URL не настроен, пропускаем проверку обновлений")
        return
        
    try:
        # Проверяем обновления
        success, commits_behind, log_text = check_for_updates()
        
        if success and commits_behind > 0:
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
            
            # Отправляем уведомления админам
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"📦 *Доступно обновление!*\n\n{log_text}",
                        reply_markup=kb,
                        parse_mode="Markdown"
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
            # Ждём до 12:00
            seconds_to_wait = get_seconds_until(12, 0)
            logger.info(f"Следующая проверка обновлений через {seconds_to_wait // 3600}ч {(seconds_to_wait % 3600) // 60}м")
            
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

