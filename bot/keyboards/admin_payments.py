
from localization import text as _tr
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button, state_pair_buttons
from core.payment_provider_specs import INTERNATIONAL_PROVIDERS, REGIONAL_CURRENCY_EXPONENTS
from database.requests import is_payment_admin_visible, is_gateway_enabled, get_cards_currency, get_setting


def _status_dot(enabled: bool) -> str:
    """Status indicator for row buttons."""
    return '🟢' if enabled else '⚪'

def payments_menu_kb(stars_enabled: bool, crypto_enabled: bool, cards_enabled: bool, qr_enabled: bool=False, demo_enabled: bool=False, wata_enabled: bool=False, platega_enabled: bool=False, cardlink_enabled: bool=False, notify_enabled: bool=False, cryptobot_enabled: bool=False) -> InlineKeyboardMarkup:
    """
    Main menu of the payments section.

    Args:
        stars_enabled: Is Telegram Stars enabled?
        crypto_enabled: Whether legacy Ya.Seller payments are enabled
        cryptobot_enabled: Whether Crypto Pay is enabled
        cards_enabled: Whether TG payments are enabled (historical internal name cards)
        qr_enabled: Is YuKass direct payment enabled?
        demo_enabled: Is demo payment enabled?
        wata_enabled: Is payment via WATA enabled?
        platega_enabled: Is payment via Platega enabled?
        cardlink_enabled: Whether payment via Cardlink is enabled
        notify_enabled: Whether payment notifications are enabled
    """
    builder = InlineKeyboardBuilder()
    stars_status = _status_dot(stars_enabled)
    cryptobot_status = _status_dot(cryptobot_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{stars_status} Telegram Stars', callback_data='admin_payments_toggle_stars'),
        InlineKeyboardButton(text=f'{cryptobot_status} Crypto Pay', callback_data='admin_payments_cryptobot'),
    )
    builder.row(InlineKeyboardButton(
        text=f'{_status_dot(crypto_enabled)} Ya.Seller', callback_data='admin_payments_toggle_crypto',
    ))
    cards_status = _status_dot(cards_enabled)
    qr_status = _status_dot(qr_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{cards_status} TG payments', callback_data='admin_payments_cards'),
        InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.1', p0=f'{qr_status}'), callback_data='admin_payments_qr'),
    )
    wata_status = _status_dot(wata_enabled)
    platega_status = _status_dot(platega_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{wata_status} WATA', callback_data='admin_payments_wata'),
        InlineKeyboardButton(text=f'{platega_status} Platega', callback_data='admin_payments_platega'),
    )
    cardlink_status = _status_dot(cardlink_enabled)
    demo_status = _status_dot(demo_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{cardlink_status} Cardlink', callback_data='admin_payments_cardlink'),
        InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.2', p0=f'{demo_status}'), callback_data='admin_payments_toggle_demo'),
    )
    method_callbacks = {
        'admin_payments_toggle_stars': 'stars', 'admin_payments_cryptobot': 'cryptobot',
        'admin_payments_toggle_crypto': 'crypto', 'admin_payments_cards': 'cards',
        'admin_payments_qr': 'yookassa_qr', 'admin_payments_wata': 'wata',
        'admin_payments_platega': 'platega', 'admin_payments_cardlink': 'cardlink',
        'admin_payments_toggle_demo': 'demo',
    }
    visible_rows = [[button for button in row
                     if is_payment_admin_visible(method_callbacks[button.callback_data])]
                    for row in builder.export()]
    builder = InlineKeyboardBuilder(markup=[row for row in visible_rows if row])
    for pid, (title, _, _) in INTERNATIONAL_PROVIDERS.items():
        if is_payment_admin_visible(pid):
            builder.row(InlineKeyboardButton(text=f'{_status_dot(is_gateway_enabled(pid))} {title}',
                                            callback_data=f'admin_gateway:{pid}'))
    notify_status = _status_dot(notify_enabled)
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.3', p0=f'{notify_status}'), callback_data='admin_toggle_payment_notify'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.4'), callback_data='admin_groups'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.5'), callback_data='admin_tariffs'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.6'), callback_data='admin_payment_rates'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.payments_menu_kb.7'), callback_data='admin_trial'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def payment_rates_kb(base_currency: str = 'RUB') -> InlineKeyboardMarkup:
    """Administrator controls for the base currency and fixed conversion rates."""
    base = str(base_currency or 'RUB').upper()
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=_tr('admin_payments.payment_rates_kb.1'),
        callback_data='admin_payment_rate_edit:USDT',
    ))
    builder.row(InlineKeyboardButton(
        text=_tr('admin_payments.payment_rates_kb.2'),
        callback_data='admin_payment_rate_edit:XTR',
    ))
    if base == 'USD':
        builder.row(InlineKeyboardButton(
            text=_tr('admin_payments.payment_rates_kb.3'),
            callback_data='admin_payment_rate_edit:RUB',
        ))
    else:
        builder.row(InlineKeyboardButton(
            text=_tr('admin_payments.payment_rates_kb.4'),
            callback_data='admin_payment_rate_edit:USD',
        ))
    target = 'USD' if base == 'RUB' else 'RUB'
    for currency in REGIONAL_CURRENCY_EXPONENTS:
        builder.row(InlineKeyboardButton(text=f'💱 {currency}', callback_data=f'admin_payment_rate_edit:{currency}'))
    builder.row(InlineKeyboardButton(
        text=_tr('admin_payments.payment_rates_kb.5', p0=f'{target}'),
        callback_data=f'admin_base_currency_select:{target}',
    ))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def base_currency_switch_input_kb() -> InlineKeyboardMarkup:
    """Navigation while the administrator enters a transition rate."""
    builder = InlineKeyboardBuilder()
    builder.row(back_button('admin_payment_rates'), home_button())
    return builder.as_markup()


