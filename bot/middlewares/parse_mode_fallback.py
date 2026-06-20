"""
Кастомная сессия бота с автоматическим fallback при ошибках Markdown/HTML.

Перехватывает все вызовы методов Telegram API и при ошибке парсинга
автоматически повторяет запрос без parse_mode.
Поддерживает один или несколько адресов Telegram Bot API из конфига.
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

# Стандартный адрес Telegram Bot API (фоллбэк)
DEFAULT_TELEGRAM_API_URL = "https://api.telegram.org"


def _clean_telegram_api_url(value: str) -> str:
    """Возвращает URL без пробелов и завершающего слеша."""
    return value.strip().rstrip("/")


def _normalize_telegram_api_urls(value: Any) -> list[str]:
    """
    Нормализует TELEGRAM_API_URL.

    Поддерживает старый формат строкой и новый формат списком/кортежем строк.
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
    """Получает адреса Telegram Bot API из конфига с фоллбэком на стандартный."""
    try:
        from config import TELEGRAM_API_URL
        return _normalize_telegram_api_urls(TELEGRAM_API_URL)
    except ImportError:
        return [DEFAULT_TELEGRAM_API_URL]


def _format_api_server_url(api: TelegramAPIServer) -> str:
    """Возвращает читаемый URL для TelegramAPIServer, переданного напрямую."""
    base_url = getattr(api, 'base', None)
    if isinstance(base_url, str):
        return base_url.replace('/bot{token}/{method}', '').rstrip('/')
    return 'передан через параметр api'


class SafeParseSession(AiohttpSession):
    """
    Сессия с автоматическим fallback при ошибках Markdown/HTML парсинга.
    
    Если Telegram возвращает ошибку "can't parse entities", 
    автоматически повторяет запрос с parse_mode=None.
    
    Использует адреса Telegram Bot API из конфига (TELEGRAM_API_URL).
    Если параметр не указан — фоллбэк на https://api.telegram.org.
    Если адресов несколько — переключается на следующий после сетевого сбоя.
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
        """Возвращает URL текущего Telegram Bot API."""
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
            
            # Проверяем, что это ошибка парсинга Markdown/HTML
            if "can't parse entities" in error_msg:
                # Проверяем, есть ли у метода атрибут parse_mode
                if hasattr(method, 'parse_mode') and method.parse_mode is not None:
                    logger.warning(
                        f"Ошибка Markdown парсинга в {method.__class__.__name__}, "
                        f"повторяю без форматирования: {e}"
                    )
                    
                    # Создаём копию метода без parse_mode
                    # Используем model_copy для Pydantic моделей
                    method_copy = method.model_copy(update={'parse_mode': None})
                    
                    # Повторяем запрос без parse_mode
                    return await self._request_once(bot, method_copy, timeout)
            
            # Если это не ошибка парсинга или нет parse_mode — пробрасываем
            raise
