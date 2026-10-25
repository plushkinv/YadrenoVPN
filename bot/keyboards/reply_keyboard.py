"""Factory for the optional persistent command keyboard."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def build_reply_keyboard(rows: list[list[dict[str, str]]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=button['label']) for button in row] for row in rows],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
