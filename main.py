"""
Точка входа VPN Telegram бота.

Инициализирует бота, диспетчер, применяет миграции и запускает polling.
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.migrations import run_migrations

from bot.services.vpn_api import close_all_clients
from bot.services.scheduler import run_daily_tasks, run_update_check_scheduler, run_traffic_sync_scheduler

# Импорт роутеров
from bot.handlers.user import router as user_router
from bot.handlers.admin import admin_router


# Создаём папку для логов если её нет (важно сделать до basicConfig)
os.makedirs("logs", exist_ok=True)


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "logs/bot.log", 
            maxBytes=1024 * 1024,  # 1 мегабайт
            backupCount=3, 
            encoding="utf-8"
        )
    ]
)

# Уменьшаем шум от aiohttp
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)





async def on_startup(bot: Bot):
    """Действия при запуске бота."""
    logger.info("🚀 Бот запускается...")
    
    # Применяем миграции БД
    run_migrations()

    from bot.utils.custom_extensions import load_custom_extensions
    extensions_result = load_custom_extensions()
    if extensions_result.skipped:
        logger.info("Custom extensions не загружены: %s", extensions_result.reason)
    else:
        logger.info(
            "Custom extensions: загружено %s, ошибок %s",
            len(extensions_result.loaded),
            len(extensions_result.failed),
        )

    try:
        from bot.services.custom_payment_webhooks import start_custom_payment_webhook_server

        bot.custom_payment_webhook_server = await start_custom_payment_webhook_server(bot)
    except Exception as e:
        logger.warning(f"Не удалось запустить custom payment webhook server: {e}")
    
    # Информация о боте
    bot_info = await bot.get_me()
    bot.my_username = bot_info.username
    logger.info(f"✅ Бот запущен: @{bot_info.username}")
    
    # Если обновления заблокированы — сразу уведомляем админов
    from bot.utils.update_block import is_update_blocked, get_blocked_message
    if is_update_blocked():
        from config import ADMIN_IDS
        from aiogram.types import InlineKeyboardButton
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        
        msg = get_blocked_message()
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

    try:
        from bot.services.yadreno_admin import recover_active_dialogs_on_startup

        await recover_active_dialogs_on_startup(bot)
    except Exception as e:
        logger.warning(f"Не удалось запустить восстановление Yadreno Admin: {e}")


async def on_shutdown(bot: Bot):
    """Действия при остановке бота."""
    logger.info("🛑 Бот останавливается...")

    webhook_server = getattr(bot, 'custom_payment_webhook_server', None)
    if webhook_server is not None:
        try:
            await webhook_server.stop()
        except Exception as e:
            logger.warning(f"Не удалось остановить custom payment webhook server: {e}")
    
    # Закрываем все VPN API сессии
    await close_all_clients()
    
    logger.info("✅ Бот остановлен")


async def main():
    """Главная функция запуска бота."""
    # Импортируем кастомную сессию с fallback для ошибок Markdown
    from bot.middlewares.parse_mode_fallback import SafeParseSession
    
    # Создаём бота с кастомной сессией и диспетчер
    session = SafeParseSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    from bot.middlewares.bot_blocked import BotBlockedResetMiddleware
    bot_blocked_reset = BotBlockedResetMiddleware()
    dp.message.outer_middleware(bot_blocked_reset)
    dp.callback_query.outer_middleware(bot_blocked_reset)
    
    # Регистрируем роутеры
    # Порядок важен: сначала более специфичные, потом общие
    dp.include_router(admin_router)           # Админ-панель (общая)
    dp.include_router(user_router)            # Пользователь (имеет строгий внутренний порядок)
    
    # Глобальный обработчик ошибок сети
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.types import ErrorEvent
    
    @dp.errors()
    async def global_error_handler(event: ErrorEvent):
        """Перехватывает сетевые ошибки Telegram API и пишет короткий warning."""
        exception = event.exception
        if isinstance(exception, TelegramNetworkError):
            logger.warning(f"⚠️ Нет связи с Telegram API: {exception}")
            return True  # Ошибка обработана, не пробрасываем дальше
        # Остальные ошибки логируем как обычно
        logger.error(f"Необработанная ошибка: {exception}", exc_info=True)
        return True
    
    # Регистрируем startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Удаляем старые обновления и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    

    
    # Запускаем планировщик ежедневных задач (статистика + бэкапы)
    daily_tasks = asyncio.create_task(run_daily_tasks(bot))
    # Запускаем планировщик проверки обновлений
    update_tasks = asyncio.create_task(run_update_check_scheduler(bot))
    # Запускаем планировщик синхронизации трафика (каждые 5 мин)
    traffic_tasks = asyncio.create_task(run_traffic_sync_scheduler(bot))
    background_tasks = [daily_tasks, update_tasks, traffic_tasks]
    
    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await close_all_clients()
        await bot.session.close()


if __name__ == "__main__":
    # Запускаем бота
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки")
