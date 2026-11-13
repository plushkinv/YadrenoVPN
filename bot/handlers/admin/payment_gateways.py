"""Single-card administrator settings for hosted providers and generic TG Payments."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from localization import text as _tr
from core.payment_provider_specs import (
    INTERNATIONAL_PROVIDERS, MYFATOORAH_CURRENCIES, PROVIDER_FIELD_CHOICES,
)
from database.requests import (
    get_gateway_settings, is_gateway_enabled, is_gateway_configured,
    get_setting, set_setting, get_base_currency, get_currency_rate,
)
from bot.keyboards.admin_payment_gateways import gateway_management_kb, gateway_input_kb
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import render_admin_dialog, render_admin_dialog_from_input
from bot.utils.text import escape_html, get_message_text_for_storage
from bot.services.money import payment_type_currency

router = Router()
_SECRET_FIELDS = {'api_key', 'password', 'secret_key', 'client_secret', 'server_key', 'api_token'}


def _management_text(pid, result=''):
    title, icon, fields = INTERNATIONAL_PROVIDERS[pid]
    values = get_gateway_settings(pid)
    lines = []
    for field in fields:
        value = values[field]
        rendered = ('✅' if value else '—') if field in _SECRET_FIELDS else escape_html(value or '—')
        lines.append(f'{_tr("gateway.field." + field)}: <code>{rendered}</code>')
    enabled = is_gateway_enabled(pid)
    status = _tr('coupons.render_purchase_settings.1' if enabled else 'coupons.render_purchase_settings.2')
    return _tr('gateway.management', provider=title, icon=icon, status=status,
               fields='\n'.join(lines), result=result, help=_tr('gateway.help.' + pid))


def _prompt(pid, field, error=''):
    return _tr('gateway.input', provider=INTERNATIONAL_PROVIDERS[pid][0],
               field=_tr('gateway.field.' + field), error=error)


def _validate(pid, field, value):
    if not value or len(value) > 16384 or any(ord(c) < 32 for c in value):
        return False
    choices = PROVIDER_FIELD_CHOICES.get((pid, field))
    if choices and value not in choices:
        return False
    if field == 'profile_id' and (not value.isascii() or not value.isdecimal() or int(value) <= 0):
        return False
    if field == 'email' and ('@' not in value or ' ' in value):
        return False
    return True


def _save(pid, field, value):
    key = f'{pid}_{field}'
    changed = get_setting(key, '') != value
    set_setting(key, value)
    if pid == 'myfatoorah' and field == 'region':
        set_setting('myfatoorah_currency', MYFATOORAH_CURRENCIES[value])
    return _tr('gateway.saved' if changed else 'gateway.unchanged')


@router.callback_query(F.data.startswith('admin_gateway:'))
async def show_gateway(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    pid = callback.data.split(':')[1]
    if pid not in INTERNATIONAL_PROVIDERS:
        return
    await state.set_state(AdminStates.payments_menu)
    await render_admin_dialog(callback.message, state, _management_text(pid),
                              reply_markup=gateway_management_kb(pid, is_gateway_enabled(pid)))
    await callback.answer()


@router.callback_query(F.data.startswith('admin_gateway_set:'))
async def set_gateway_enabled(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    _, pid, value = callback.data.split(':')
    if pid not in INTERNATIONAL_PROVIDERS or value not in {'0', '1'}:
        return
    if value == '1':
        ready = is_gateway_configured(pid, require_enabled=False)
        if ready:
            ready = get_currency_rate(payment_type_currency(pid), base_currency=get_base_currency()) is not None
        if not ready:
            await callback.answer(_tr('gateway.required'), show_alert=True)
            return
    set_setting(f'{pid}_enabled', value)
    await state.set_state(AdminStates.payments_menu)
    await render_admin_dialog(callback.message, state, _management_text(pid),
                              reply_markup=gateway_management_kb(pid, value == '1'))
    await callback.answer()


@router.callback_query(F.data.startswith('admin_gateway_edit:'))
async def edit_gateway(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    _, pid, field = callback.data.split(':')
    if pid not in INTERNATIONAL_PROVIDERS or field not in INTERNATIONAL_PROVIDERS[pid][2]:
        return
    await state.update_data(gateway_provider=pid, gateway_field=field)
    await render_admin_dialog(callback.message, state, _prompt(pid, field), reply_markup=gateway_input_kb(pid, field))
    await state.set_state(AdminStates.gateway_setting_value)
    await callback.answer()


@router.message(AdminStates.gateway_setting_value)
async def gateway_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid, field = data['gateway_provider'], data['gateway_field']
    value = get_message_text_for_storage(message, 'plain')
    if field != 'password':
        value = value.strip()
    if not _validate(pid, field, value):
        await render_admin_dialog_from_input(message, state, _prompt(pid, field, _tr('gateway.invalid')),
                                              reply_markup=gateway_input_kb(pid, field))
        return
    result = _save(pid, field, value)
    await render_admin_dialog_from_input(message, state, _management_text(pid, result),
                                         reply_markup=gateway_management_kb(pid, is_gateway_enabled(pid)))
    await state.set_state(AdminStates.payments_menu)


@router.callback_query(AdminStates.gateway_setting_value, F.data.startswith('admin_gateway_choice:'))
async def gateway_choice(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    _, pid, field, value = callback.data.split(':')
    data = await state.get_data()
    if (pid, field) != (data.get('gateway_provider'), data.get('gateway_field')) or not _validate(pid, field, value):
        return
    result = _save(pid, field, value)
    await render_admin_dialog(callback.message, state, _management_text(pid, result),
                              reply_markup=gateway_management_kb(pid, is_gateway_enabled(pid)))
    await state.set_state(AdminStates.payments_menu)
    await callback.answer()


@router.callback_query(F.data.startswith('admin_cards_currency:') | F.data.startswith('admin_cards_receipt:'))
async def cards_options(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    action, value = callback.data.split(':')
    if action == 'admin_cards_currency' and value in {'RUB', 'USD'}:
        set_setting('cards_currency', value)
    elif action == 'admin_cards_receipt' and value in {'0', '1'}:
        set_setting('cards_yookassa_receipt', value)
    else:
        return
    from .payments import show_cards_management_menu
    await show_cards_management_menu(callback, state)
