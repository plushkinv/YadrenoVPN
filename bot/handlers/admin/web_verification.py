"""Private setup dialogs for the three built-in phone verification methods."""

from localization import text as _tr
from aiogram import F, Router

from bot.keyboards.admin import back_and_home_kb
from bot.keyboards.admin_web_verification import FIELD_LABELS, SERVICE_LABELS, verification_configuration_kb
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import render_admin_dialog, render_admin_dialog_from_input
from bot.utils.text import escape_html, get_message_text_for_storage
from core.phone_verification_settings import (
    FIELDS, METHOD_FIELDS, PREFIX, SECRET_FIELDS, admin_verification_settings, set_verification_option,
)
from core.results import CoreError
from database import requests as db

router = Router()
_REGISTRATION = {
    'ucaller': (
        _tr('web_verification.module.1')
    ),
    'smsaero': _tr('web_verification.module.2'),
}
_INSTRUCTIONS = {
    'ucaller': (
        _tr('web_verification.module.3') + _REGISTRATION['ucaller'] + _tr('web_verification.module.4')
    ),
    'smsaero_mobile': (
        _tr('web_verification.module.5') + _REGISTRATION['smsaero'] + _tr('web_verification.module.6')
    ),
    'smsaero_sms': (
        _tr('web_verification.module.7') + _REGISTRATION['smsaero'] + _tr('web_verification.module.8')
    ),
}
_FIELD_INSTRUCTIONS = {
    'ucaller_service_id': (
        _tr('web_verification.module.9')
    ),
    'ucaller_secret_key': _tr('web_verification.module.10'),
    'smsaero_email': (
        _tr('web_verification.module.11')
    ),
    'smsaero_api_key': (
        _tr('web_verification.module.12')
    ),
    'smsaero_mobile_sign': (
        _tr('web_verification.module.13')
    ),
    'smsaero_sms_sign': (
        _tr('web_verification.module.14')
    ),
}


async def _allowed(callback):
    if is_admin(callback.from_user.id):
        return True
    await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
    return False


def _prompt(field):
    emoji, label = FIELD_LABELS[field].split(' ', 1)
    provider = field.split('_', 1)[0]
    return (
        _tr('web_verification.prompt.1', p0=f'{emoji}', p1=f'{escape_html(label)}', p2=f'{_REGISTRATION[provider]}', p3=f'{_FIELD_INSTRUCTIONS[field]}')
    )


@router.callback_query(F.data.startswith('admin_web_verification:'))
async def switch_verification(callback, state):
    if not await _allowed(callback):
        return
    try:
        _, name, value = callback.data.split(':')
        if name == 'enabled':
            if value not in ('0', '1'):
                raise ValueError()
            value = value == '1'
        elif name != 'method':
            raise ValueError()
        set_verification_option(name, value)
    except (ValueError, CoreError):
        await callback.answer(_tr('web_verification.switch_verification.1'), show_alert=True)
        return
    from bot.handlers.admin.web import _screen
    await _screen(callback, state)
    await callback.answer(_tr('web.switch_web.2'))


@router.callback_query(F.data == 'admin_web_verification_config')
async def configuration(callback, state):
    if not await _allowed(callback):
        return
    settings = admin_verification_settings()
    method = settings['method']
    if not settings['enabled'] or method not in METHOD_FIELDS:
        from bot.handlers.admin.web import _screen
        await _screen(callback, state)
        await callback.answer()
        return
    lines = [_INSTRUCTIONS[method], '',
             _tr('web_verification.configuration.1') if settings['available'] else _tr('web_verification.configuration.2'), '']
    for field in METHOD_FIELDS[method]:
        value = db.get_setting(PREFIX + field, '') or ''
        shown = (_tr('web_verification.configuration.3') if value else _tr('broadcast.render_broadcast_menu.1')) if field in SECRET_FIELDS else value or _tr('web_verification.configuration.4')
        lines.append(FIELD_LABELS[field] + ': ' + escape_html(shown))
    await state.clear()
    await render_admin_dialog(callback.message, state, '\n'.join(lines), reply_markup=verification_configuration_kb(method))
    await callback.answer()


@router.callback_query(F.data.startswith('admin_web_verification_field:'))
async def edit_field(callback, state):
    if not await _allowed(callback):
        return
    field = callback.data.split(':', 1)[1]
    settings = admin_verification_settings()
    if not settings['enabled'] or field not in METHOD_FIELDS.get(settings['method'], ()):
        await callback.answer(_tr('web_verification.edit_field.1'), show_alert=True)
        return
    await render_admin_dialog(callback.message, state, _prompt(field),
                              reply_markup=back_and_home_kb('admin_web_verification_config'))
    await state.update_data(web_verification_field=field)
    await state.set_state(AdminStates.web_verification_value)
    await callback.answer()


@router.message(AdminStates.web_verification_value, F.text, ~F.text.startswith('/'))
async def save_field(message, state):
    if not is_admin(message.from_user.id):
        return
    field = (await state.get_data()).get('web_verification_field')
    if field not in FIELDS:
        return
    value = get_message_text_for_storage(message, 'plain')
    value = '' if value == '-' else value
    old = db.get_setting(PREFIX + field, '') or ''
    try:
        set_verification_option(field, value)
    except CoreError:
        await render_admin_dialog_from_input(message, state, _tr('web_verification.save_field.1') + _prompt(field),
                                            reply_markup=back_and_home_kb('admin_web_verification_config'))
        return
    current = db.get_setting(PREFIX + field, '') or ''
    active = admin_verification_settings()['available']
    text = (_tr('web_verification.save_field.2') if current != old else _tr('web_verification.save_field.3')) + '\n\n'
    text += (_tr('web_verification.save_field.4') if current else _tr('web_verification.save_field.5')) if field in SECRET_FIELDS else escape_html(current or _tr('web_verification.save_field.5'))
    text += '\n\n' + (_tr('web_verification.save_field.6') if active else _tr('web_verification.save_field.7'))
    await render_admin_dialog_from_input(message, state, text, reply_markup=back_and_home_kb('admin_web_verification_config'))
    await state.clear()
