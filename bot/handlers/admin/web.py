"""Daily web controls without domain, proxy, certificate or installer changes."""

from localization import text as _tr
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
    await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
    return False


async def _screen(callback, state):
    await state.clear()
    result = web_diagnostics(getattr(callback.bot, 'runtime_application', None))
    origin = escape_html(result['public_origin']) if result['configured'] else _tr('web.screen.1')
    verification = result['verification']
    verification_status = _tr('web.screen.2') if verification['available'] else _tr('coupons.render_purchase_settings.2')
    if verification['enabled'] and not verification['configured']:
        verification_status = _tr('web.screen.3')
    service = SERVICE_LABELS.get(verification['method'], _tr('web.screen.4'))
    publication = result['ui']
    ui = {'not_published': _tr('web.screen.5'), 'unavailable': _tr('web.screen.6')}.get(publication['state'])
    if ui is None:
        ui = '<code>' + escape_html(publication['build_id'][:12]) + _tr('web.screen.7') + escape_html(publication['customization_version']) + '</code>'
    text = (_tr('web.screen.8', p0=f'{origin}', p1=f"{(_tr('groups.build_group_view_text.3') if result['enabled'] else _tr('groups.build_group_view_text.4'))}", p2=f'{ui}', p3=f'{verification_status}', p4=f'{service}', p5=f"{(_tr('groups.build_group_view_text.3') if result['telegram_login']['enabled'] else _tr('groups.build_group_view_text.4'))}"))
    if result['error'] == 'web_setup_recovery_failed':
        text += _tr('web.screen.9')
    elif result['error']:
        text += _tr('web.screen.10')
    if publication.get('warning'):
        text += _tr('web.screen.11')
    release = result.get('ui_release')
    if release and release.get('rebuild_required'):
        text += (_tr('web.screen.12'))
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
        await callback.answer(_tr('web.switch_web.1'), show_alert=True)
        return
    await _screen(callback, state)
    await callback.answer(_tr('web.switch_web.2'))


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
    await callback.answer(_tr('web.switch_web.2'))
