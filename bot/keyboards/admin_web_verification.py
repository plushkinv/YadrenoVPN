"""Phone verification controls for the existing web administrator card."""
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.admin_misc import back_button, home_button, state_pair_buttons
from core.phone_verification_settings import METHOD_FIELDS

METHOD_LABELS = {'ucaller': '⭐ Звонки', 'smsaero_mobile': 'Моб.Авторизация', 'smsaero_sms': 'СМС'}
SERVICE_LABELS = {'ucaller': '⭐ Ucaller', 'smsaero_mobile': 'SMS Aero · Мобильная авторизация', 'smsaero_sms': 'SMS Aero · СМС'}
FIELD_LABELS = {
    'ucaller_service_id': '🔢 Идентификатор сервиса', 'ucaller_secret_key': '🔐 Секретный ключ',
    'smsaero_email': '📧 Логин (email)', 'smsaero_api_key': '🔑 API-ключ',
    'smsaero_mobile_sign': '🏷 Имя отправителя', 'smsaero_sms_sign': '🏷 Имя отправителя',
}


def add_verification_controls(builder, settings):
    builder.row(*state_pair_buttons(settings['enabled'], 'Подтверждение обязательно', 'admin_web_verification:enabled:1',
                                    'Подтверждение необязательно', 'admin_web_verification:enabled:0'))
    if settings['enabled']:
        builder.row(*(InlineKeyboardButton(
            text=('🟢 ' if settings['method'] == method else '⚪ ') + label,
            callback_data='admin_web_verification:method:' + method,
        ) for method, label in METHOD_LABELS.items()))
        if settings['method'] in SERVICE_LABELS:
            builder.row(InlineKeyboardButton(text='🔧 Настроить ' + SERVICE_LABELS[settings['method']],
                                             callback_data='admin_web_verification_config'))


def verification_configuration_kb(method):
    builder = InlineKeyboardBuilder()
    for field in METHOD_FIELDS[method]:
        builder.row(InlineKeyboardButton(text=FIELD_LABELS[field], callback_data='admin_web_verification_field:' + field))
    builder.row(back_button('admin_web'), home_button())
    return builder.as_markup()
