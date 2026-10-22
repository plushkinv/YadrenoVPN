"""Daily web controls without domain, proxy, certificate or installer changes."""
from aiogram import F, Router
from bot.keyboards.admin_web import web_settings_kb
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, safe_edit_or_send
from core.administration import web_diagnostics

from bot.handlers.admin.web_verification import router as verification_router
from bot.keyboards.admin_web_verification import SERVICE_LABELS

router = Router()
router.include_router(verification_router)


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
        ui = '<code>' + escape_html(publication['build_id'][:12]) + '</code> · оформление <code>' + escape_html(publication['customization_version']) + '</code>'
    text = ('🌐 <b>Веб и Mini App</b>\n\n'
            f'🔗 <b>Ссылка:</b> {origin}\n'
            f"<b>Веб:</b> {'включён' if result['enabled'] else 'выключен'}\n"
            f'<b>Интерфейс:</b> {ui}\n\n'
            '📱 <b>Подтверждение телефона</b>\n'
            f'• <b>Статус:</b> {verification_status}\n'
            f'• <b>Способ:</b> {service}\n\n'
            f"✈️ <b>Вход через Telegram:</b> {'включён' if result['telegram_login']['enabled'] else 'выключен'}\n"
            '<b>Настройка в @BotFather:</b>\n'
            '1. Откройте Mini App → ваш бот → <b>Login Widget</b>.\n'
            '2. Если есть кнопка <b>Switch to OpenID Connect Login</b>, нажмите её.\n'
            '3. В <b>Redirect URIs</b> добавьте адрес сайта с <code>/</code> в конце.\n'
            '4. В <b>Trusted Origins</b> — тот же адрес без <code>/</code> в конце.\n'
            'Алгоритм подписи — <b>RS256 (Default)</b>. '
            'Client ID и Client Secret вводить в боте не нужно.\n\n'
            '⚙️ <b>Домен и HTTPS</b>\n'
            'Подключаются через <b>установщик</b>.\n\n'
            'ℹ️ <b>Без подтверждения телефона:</b>\n'
            '• регистрация — без проверки номера;\n'
            '• восстановление пароля по телефону — недоступно.')
    if result['error'] == 'web_setup_recovery_failed':
        text += '\n\n<b>Веб недоступен:</b> не удалось восстановить настройку подключения. Откройте установщик; бот продолжает работать.'
    elif result['error']:
        text += '\n\n<b>Параметры подключения некорректны.</b> Проверьте их через установщик.'
    if publication.get('warning'):
        text += '\n\n<b>Не удалось обновить базовый интерфейс при запуске.</b> Сохранённая версия проверена; подробности — в журнале.'
    release = result.get('ui_release')
    if release and release.get('rebuild_required'):
        text += ('\n\n<b>После обновления сохранён прежний интерфейс.</b> Кастомное оформление требует проверки '
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


@router.callback_query(F.data.startswith('admin_web_telegram:'))
async def switch_telegram_login(callback, state):
    if not await _allowed(callback):
        return
    value = callback.data.rsplit(':', 1)[-1]
    if value not in ('0', '1'):
        await callback.answer()
        return
    from database.requests import set_setting
    set_setting('web_telegram_login_enabled', value)
    await _screen(callback, state)
    await callback.answer('Настройка применена')
