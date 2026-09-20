"""Buttons for service ingress that must not register a Telegram account."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.user_ui_texts import render_ui_text


def account_link_confirmation_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=render_ui_text('account.link.button.confirm'),
                             callback_data='account_link:confirm:' + token),
        InlineKeyboardButton(text=render_ui_text('account.link.button.cancel'),
                             callback_data='account_link:cancel:' + token),
    ]])
