"""
VPN Telegram bot entry point.

Initializes the bot, dispatcher, applies migrations and starts polling.
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN

from bot.services.vpn_api import close_all_clients

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
    """Compatibility entry for tools that start the shared runtime explicitly."""
    from runtime.application import Application

    application = Application(bot)
    bot.runtime_application = application
    await application.start()


async def on_shutdown(bot: Bot):
    """Stop the shared runtime and its single set of background tasks."""
    application = getattr(bot, 'runtime_application', None)
    if application is not None:
        await application.stop()


async def main():
    """The main function of launching the bot."""
    # The Telegram transport remains an adapter over the shared runtime.
    from bot.middlewares.parse_mode_fallback import SafeParseSession
    
    # Creating a bot with a custom session and a dispatcher
    session = SafeParseSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    from bot.utils.custom_extensions import _set_extension_runtime_bot
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    from bot.middlewares.runtime import RuntimeIngressMiddleware
    dp.update.outer_middleware(RuntimeIngressMiddleware())

    from bot.middlewares.account_links import AccountLinkMiddleware
    account_links = AccountLinkMiddleware()
    dp.message.outer_middleware(account_links)
    dp.callback_query.outer_middleware(account_links)
    from bot.middlewares.user_registration import UserRegistrationMiddleware
    user_registration = UserRegistrationMiddleware()
    dp.message.outer_middleware(user_registration)
    dp.callback_query.outer_middleware(user_registration)
    dp.pre_checkout_query.outer_middleware(user_registration)

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
    
    _set_extension_runtime_bot(bot)
    try:
        from runtime.application import run_application

        await run_application(bot, dp)
    finally:
        _set_extension_runtime_bot(None)
        background_tasks = getattr(bot, 'background_tasks', [])
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
