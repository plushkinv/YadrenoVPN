"""
VPN Telegram bot entry point.

Initializes the bot, dispatcher, applies migrations and starts polling.
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
from bot.services.payment_auto_check import run_payment_auto_check_scheduler

# Importing routers
from bot.handlers.user import router as user_router
from bot.handlers.admin import admin_router


# Create a folder for logs if it doesn’t exist (it’s important to do this before basicConfig)
os.makedirs("logs", exist_ok=True)


# Setting up logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "logs/bot.log", 
            maxBytes=1024 * 1024,  # 1 megabyte
            backupCount=3, 
            encoding="utf-8"
        )
    ]
)

# Reducing noise from aiohttp
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)





async def on_startup(bot: Bot):
    """Actions when starting a bot."""
    logger.info("🚀 Бот запускается...")
    
    # Applying database migrations
    run_migrations()

    from bot.utils.user_ui_texts import load_user_ui_text_cache

    loaded_ui_texts = load_user_ui_text_cache()
    logger.info("User UI text cache loaded: %s entries", loaded_ui_texts)

    from bot.utils.page_renderer import validate_required_user_pages

    required_pages = validate_required_user_pages()
    logger.info("Required user pages validated: %s entries", required_pages)

    from bot.utils.telegram_links import load_telegram_link_domain

    load_telegram_link_domain()

    from bot.utils.update_block import try_unblock

    try_unblock()

    from bot.services.yadreno_admin_core_guard import recover_core_guards_on_startup

    await recover_core_guards_on_startup()

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
    
    # Bot information
    bot_info = await bot.get_me()
    bot.my_username = bot_info.username
    logger.info(f"✅ Бот запущен: @{bot_info.username}")
    
    # If updates are blocked, we immediately notify the admins
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
    """Actions to take when stopping the bot."""
    logger.info("🛑 Бот останавливается...")

    webhook_server = getattr(bot, 'custom_payment_webhook_server', None)
    if webhook_server is not None:
        try:
            await webhook_server.stop()
        except Exception as e:
            logger.warning(f"Не удалось остановить custom payment webhook server: {e}")
    
    # Close all VPN API sessions
    await close_all_clients()
    
    logger.info("✅ Бот остановлен")


async def main():
    """The main function of launching the bot."""
    # Importing a custom session with fallback for Markdown errors
    from bot.middlewares.parse_mode_fallback import SafeParseSession
    
    # Creating a bot with a custom session and a dispatcher
    session = SafeParseSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    from bot.middlewares.bot_blocked import BotBlockedResetMiddleware
    bot_blocked_reset = BotBlockedResetMiddleware()
    dp.message.outer_middleware(bot_blocked_reset)
    dp.callback_query.outer_middleware(bot_blocked_reset)
    
    # Registering routers
    # The order is important: first the more specific, then the general
    dp.include_router(admin_router)           # Admin panel (general)
    dp.include_router(user_router)            # User (has a strict internal order)
    
    # Global Network Error Handler
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.types import ErrorEvent
    from bot.utils.callbacks import is_expired_callback_error
    
    @dp.errors()
    async def global_error_handler(event: ErrorEvent):
        """Intercepts safe Telegram network/callback errors with short warnings."""
        exception = event.exception
        if isinstance(exception, TelegramNetworkError):
            logger.warning(f"⚠️ Нет связи с Telegram API: {exception}")
            return True  # The error has been processed, do not forward it further
        if is_expired_callback_error(exception):
            logger.warning("⚠️ Просроченный Telegram callback: %s", exception)
            return True
        # We log the rest of the errors as usual
        logger.error(f"Необработанная ошибка: {exception}", exc_info=True)
        return True
    
    # Registering startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Remove old updates and run polling
    await bot.delete_webhook(drop_pending_updates=True)
    

    
    # Launch the daily task scheduler (statistics + backups)
    daily_tasks = asyncio.create_task(run_daily_tasks(bot))
    # Launch the update check scheduler
    update_tasks = asyncio.create_task(run_update_check_scheduler(bot))
    # Launch the traffic synchronization scheduler (every 5 minutes)
    traffic_tasks = asyncio.create_task(run_traffic_sync_scheduler(bot))
    payment_check_tasks = asyncio.create_task(run_payment_auto_check_scheduler(bot))
    background_tasks = [daily_tasks, update_tasks, traffic_tasks, payment_check_tasks]
    
    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await close_all_clients()
        await bot.session.close()


if __name__ == "__main__":
    # Let's launch the bot
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки")
