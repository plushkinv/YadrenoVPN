"""
Custom bot exceptions.
"""

class BotError(Exception):
    """Base class for bot exceptions."""
    pass


class TariffNotFoundError(BotError):
    """Exception: Tariff not found or inactive."""
    def __init__(self, message: str = None):
        from bot.messages import missing_tariff_message
        super().__init__(message or missing_tariff_message())
