"""Configuration keyboards for built-in hosted providers."""
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from localization import text as _tr
from core.payment_provider_specs import INTERNATIONAL_PROVIDERS, PROVIDER_FIELD_CHOICES
from .admin_misc import back_button, home_button, state_pair_buttons


def gateway_management_kb(provider_id, enabled):
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(enabled, _tr('admin_payments.wata_management_kb.1'),
        f'admin_gateway_set:{provider_id}:1', _tr('admin_payments.wata_management_kb.2'),
        f'admin_gateway_set:{provider_id}:0'))
    for field in INTERNATIONAL_PROVIDERS[provider_id][2]:
        builder.row(InlineKeyboardButton(text=_tr('gateway.edit', field=_tr('gateway.field.' + field)),
                                        callback_data=f'admin_gateway_edit:{provider_id}:{field}'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.6'), callback_data='admin_payment_rates'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def gateway_input_kb(provider_id, field):
    builder = InlineKeyboardBuilder()
    for value in PROVIDER_FIELD_CHOICES.get((provider_id, field), ()):
        builder.row(InlineKeyboardButton(text=f'💱 {value}',
                                        callback_data=f'admin_gateway_choice:{provider_id}:{field}:{value}'))
    builder.row(back_button(f'admin_gateway:{provider_id}'), home_button())
    return builder.as_markup()
