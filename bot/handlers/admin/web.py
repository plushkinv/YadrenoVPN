"""Daily web controls without domain, proxy, certificate or installer changes."""
from aiogram import F, Router
from bot.keyboards.admin import back_and_home_kb
from bot.keyboards.admin_web import web_modules_kb, web_settings_kb, module_selector, web_presentation_kb
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import render_admin_dialog, render_admin_dialog_from_input
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from core.administration import set_module_enabled, web_diagnostics
from core.results import CoreError
from core.web_ui import public_settings, set_presentation_setting

from bot.handlers.admin.web_verification import router as verification_router
from bot.keyboards.admin_web_verification import SERVICE_LABELS

router = Router()
router.include_router(verification_router)
_STYLE_PROMPTS = {
    'title': '🏷 <b>Название сайта</b>\n\nВведите название, до 100 символов.',
    'logo': '🖼 <b>Логотип сайта</b>\n\nВведите адрес изображения из установленного оформления, начинающийся с /ui/assets/. Чтобы убрать логотип, отправьте «-».',
    'accent': '🎨 <b>Основной цвет</b>\n\nВведите цвет в формате #2563eb. Чтобы использовать цвет пресета, отправьте «-».',
    'sync_interval_seconds': '🔄 <b>Обновление данных</b>\n\nВведите интервал в секундах: от 30 до 86400. При обновлении данных проверяется и версия интерфейса.',
}


@router.callback_query(F.data == 'admin_web_presentation')
@router.callback_query(F.data.startswith('admin_web_preset:'))
@router.callback_query(F.data.startswith('admin_web_theme:'))
async def presentation(callback, state):
    if not await _allowed(callback):
        return
    if ':' in callback.data:
        kind, value = callback.data.split(':', 1)
        try:
            set_presentation_setting('preset' if kind == 'admin_web_preset' else 'theme', value)
        except CoreError:
            await callback.answer('Не удалось изменить оформление', show_alert=True)
            return
    settings = public_settings()
    text = ('🎨 <b>Оформление сайта</b>\n\n'
            f"Название: {escape_html(settings['title'])}\n"
            f"Логотип: {escape_html(settings['logo'] or 'не выбран')}\n"
            f"Пресет: {escape_html(settings['preset'])}\n"
            f"Тема: {'светлая' if settings['theme'] == 'light' else 'тёмная'}\n"
            f"Цвет: {escape_html(settings['accent'] or 'из пресета')}\n"
            f"Обновление: каждые {settings['sync_interval_seconds']} с\n\n"
            'Выбранный в Mini App готовый дизайн сохраняется зелёной галочкой.\n'
            'Контакты и ссылки помощи используются из существующих страниц бота.')
    await state.clear()
    await safe_edit_or_send(callback.message, text, reply_markup=web_presentation_kb())
    await callback.answer()


@router.callback_query(F.data.startswith('admin_web_style:'))
async def edit_presentation(callback, state):
    if not await _allowed(callback):
        return
    name = callback.data.split(':', 1)[1]
    if name not in _STYLE_PROMPTS:
        await callback.answer('Настройка не найдена', show_alert=True)
        return
    await render_admin_dialog(callback.message, state, _STYLE_PROMPTS[name],
                              reply_markup=back_and_home_kb('admin_web_presentation'))
    await state.update_data(web_style=name)
    await state.set_state(AdminStates.web_presentation)
    await callback.answer()


@router.message(AdminStates.web_presentation, F.text, ~F.text.startswith('/') | F.text.startswith('/ui/assets/'))
async def save_presentation(message, state):
    if not is_admin(message.from_user.id):
        return
    name = (await state.get_data()).get('web_style')
    if name not in _STYLE_PROMPTS:
        return
    value = get_message_text_for_storage(message, 'plain')
    value = '' if value == '-' and name in ('accent', 'logo') else value
    previous = public_settings()[name]
    try:
        settings = set_presentation_setting(name, value)
    except CoreError:
        await render_admin_dialog_from_input(message, state, '❌ Некорректное значение.\n\n' + _STYLE_PROMPTS[name],
                                            reply_markup=back_and_home_kb('admin_web_presentation'))
        return
    changed = settings[name] != previous
    await render_admin_dialog_from_input(message, state,
        ('✅ <b>Настройка сохранена</b>' if changed else '✅ <b>Настройка не изменилась</b>')
        + '\n\n' + escape_html(str(settings[name] if settings[name] is not None else 'не задано')),
        reply_markup=back_and_home_kb('admin_web_presentation'))
    await state.clear()


async def _allowed(callback):
    if is_admin(callback.from_user.id):
        return True
    await callback.answer('⛔ Доступ запрещён', show_alert=True)
    return False


async def _screen(callback, state):
    await state.clear()
    result = web_diagnostics(getattr(callback.bot, 'runtime_application', None))
    origin = escape_html(result['public_origin']) if result['configured'] else 'Не подключён через установщик'
    verification = result['verification']
    verification_status = 'работает' if verification['available'] else 'выключено'
    if verification['enabled'] and not verification['configured']:
        verification_status = 'выключено: заполните настройки службы'
    service = SERVICE_LABELS.get(verification['method'], 'не выбран')
    publication = result['ui']
    ui = {'not_published': 'ещё не подготовлен', 'unavailable': 'не удалось проверить'}.get(publication['state'])
    if ui is None:
        ui = escape_html(publication['build_id'][:12]) + ' · оформление ' + escape_html(publication['customization_version'])
    text = ('🌐 <b>Веб и Mini App</b>\n\n'
            f'Ссылка: {origin}\n'
            f"Веб: {'включён' if result['enabled'] else 'выключен'}\n"
            f'Интерфейс: {ui}\n'
            f'Подтверждение телефона: {verification_status}\n'
            f'Способ: {service}\n\n'
            'Подключение домена и HTTPS выполняется через установщик.\n'
            'Без настроенного подтверждения регистрация доступна без проверки номера, '
            'а восстановление пароля по телефону недоступно.')
    if result['error'] == 'web_setup_recovery_failed':
        text += '\n\nВеб недоступен: не удалось восстановить настройку подключения. Откройте установщик; бот продолжает работать.'
    elif result['error']:
        text += '\n\nПараметры подключения некорректны. Проверьте их через установщик.'
    if publication.get('warning'):
        text += '\n\nПри запуске не удалось обновить базовый интерфейс. Сохранённая версия проверена; подробности — в журнале.'
    release = result.get('ui_release')
    if release and release.get('rebuild_required'):
        text += ('\n\nПосле обновления сохранён прежний интерфейс. Кастомное оформление требует проверки '
                 'и повторной сборки; подробности доступны в диагностике обновления.')
    private = getattr(getattr(callback.message, 'chat', None), 'type', None) == 'private'
    supported = private and not getattr(callback.message, 'business_connection_id', None)
    await safe_edit_or_send(callback.message, text, reply_markup=web_settings_kb(result, mini_app_supported=supported))


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
