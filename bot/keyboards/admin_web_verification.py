"""Phone verification controls for the existing web administrator card."""

from localization import text as _tr
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.admin_misc import back_button, home_button, state_pair_buttons
from core.phone_verification_settings import METHOD_FIELDS

METHOD_LABELS = {'ucaller': _tr('admin_web_verification.module.1'), 'smsaero_mobile': _tr('admin_web_verification.module.2'), 'smsaero_sms': _tr('admin_web_verification.module.3')}
SERVICE_LABELS = {'ucaller': '⭐ Ucaller', 'smsaero_mobile': _tr('admin_web_verification.module.4'), 'smsaero_sms': _tr('admin_web_verification.module.5')}
FIELD_LABELS = {
    'ucaller_service_id': _tr('admin_web_verification.module.6'), 'ucaller_secret_key': _tr('admin_web_verification.module.7'),
    'smsaero_email': _tr('admin_web_verification.module.8'), 'smsaero_api_key': _tr('admin_web_verification.module.9'),
    'smsaero_mobile_sign': _tr('admin_web_verification.module.10'), 'smsaero_sms_sign': _tr('admin_web_verification.module.10'),
}


def add_verification_controls(builder, settings):
    builder.row(*state_pair_buttons(settings['enabled'], _tr('admin_web_verification.add_verification_controls.1'), 'admin_web_verification:enabled:1',
                                    _tr('admin_web_verification.add_verification_controls.2'), 'admin_web_verification:enabled:0'))
    if settings['enabled']:
        builder.row(*(InlineKeyboardButton(
            text=('🟢 ' if settings['method'] == method else '⚪ ') + label,
            callback_data='admin_web_verification:method:' + method,
        ) for method, label in METHOD_LABELS.items()))
        if settings['method'] in SERVICE_LABELS:
            builder.row(InlineKeyboardButton(text=_tr('admin_web_verification.add_verification_controls.3') + SERVICE_LABELS[settings['method']],
                                             callback_data='admin_web_verification_config'))


def verification_configuration_kb(method):
    builder = InlineKeyboardBuilder()
    for field in METHOD_FIELDS[method]:
        builder.row(InlineKeyboardButton(text=FIELD_LABELS[field], callback_data='admin_web_verification_field:' + field))
    builder.row(back_button('admin_web'), home_button())
    return builder.as_markup()
