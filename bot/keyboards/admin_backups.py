"""Administrator-only backup actions; Telegram documents carry their file IDs."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .admin_misc import back_button, home_button


def backup_document_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text='📥 Загрузить или восстановить этот бэкап',
            callback_data='admin_backup_download',
        ),
    ]])


def backup_confirmation_kb(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Восстановить', callback_data=f'backup_apply:{job_id}'),
        InlineKeyboardButton(text='❌ Отмена', callback_data=f'backup_cancel:{job_id}'),
    ]])


def backup_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        back_button('admin_bot_settings'), home_button(),
    ]])
