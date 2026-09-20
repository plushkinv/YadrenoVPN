"""Daily web controls without domain, proxy, certificate or installer changes."""
from aiogram import F, Router
from bot.keyboards.admin import back_and_home_kb
from bot.keyboards.admin_web import web_modules_kb, web_settings_kb, module_selector
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import render_admin_dialog, render_admin_dialog_from_input
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from core.administration import set_module_enabled, set_sms_option, web_diagnostics
from core.results import CoreError

router = Router()
_KEY_PROMPT = '🔐 <b>Ключ SMS.RU</b>\n\nВведите API-ключ SMS.RU. Чтобы удалить его, сначала выключите SMS и отправьте «-».'


async def _allowed(callback):
    if is_admin(callback.from_user.id):
        return True
    await callback.answer('⛔ Доступ запрещён', show_alert=True)
    return False


async def _screen(callback, state):
    await state.clear()
    result = web_diagnostics(getattr(callback.bot, 'runtime_application', None))
    origin = escape_html(result['public_origin']) if result['configured'] else 'Не подключён через установщик'
    sms = result['sms']
    text = ('🌐 <b>Веб и Mini App</b>\n\n'
            f'Ссылка: {origin}\n'
            f"Веб: {'включён' if result['enabled'] else 'выключен'}\n"
            f"Ключ SMS.RU: {'задан' if sms['key_configured'] else 'не задан'}\n\n"
            'Подключение домена и HTTPS выполняется через установщик.\n'
            'Если SMS выключены, восстановление пароля по телефону недоступно.')
    if result['error']:
        text += '\n\nПараметры подключения некорректны. Проверьте их через установщик.'
    await safe_edit_or_send(callback.message, text, reply_markup=web_settings_kb(result))


@router.callback_query(F.data == 'admin_web')
async def show_web(callback, state):
    if await _allowed(callback):
        await _screen(callback, state)
        await callback.answer()


@router.callback_query(F.data.startswith('admin_web_set:'))
async def switch_web(callback, state):
    if not await _allowed(callback):
        return
    try:
        application = getattr(callback.bot, 'runtime_application', None)
        if application is None or callback.data.rsplit(':', 1)[1] not in ('0', '1'):
            raise ValueError()
        await application.set_web_enabled(callback.data.endswith(':1'))
    except Exception:
        await callback.answer('Не удалось переключить веб. Проверьте подключение и состояние сервиса.', show_alert=True)
        return
    await _screen(callback, state)
    await callback.answer('Настройка применена')


@router.callback_query(F.data.startswith('admin_web_sms:'))
async def switch_sms(callback, state):
    if not await _allowed(callback):
        return
    try:
        _, name, value = callback.data.split(':')
        if value not in ('0', '1'):
            raise ValueError()
        set_sms_option(name, value == '1')
    except (ValueError, CoreError):
        await callback.answer('Сначала укажите ключ SMS.RU и включите SMS.', show_alert=True)
        return
    await _screen(callback, state)
    await callback.answer('Настройка применена')


@router.callback_query(F.data == 'admin_web_sms_key')
async def edit_sms_key(callback, state):
    if not await _allowed(callback):
        return
    await render_admin_dialog(callback.message, state, _KEY_PROMPT, reply_markup=back_and_home_kb('admin_web'))
    await state.set_state(AdminStates.web_sms_key)
    await callback.answer()


@router.message(AdminStates.web_sms_key, F.text, ~F.text.startswith('/'))
async def save_sms_key(message, state):
    if not is_admin(message.from_user.id):
        return
    value = get_message_text_for_storage(message, 'plain')
    value = '' if value == '-' else value
    try:
        set_sms_option('api_key', value)
    except CoreError as error:
        reason = 'Сначала выключите SMS.' if error.code == 'sms_enabled' else 'Некорректный ключ.'
        await render_admin_dialog_from_input(message, state, '❌ ' + reason + '\n\n' + _KEY_PROMPT,
                                            reply_markup=back_and_home_kb('admin_web'))
        return
    await render_admin_dialog_from_input(message, state, '✅ <b>Ключ SMS.RU сохранён</b>\n\n' + ('Ключ задан.' if value else 'Ключ удалён.'),
                                        reply_markup=back_and_home_kb('admin_web'))
    await state.clear()


@router.callback_query(F.data == 'admin_web_modules')
@router.callback_query(F.data.startswith('admin_web_module:'))
async def modules(callback, state):
    if not await _allowed(callback):
        return
    if callback.data.startswith('admin_web_module:'):
        try:
            _, selector, value = callback.data.split(':')
            if value not in ('0', '1'):
                raise ValueError()
            matches = [item['module_id'] for item in web_diagnostics()['modules'] if module_selector(item['module_id']) == selector]
            if len(matches) != 1:
                raise ValueError()
            module_id = matches[0]
            set_module_enabled(module_id, value == '1')
        except (CoreError, ValueError):
            await callback.answer('Модуль не найден', show_alert=True)
            return
    result = web_diagnostics(getattr(callback.bot, 'runtime_application', None))
    states = {'available':'включён', 'disabled':'выключен', 'incompatible':'несовместим', 'unavailable':'недоступен'}
    lines = ['🧩 <b>Общие модули</b>', '', 'Отключение прекращает новые действия модуля. Уже принятые обязательства сохраняются.', '']
    lines += [f"{escape_html(item['module_id'])} {escape_html(item['version'])}: {states[item['state']]}" for item in result['modules']]
    if not result['modules']:
        lines.append('Модули не установлены.')
    await state.clear()
    await safe_edit_or_send(callback.message, '\n'.join(lines), reply_markup=web_modules_kb(result['modules']))
    await callback.answer()