def base_currency_switch_confirm_kb(target_currency: str) -> InlineKeyboardMarkup:
    """Explicit confirmation for the destructive accounting conversion."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=_tr('admin_payments.base_currency_switch_confirm_kb.1', p0=f'{str(target_currency).upper()}'),
        callback_data='admin_base_currency_confirm',
    ))
    builder.row(back_button('admin_payment_rates'), home_button())
    return builder.as_markup()


def wata_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Payment management menu via WATA.

    Args:
        is_enabled: Is WATA payment enabled now?
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_wata_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_wata_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.wata_management_kb.3'), callback_data='admin_wata_mgmt_edit_token'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def platega_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Payment management menu via Platega.

    Args:
        is_enabled: Is Platega payment enabled now?
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_platega_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_platega_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.platega_management_kb.1'), callback_data='admin_platega_mgmt_edit_merchant'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.platega_management_kb.2'), callback_data='admin_platega_mgmt_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def cardlink_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Payment management menu via Cardlink.

    Args:
        is_enabled: Is Cardlink payment enabled now?
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_cardlink_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_cardlink_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.cardlink_management_kb.1'), callback_data='admin_cardlink_mgmt_edit_shop_id'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.cardlink_management_kb.2'), callback_data='admin_cardlink_mgmt_edit_api_token'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def cryptobot_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """Administrator controls for the built-in Crypto Pay provider."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_cryptobot_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_cryptobot_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(
        text=_tr('admin_payments.cardlink_management_kb.2'),
        callback_data='admin_cryptobot_mgmt_edit_token',
    ))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()

def crypto_setup_kb(step: int) -> InlineKeyboardMarkup:
    """
    Keyboard for crypto payment setup step.
    
    Args:
        step: Current step (1 = link, 2 = key)
    """
    builder = InlineKeyboardBuilder()
    buttons = []
    if step > 1:
        buttons.append(InlineKeyboardButton(text=_tr('system.extension_settings_kb.7'), callback_data='admin_crypto_setup_back'))
    buttons.append(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_payments'))
    builder.row(*buttons)
    return builder.as_markup()

def crypto_setup_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard for confirming crypto settings."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.crypto_setup_confirm_kb.1'), callback_data='admin_crypto_setup_save'))
    builder.row(InlineKeyboardButton(text=_tr('system.extension_settings_kb.7'), callback_data='admin_crypto_setup_back'), InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_payments'))
    return builder.as_markup()

def cards_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """TG payments control keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_cards_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_cards_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.cards_management_kb.1'), callback_data='admin_cards_mgmt_edit_token'))
    for currency in ('RUB', 'USD'):
        builder.row(InlineKeyboardButton(
            text=f'{"✅" if get_cards_currency() == currency else "💱"} {currency}',
            callback_data=f'admin_cards_currency:{currency}'))
    if get_cards_currency() == 'RUB':
        enabled = get_setting('cards_yookassa_receipt', '1') == '1'
        builder.row(*state_pair_buttons(enabled, _tr('gateway.receipt_on'), 'admin_cards_receipt:1',
                                       _tr('gateway.receipt_off'), 'admin_cards_receipt:0'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def qr_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """YuKassa QR payment control keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_qr_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_qr_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.qr_management_kb.1'), callback_data='admin_qr_edit_shop_id'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.qr_management_kb.2'), callback_data='admin_qr_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def edit_crypto_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Keyboard for editing crypto settings with navigation.
    
    Args:
        current_param: Index of the current parameter
        total_params: Total number of parameters
    """
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if current_param > 0:
        nav_buttons.append(InlineKeyboardButton(text=_tr('admin_payments.edit_crypto_kb.1'), callback_data='admin_crypto_edit_prev'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    if current_param < total_params - 1:
        nav_buttons.append(InlineKeyboardButton(text=_tr('admin_payments.edit_crypto_kb.2'), callback_data='admin_crypto_edit_next'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.edit_crypto_kb.3'), callback_data='admin_crypto_edit_done'))
    return builder.as_markup()

def crypto_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Menu for managing crypto payments.
    
    Args:
        is_enabled: Whether crypto payments are currently enabled
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_crypto_mgmt_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_crypto_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.crypto_management_kb.1'), callback_data='admin_crypto_mgmt_edit_url'))
    builder.row(InlineKeyboardButton(text=_tr('admin_payments.crypto_management_kb.2'), callback_data='admin_crypto_mgmt_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()
