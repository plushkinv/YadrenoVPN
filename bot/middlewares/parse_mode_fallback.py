"""
Кастомная сессия бота с автоматическим fallback при ошибках Markdown.

Перехватывает все вызовы методов Telegram API и при ошибке парсинга
автоматически повторяет запрос без parse_mode.
Поддерживает кастомный адрес Telegram Bot API из конфига.
"""
import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)

# Стандартный адрес Telegram Bot API (фоллбэк)
DEFAULT_TELEGRAM_API_URL = "https://api.telegram.org"


def _get_telegram_api_url() -> str:
    """Получает адрес Telegram Bot API из конфига с фоллбэком на стандартный."""
    try:
        from config import TELEGRAM_API_URL
        return TELEGRAM_API_URL
    except ImportError:
        return DEFAULT_TELEGRAM_API_URL


class SafeParseSession(AiohttpSession):
    """
    Сессия с автоматическим fallback при ошибках Markdown/HTML парсинга.
    
    Если Telegram возвращает ошибку "can't parse entities", 
    автоматически повторяет запрос с parse_mode=None.
    
    Использует адрес Telegram Bot API из конфига (TELEGRAM_API_URL).
    Если параметр не указан — фоллбэк на https://api.telegram.org.
    """
    
    def __init__(self, **kwargs):
        # Получаем кастомный URL из конфига
        api_url = _get_telegram_api_url()
        
        # Если URL отличается от стандартного — настраиваем кастомный сервер
        if api_url and api_url.rstrip('/') != DEFAULT_TELEGRAM_API_URL:
            kwargs.setdefault('api', TelegramAPIServer.from_base(api_url))
            logger.info(f"🌐 Telegram Bot API: {api_url}")
        else:
            logger.info(f"🌐 Telegram Bot API: {DEFAULT_TELEGRAM_API_URL} (стандартный)")
        
        super().__init__(**kwargs)
    
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: Optional[float] = None
    ) -> TelegramType:
        from bot.utils.text import prepare_telegram_method

        method = prepare_telegram_method(method)

        try:
            return await super().make_request(bot, method, timeout)
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
                    return await super().make_request(bot, method_copy, timeout)
            
            # Если это не ошибка парсинга или нет parse_mode — пробрасываем
            raise
