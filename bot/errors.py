"""
Кастомные исключения бота.
"""

class BotError(Exception):
    """Базовый класс для исключений бота."""
    pass


class TariffNotFoundError(BotError):
    """Исключение: Тариф не найден или неактивен."""
    def __init__(self, message: str = None):
        from bot.messages import MISSING_TARIFF_MESSAGE
        super().__init__(message or MISSING_TARIFF_MESSAGE)
