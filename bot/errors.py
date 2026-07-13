"""
Custom bot exceptions.
"""

class BotError(Exception):
    """Base class for bot exceptions."""
    pass


class TariffNotFoundError(BotError):
    """Exception: Tariff not found or inactive."""
    def __init__(self, message: str = None):
        from bot.messages import MISSING_TARIFF_MESSAGE
        super().__init__(message or MISSING_TARIFF_MESSAGE)
