"""Утилиты для классификации ошибок доставки сообщений."""
from aiogram.exceptions import TelegramForbiddenError


def is_bot_blocked_error(error: Exception) -> bool:
    """Возвращает True только для подтверждённой недоступности бота у пользователя."""
    return isinstance(error, TelegramForbiddenError)
