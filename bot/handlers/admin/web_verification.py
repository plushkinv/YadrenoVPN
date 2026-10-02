"""Private setup dialogs for the three built-in phone verification methods."""
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
        'Зарегистрируйтесь в ⭐ Ucaller по реферальной ссылке автора:\n'
        '<a href="https://ucaller.ru/?invite=26951">https://ucaller.ru/?invite=26951</a>.'
    ),
    'smsaero': 'Зарегистрируйтесь на <a href="https://smsaero.ru/">smsaero.ru</a> и войдите в личный кабинет.',
}
_INSTRUCTIONS = {
    'ucaller': (
        '⭐ <b>Основной вариант — звонки Ucaller</b>\n\n' + _REGISTRATION['ucaller'] + '\n'
        'В разделе «Мои сервисы» создайте сервис и перенесите его идентификатор и секретный ключ сюда.\n\n'
        'Пользователь вводит последние четыре цифры номера входящего звонка. Отвечать не нужно. '
        'Это бюджетный вариант подтверждения; актуальную стоимость проверьте в кабинете Ucaller.'
    ),
    'smsaero_mobile': (
        '📱 <b>Мобильная авторизация SMS Aero</b>\n\n' + _REGISTRATION['smsaero'] + '\n'
        'API-ключ находится в «Настройки → API и SMPP». Подключите активное имя отправителя для мобильной авторизации.\n\n'
        'Укажите email, API-ключ и это имя. Пользователь подтверждает SIM-PUSH; если он недоступен, '
        'SMS Aero отправляет SMS-код. Адрес обратного уведомления настраивается автоматически после подключения сайта.\n\n'
        'Email и API-ключ общие с обычными SMS; имя отправителя задаётся отдельно.'
    ),
    'smsaero_sms': (
        '💬 <b>SMS-коды через SMS Aero</b>\n\n' + _REGISTRATION['smsaero'] + '\n'
        'API-ключ находится в «Настройки → API и SMPP». Укажите email, API-ключ и одобренное имя отправителя.\n\n'
        'Для своевременной доставки кодов отключите ручную модерацию, оформив договор в кабинете SMS Aero. '
        'Название сайта включается в текст SMS.\n\n'
        'Email и API-ключ общие с мобильной авторизацией; имя отправителя задаётся отдельно.'
    ),
}
_FIELD_INSTRUCTIONS = {
    'ucaller_service_id': (
        'Откройте «Мои сервисы», создайте сервис или выберите существующий '
        'и скопируйте его идентификатор (ID).'
    ),
    'ucaller_secret_key': 'Откройте нужный сервис в разделе «Мои сервисы» и скопируйте его секретный ключ.',
    'smsaero_email': (
        'Укажите email, с которым вы входите в этот кабинет SMS Aero. '
        'Логин общий для мобильной авторизации и обычных SMS.'
    ),
    'smsaero_api_key': (
        'Откройте «Настройки → API и SMPP» и скопируйте API-ключ. '
        'Ключ общий для мобильной авторизации и обычных SMS.'
    ),
    'smsaero_mobile_sign': (
        'В кабинете SMS Aero подключите имя отправителя для мобильной авторизации. '
        'После одобрения укажите его точно так же, как в кабинете. '
        'Для обычных SMS имя задаётся отдельно.'
    ),
    'smsaero_sms_sign': (
        'В кабинете SMS Aero выберите одобренное имя отправителя обычных SMS и скопируйте его сюда. '
        'Для мобильной авторизации имя задаётся отдельно.'
    ),
}


async def _allowed(callback):
    if is_admin(callback.from_user.id):
        return True
    await callback.answer('⛔ Доступ запрещён', show_alert=True)
    return False


def _prompt(field):
    emoji, label = FIELD_LABELS[field].split(' ', 1)
    provider = field.split('_', 1)[0]
    return (
        f'{emoji} <b>{escape_html(label)}</b>\n\n'
        f'{_REGISTRATION[provider]}\n\n{_FIELD_INSTRUCTIONS[field]}\n\n'
        'Введите значение. Чтобы удалить его, отправьте «-».\n'
        'Без полного набора реквизитов подтверждение телефона не работает.'
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
        await callback.answer('Не удалось изменить настройку.', show_alert=True)
        return
    from bot.handlers.admin.web import _screen
    await _screen(callback, state)
    await callback.answer('Настройка применена')


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
             'Подтверждение работает.' if settings['available'] else 'Подтверждение пока выключено: заполните настройки.', '']
    for field in METHOD_FIELDS[method]:
        value = db.get_setting(PREFIX + field, '') or ''
        shown = ('задан' if value else 'не задан') if field in SECRET_FIELDS else value or 'не задано'
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
        await callback.answer('Откройте текущие настройки службы.', show_alert=True)
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
        await render_admin_dialog_from_input(message, state, '❌ Некорректное значение.\n\n' + _prompt(field),
                                            reply_markup=back_and_home_kb('admin_web_verification_config'))
        return
    current = db.get_setting(PREFIX + field, '') or ''
    active = admin_verification_settings()['available']
    text = ('✅ <b>Настройка сохранена</b>' if current != old else '✅ <b>Настройка не изменилась</b>') + '\n\n'
    text += ('Значение задано.' if current else 'Значение удалено.') if field in SECRET_FIELDS else escape_html(current or 'Значение удалено.')
    text += '\n\n' + ('Подтверждение телефона работает.' if active else 'Подтверждение телефона пока выключено.')
    await render_admin_dialog_from_input(message, state, text, reply_markup=back_and_home_kb('admin_web_verification_config'))
    await state.clear()
