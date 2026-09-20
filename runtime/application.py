"""Initialize once, accept once, then run Telegram, HTTP and shared workers."""
from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from runtime import readiness
from runtime.ownership import RuntimeOwner

logger = logging.getLogger(__name__)


async def initialize_core() -> None:
    from database.migrations import run_migrations
    from bot.utils.user_ui_texts import load_user_ui_text_cache
    from bot.utils.page_renderer import validate_required_user_pages
    from bot.utils.telegram_links import load_telegram_link_domain
    from bot.utils.update_block import try_unblock
    from bot.services.yadreno_admin_core_guard import recover_core_guards_on_startup
    from bot.utils.custom_extensions import load_custom_extensions

    run_migrations()
    load_user_ui_text_cache()
    validate_required_user_pages()
    load_telegram_link_domain()
    try_unblock()
    await recover_core_guards_on_startup()
    result = load_custom_extensions()
    if result.skipped:
        logger.info('Custom extensions skipped: %s', result.reason)
    else:
        logger.info('Custom extensions loaded=%s failed=%s', len(result.loaded), len(result.failed))


async def accept_managed_update() -> None:
    from bot.services.update_rollback import (
        acknowledge_pending_update, pending_update_health_exists,
        wait_for_pending_update_acceptance,
    )

    pending = pending_update_health_exists()
    acknowledged = acknowledge_pending_update()
    if pending and not acknowledged:
        raise RuntimeError('Pending update health record could not be acknowledged')
    if acknowledged:
        accepted = await wait_for_pending_update_acceptance()
        if not accepted:
            raise RuntimeError('Update worker did not release the initialized runtime')


def start_background_tasks(bot: Any) -> list[asyncio.Task]:
    from bot.services.scheduler import run_daily_tasks, run_update_check_scheduler, run_traffic_sync_scheduler
    from bot.services.payment_auto_check import run_payment_auto_check_scheduler

    readiness.require_active()
    return [asyncio.create_task(worker(bot), name=worker.__name__) for worker in (
        run_daily_tasks, run_update_check_scheduler,
        run_traffic_sync_scheduler, run_payment_auto_check_scheduler,
    )]


async def deliver_startup_notifications(bot: Any) -> None:
    """Telegram delivery/recovery cannot hold the core activation acknowledgement."""
    from bot.services.update_rollback import notify_pending_rollback_result, notify_pending_update_result
    from bot.services.yadreno_admin import recover_active_dialogs_on_startup
    from bot.utils.update_block import is_update_blocked, get_blocked_message

    for operation in (notify_pending_rollback_result, notify_pending_update_result, recover_active_dialogs_on_startup):
        try:
            await operation(bot)
        except Exception:
            logger.exception('Startup Telegram notification failed: %s', operation.__name__)
    if is_update_blocked():
        from config import ADMIN_IDS
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='✅ OK', callback_data='dismiss_msg'),
        ]])
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, get_blocked_message(), reply_markup=keyboard, parse_mode='HTML')
            except Exception:
                logger.exception('Cannot deliver blocked-update notice')


