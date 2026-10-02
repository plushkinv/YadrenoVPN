"""Administrative web switches; system installation stays in the installer."""
from aiogram.types import InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards.admin_misc import back_button, home_button, state_pair_buttons
from bot.keyboards.admin_web_verification import add_verification_controls
import hashlib


def module_selector(module_id):
    return hashlib.sha256(module_id.encode()).hexdigest()[:16]


def web_settings_kb(state, *, mini_app_supported=True):
    builder = InlineKeyboardBuilder()
    add_verification_controls(builder, state['verification'])
    if state['configured']:
        builder.row(*state_pair_buttons(state['enabled'], 'Веб включён', 'admin_web_set:1', 'Веб выключен', 'admin_web_set:0'))
    builder.row(InlineKeyboardButton(text='🧩 Модули', callback_data='admin_web_modules'))
    builder.row(InlineKeyboardButton(text='🎨 Оформление', callback_data='admin_web_presentation'))
    if state['configured'] and state['enabled'] and mini_app_supported:
        builder.row(InlineKeyboardButton(text='👁 Открыть Mini App', web_app=WebAppInfo(url=state['public_origin'])))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()


def web_presentation_kb():
    builder = InlineKeyboardBuilder()
    for name, label in (('title', '🏷 Название'), ('logo', '🖼 Логотип'), ('accent', '🎨 Основной цвет'),
                        ('sync_interval_seconds', '🔄 Обновление данных')):
        builder.row(InlineKeyboardButton(text=label, callback_data='admin_web_style:' + name))
    for preset, label in (('clear', 'Universal / Clear'), ('signal', 'Signal / Dark'), ('friendly', 'Friendly / Brand')):
        builder.row(InlineKeyboardButton(text='🎨 ' + label, callback_data='admin_web_preset:' + preset))
    builder.row(InlineKeyboardButton(text='☀️ Светлая', callback_data='admin_web_theme:light'),
                InlineKeyboardButton(text='🌙 Тёмная', callback_data='admin_web_theme:dark'))
    builder.row(back_button('admin_web'), home_button())
    return builder.as_markup()


def web_modules_kb(modules):
    builder = InlineKeyboardBuilder()
    for item in modules:
        enabled = item['state'] == 'available'
        builder.row(InlineKeyboardButton(text=('🟢 ' if enabled else '⚪ ') + item['module_id'],
            callback_data=f"admin_web_module:{module_selector(item['module_id'])}:{int(not enabled)}"))
    builder.row(back_button('admin_web'), home_button())
    return builder.as_markup()
