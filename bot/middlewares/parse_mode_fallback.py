"""
Custom bot session with automatic fallback for Markdown/HTML errors.

Intercepts all calls to Telegram API methods and in case of parsing error
automatically retry the request without parse_mode.
Supports one or more Telegram Bot API addresses from the config.
"""
import asyncio
import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)

# Standard Telegram Bot API address (fallback)
DEFAULT_TELEGRAM_API_URL = "https://api.telegram.org"


def _clean_telegram_api_url(value: str) -> str:
    """Returns a URL without spaces or trailing slash."""
    return value.strip().rstrip("/")


def _normalize_telegram_api_urls(value: Any) -> list[str]:
    """
    Normalizes TELEGRAM_API_URL.

    Supports the old string format and the new list/tuple string format.
    """
    if value is None:
        return [DEFAULT_TELEGRAM_API_URL]

    if isinstance(value, str):
        api_url = _clean_telegram_api_url(value)
        return [api_url] if api_url else [DEFAULT_TELEGRAM_API_URL]

    if isinstance(value, (list, tuple)):
        api_urls: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                logger.warning(
                    "⚠️ TELEGRAM_API_URL[%s] пропущен: ожидается строка, получено %s",
                    index,
                    type(item).__name__,
                )
                continue

            api_url = _clean_telegram_api_url(item)
            if api_url:
                api_urls.append(api_url)

        return api_urls or [DEFAULT_TELEGRAM_API_URL]

    logger.warning(
        "⚠️ TELEGRAM_API_URL должен быть строкой, списком или кортежем. "
        "Используется стандартный Telegram Bot API."
    )
    return [DEFAULT_TELEGRAM_API_URL]


def _get_telegram_api_urls() -> list[str]:
    """Receives Telegram Bot API addresses from the config with a fallback to the standard one."""
    try:
        from config import TELEGRAM_API_URL
        return _normalize_telegram_api_urls(TELEGRAM_API_URL)
    except ImportError:
        return [DEFAULT_TELEGRAM_API_URL]


def _format_api_server_url(api: TelegramAPIServer) -> str:
    """Returns the readable URL for the TelegramAPIServer passed directly."""
    base_url = getattr(api, 'base', None)
    if isinstance(base_url, str):
        return base_url.replace('/bot{token}/{method}', '').rstrip('/')
    return 'передан через параметр api'


class SafeParseSession(AiohttpSession):
    """
    Session with automatic fallback for Markdown/HTML parsing errors.
    
    If Telegram returns the error "can't parse entities", 
    automatically retry the request with parse_mode=None.
    
    Uses Telegram Bot API addresses from the config (TELEGRAM_API_URL).
    If the parameter is not specified, the fallback is to https://api.telegram.org.
    If there are several addresses, it switches to the next one after a network failure.
    """
    
    def __init__(self, **kwargs):
        explicit_api = kwargs.get('api')
        if explicit_api is not None:
            self._telegram_api_urls = [_format_api_server_url(explicit_api)]
            self._telegram_api_servers = [explicit_api]
        else:
            self._telegram_api_urls = _get_telegram_api_urls()
            self._telegram_api_servers = [
                TelegramAPIServer.from_base(api_url)
                for api_url in self._telegram_api_urls
            ]

        self._telegram_api_index = 0
        self._telegram_api_switch_lock = asyncio.Lock()

        if explicit_api is None:
            kwargs['api'] = self._telegram_api_servers[self._telegram_api_index]

        active_api_url = self.active_api_url
        if len(self._telegram_api_urls) > 1:
            logger.info(
                "🌐 Telegram Bot API: %s (активный 1/%s)",
                active_api_url,
                len(self._telegram_api_urls),
            )
        elif active_api_url != DEFAULT_TELEGRAM_API_URL:
            logger.info("🌐 Telegram Bot API: %s", active_api_url)
        else:
            logger.info("🌐 Telegram Bot API: %s (стандартный)", DEFAULT_TELEGRAM_API_URL)
        
        super().__init__(**kwargs)

    @property
    def active_api_url(self) -> str:
        """Returns the URL of the current Telegram Bot API."""
        return self._telegram_api_urls[self._telegram_api_index]

    async def _send_prepared_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: Optional[float],
    ) -> TelegramType:
        return await super().make_request(bot, method, timeout)

    async def _request_once(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: Optional[float],
    ) -> TelegramType:
        failed_api_index = self._telegram_api_index
        try:
            return await self._send_prepared_request(bot, method, timeout)
        except (TelegramNetworkError, TelegramServerError, ClientDecodeError) as e:
            await self._switch_telegram_api_after_failure(failed_api_index, e)
            raise

    async def _switch_telegram_api_after_failure(
        self,
        failed_api_index: int,
        error: Exception,
    ) -> None:
        async with self._telegram_api_switch_lock:
            failed_api_url = self._telegram_api_urls[failed_api_index]

            if len(self._telegram_api_urls) <= 1:
                logger.warning(
                    "⚠️ Telegram Bot API не ответил (%s): %s",
                    failed_api_url,
                    error,
                )
                return

            if self._telegram_api_index != failed_api_index:
                logger.debug(
                    "Telegram Bot API уже переключён после параллельного сбоя: %s -> %s",
                    failed_api_url,
                    self.active_api_url,
                )
                return

            next_api_index = (self._telegram_api_index + 1) % len(self._telegram_api_urls)
            next_api_url = self._telegram_api_urls[next_api_index]

            self._telegram_api_index = next_api_index
            self.api = self._telegram_api_servers[next_api_index]

            logger.warning(
                "⚠️ Telegram Bot API не ответил (%s): %s. Переключаюсь на %s",
                failed_api_url,
                error,
                next_api_url,
            )
    
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: Optional[float] = None
    ) -> TelegramType:
        from bot.utils.text import prepare_telegram_method

        method = prepare_telegram_method(method)

        try:
            return await self._request_once(bot, method, timeout)
        except TelegramBadRequest as e:
            error_msg = str(e).lower()
            
            # Checking that this is a Markdown/HTML parsing error
            if "can't parse entities" in error_msg:
                # Checking if the method has the parse_mode attribute
                if hasattr(method, 'parse_mode') and method.parse_mode is not None:
                    logger.warning(
                        f"Ошибка Markdown парсинга в {method.__class__.__name__}, "
                        f"повторяю без форматирования: {e}"
                    )
                    
                    # Create a copy of the method without parse_mode
                    # Using model_copy for Pydantic models
                    method_copy = method.model_copy(update={'parse_mode': None})
                    
                    # Repeat the request without parse_mode
                    return await self._request_once(bot, method_copy, timeout)
            
            # If this is not a parsing error or there is no parse_mode, forward
            raise