class Application:
    def __init__(self, bot: Any):
        from database import connection

        self.bot = bot
        self.owner = RuntimeOwner(Path(connection.DB_PATH).parent / '.runtime.lock')
        self.web_server = None
        self.webhook_server = None
        self.tasks: list[asyncio.Task] = []
        self._owns_gate = False
        self._web_settings_lock = asyncio.Lock()
        self._database_anchor = None

    async def set_web_enabled(self, enabled: bool) -> None:
        """Apply a daily admin switch after CLI has configured the public origin."""
        from database.requests import get_setting, set_setting
        from web_api.app import start_web_server
        from web_api.settings import get_web_settings
        if type(enabled) is not bool or not self._owns_gate:
            raise ValueError('an active owning application is required')
        readiness.require_active()
        async with self._web_settings_lock:
            old = get_setting('web_enabled', '0')
            if enabled and not get_web_settings().public_origin:
                raise ValueError('web is not configured')
            if not enabled:
                if self.web_server is not None:
                    await self.web_server.stop()
                    self.web_server = None
                set_setting('web_enabled', '0')
                return
            set_setting('web_enabled', '1')
            try:
                if self.web_server is None:
                    self.web_server = await start_web_server()
            except BaseException:
                set_setting('web_enabled', old)
                raise

    async def start(self) -> None:
        self.owner.acquire()
        try:
            readiness.begin_startup()
            self._owns_gate = True
            with readiness.initialization_writes():
                await initialize_core()
                from database.connection import get_connection
                # Keep WAL open without a transaction so closing each short
                # repository connection does not force a last-client checkpoint.
                self._database_anchor = get_connection()
            from bot.services.custom_payment_webhooks import start_custom_payment_webhook_server
            from web_api.app import start_web_server

            # Keep the previous optional webhook failure policy and settings.
            try:
                self.webhook_server = await start_custom_payment_webhook_server(self.bot)
            except Exception:
                logger.exception('Cannot start legacy custom payment webhook server')
            self.bot.custom_payment_webhook_server = self.webhook_server
            self.web_server = await start_web_server()
            await accept_managed_update()
            readiness.activate()
            from runtime.delivery import set_bot
            set_bot(self.bot)
            self.bot.background_tasks = start_background_tasks(self.bot)
            self.tasks.extend(self.bot.background_tasks)
            self.bot.pending_update_result_task = asyncio.create_task(deliver_startup_notifications(self.bot))
            self.tasks.append(self.bot.pending_update_result_task)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._owns_gate:
            self.owner.release()
            return
        if self._owns_gate:
            readiness.deactivate()
            from runtime.delivery import set_bot, stop_pending
            set_bot(None)
            await stop_pending()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        failures = []
        for attribute in ('web_server', 'webhook_server'):
            server = getattr(self, attribute)
            if server is not None:
                try:
                    await server.stop()
                    setattr(self, attribute, None)
                except Exception as error:
                    failures.append(error)
        from bot.services.vpn_api import close_all_clients

        import sys
        password_workers = sys.modules.get('core.passwords')
        if password_workers is not None:
            try:
                await password_workers.close_password_workers()
            except Exception as error:
                failures.append(error)

        try:
            await close_all_clients()
        except Exception as error:
            failures.append(error)
        if self._database_anchor is not None:
            try:
                self._database_anchor.close()
                self._database_anchor = None
            except Exception as error:
                failures.append(error)
        if failures:
            # A surviving listener must not regain standalone write permission.
            # Keep the OS lock and closed fence until cleanup or process exit.
            raise RuntimeError('Runtime shutdown did not close every component') from failures[0]
        self.owner.release()
        readiness.release_runtime()
        self._owns_gate = False


async def run_telegram(bot: Any, dispatcher: Any) -> None:
    """Retry the transport independently; remove pending updates once per boot."""
    webhook_removed = False
    delay = 1
    while True:
        try:
            readiness.require_active()
            if not webhook_removed:
                await bot.delete_webhook(drop_pending_updates=True)
                webhook_removed = True
            info = await bot.get_me()
            bot.my_username = info.username
            from database.requests import set_setting
            set_setting('web_bot_id', str(info.id))
            set_setting('web_bot_username', info.username or '')
            await dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning('Telegram transport unavailable (%s); retry in %ss', type(error).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def run_application(bot: Any, dispatcher: Any) -> None:
    application = Application(bot)
    bot.runtime_application = application
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_signals = {}
    for number in (signal.SIGINT, signal.SIGTERM):
        previous_signals[number] = signal.getsignal(number)
        signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
    startup = asyncio.create_task(application.start(), name='core_startup')
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((startup, stopping), return_when=asyncio.FIRST_COMPLETED)
        if stopping in done:
            return
        await startup
        telegram = asyncio.create_task(run_telegram(bot, dispatcher), name='telegram_transport')
        application.tasks.append(telegram)
        telegram.add_done_callback(lambda _: stop.set())
        await stopping
    finally:
        startup.cancel()
        stopping.cancel()
        await asyncio.gather(startup, stopping, return_exceptions=True)
        try:
            await application.stop()
        finally:
            for number, handler in previous_signals.items():
                signal.signal(number, handler)
