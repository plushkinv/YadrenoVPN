"""Administrative web switches; system installation stays in the installer."""
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards.admin_misc import back_button, home_button, state_pair_buttons
import hashlib


def module_selector(module_id):
    return hashlib.sha256(module_id.encode()).hexdigest()[:16]


def web_settings_kb(state):
    builder = InlineKeyboardBuilder()
    if state['configured']:
        builder.row(*state_pair_buttons(state['enabled'], 'Веб включён', 'admin_web_set:1', 'Веб выключен', 'admin_web_set:0'))
    builder.row(*state_pair_buttons(state['sms']['sms_available'], 'SMS включены', 'admin_web_sms:enabled:1',
                                    'SMS выключены', 'admin_web_sms:enabled:0'))
    builder.row(*state_pair_buttons(state['sms']['sms_registration_required'], 'Подтверждение обязательно',
        'admin_web_sms:registration_required:1', 'Подтверждение необязательно', 'admin_web_sms:registration_required:0'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить ключ SMS.RU', callback_data='admin_web_sms_key'))
    builder.row(InlineKeyboardButton(text='🧩 Модули', callback_data='admin_web_modules'))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()


def web_modules_kb(modules):
    builder = InlineKeyboardBuilder()
    for item in modules:
        enabled = item['state'] == 'available'
        builder.row(InlineKeyboardButton(text=('🟢 ' if enabled else '⚪ ') + item['module_id'],
            callback_data=f"admin_web_module:{module_selector(item['module_id'])}:{int(not enabled)}"))
    builder.row(back_button('admin_web'), home_button())
    return builder.as_markup()
