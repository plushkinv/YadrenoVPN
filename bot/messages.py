"""
Bot text messages.
"""

from bot.utils.user_ui_texts import get_ui_text

def missing_tariff_message() -> str:
    return get_ui_text('tariff.missing')
