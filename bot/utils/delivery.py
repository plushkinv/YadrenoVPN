"""Utilities for classifying message delivery errors."""
from aiogram.exceptions import TelegramForbiddenError


def is_bot_blocked_error(error: Exception) -> bool:
    """Returns True only for confirmed bot unavailability from the user."""
    return isinstance(error, TelegramForbiddenError)
