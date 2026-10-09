"""Administrative web switches; system installation stays in the installer."""
from aiogram.types import InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards.admin_misc import back_button, home_button, state_pair_buttons
from bot.keyboards.admin_web_verification import add_verification_controls


def web_settings_kb(state, *, mini_app_supported=True):
    builder = InlineKeyboardBuilder()
    add_verification_controls(builder, state['verification'])
    builder.row(*state_pair_buttons(state.get('telegram_login', {}).get('enabled', False),
        'Telegram-вход включён', 'admin_web_telegram:1', 'Telegram-вход выключен', 'admin_web_telegram:0'))
    if state['configured']:
        builder.row(*state_pair_buttons(state['enabled'], 'Веб включён', 'admin_web_set:1', 'Веб выключен', 'admin_web_set:0'))
    if state['configured'] and state['enabled'] and mini_app_supported:
        builder.row(InlineKeyboardButton(text='👁 Открыть Mini App', web_app=WebAppInfo(url=state['public_origin'])))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()
