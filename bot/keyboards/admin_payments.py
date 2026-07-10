from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button, state_pair_buttons


def _status_dot(enabled: bool) -> str:
    """Индикатор состояния для кнопок-строк."""
    return '🟢' if enabled else '⚪'

def payments_menu_kb(stars_enabled: bool, crypto_enabled: bool, cards_enabled: bool, qr_enabled: bool=False, monthly_reset_enabled: bool=False, demo_enabled: bool=False, wata_enabled: bool=False, platega_enabled: bool=False, cardlink_enabled: bool=False, notify_enabled: bool=False) -> InlineKeyboardMarkup:
    """
    Главное меню раздела оплат.

    Args:
        stars_enabled: Включены ли Telegram Stars
        crypto_enabled: Включены ли крипто-платежи
        cards_enabled: Включены ли TG payments (историческое внутреннее имя cards)
        qr_enabled: Включена ли прямая оплата ЮКасса
        monthly_reset_enabled: Включён ли ежемесячный автосброс трафика
        demo_enabled: Включена ли демо-оплата
        wata_enabled: Включена ли оплата через WATA
        platega_enabled: Включена ли оплата через Platega
        cardlink_enabled: Включена ли оплата через Cardlink
        notify_enabled: Включены ли уведомления об оплатах
    """
    builder = InlineKeyboardBuilder()
    stars_status = _status_dot(stars_enabled)
    crypto_status = _status_dot(crypto_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{stars_status} Telegram Stars', callback_data='admin_payments_toggle_stars'),
        InlineKeyboardButton(text=f'{crypto_status} Крипто-платежи', callback_data='admin_payments_toggle_crypto'),
    )
    cards_status = _status_dot(cards_enabled)
    qr_status = _status_dot(qr_enabled)
    builder.row(
        InlineKeyboardButton(text=f'{cards_status} TG payments', callback_data='admin_payments_cards'),
        InlineKeyboardButton(text=f'{qr_status} ЮКасса', callback_data='admin_payments_qr'),
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
        InlineKeyboardButton(text=f'{demo_status} Демо оплата (РФ)', callback_data='admin_payments_toggle_demo'),
    )
    notify_status = _status_dot(notify_enabled)
    builder.row(InlineKeyboardButton(text=f'{notify_status} Сообщать об оплатах', callback_data='admin_toggle_payment_notify'))
    reset_status = _status_dot(monthly_reset_enabled)
    builder.row(InlineKeyboardButton(text=f'{reset_status} Автосброс трафика 1-го числа', callback_data='admin_toggle_monthly_reset'))
    builder.row(InlineKeyboardButton(text='📂 Группы тарифов', callback_data='admin_groups'))
    builder.row(InlineKeyboardButton(text='📋 Тарифы', callback_data='admin_tariffs'))
    builder.row(InlineKeyboardButton(text='🎁 Пробная подписка', callback_data='admin_trial'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def wata_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Меню управления оплатой через WATA.

    Args:
        is_enabled: Включена ли WATA-оплата сейчас
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_wata_mgmt_set:1',
        'Выключено',
        'admin_wata_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🔑 Изменить JWT-токен', callback_data='admin_wata_mgmt_edit_token'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def platega_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Меню управления оплатой через Platega.

    Args:
        is_enabled: Включена ли Platega-оплата сейчас
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_platega_mgmt_set:1',
        'Выключено',
        'admin_platega_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🆔 Изменить Merchant ID', callback_data='admin_platega_mgmt_edit_merchant'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить Secret', callback_data='admin_platega_mgmt_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def cardlink_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Меню управления оплатой через Cardlink.

    Args:
        is_enabled: Включена ли Cardlink-оплата сейчас
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_cardlink_mgmt_set:1',
        'Выключено',
        'admin_cardlink_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🆔 Изменить Shop ID', callback_data='admin_cardlink_mgmt_edit_shop_id'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить API-токен', callback_data='admin_cardlink_mgmt_edit_api_token'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()

def crypto_setup_kb(step: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для шага настройки крипто-платежей.
    
    Args:
        step: Текущий шаг (1 = ссылка, 2 = ключ)
    """
    builder = InlineKeyboardBuilder()
    buttons = []
    if step > 1:
        buttons.append(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_crypto_setup_back'))
    buttons.append(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_payments'))
    builder.row(*buttons)
    return builder.as_markup()

def crypto_setup_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения настроек крипто."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Сохранить и включить', callback_data='admin_crypto_setup_save'))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_crypto_setup_back'), InlineKeyboardButton(text='❌ Отмена', callback_data='admin_payments'))
    return builder.as_markup()

def cards_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """Клавиатура управления TG payments."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_cards_mgmt_set:1',
        'Выключено',
        'admin_cards_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🔗 Изменить Provider Token', callback_data='admin_cards_mgmt_edit_token'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def qr_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """Клавиатура управления QR-оплатой ЮКасса."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_qr_mgmt_set:1',
        'Выключено',
        'admin_qr_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🏪 Изменить Shop ID', callback_data='admin_qr_edit_shop_id'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить Secret Key', callback_data='admin_qr_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()


def edit_crypto_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования крипто-настроек с навигацией.
    
    Args:
        current_param: Индекс текущего параметра
        total_params: Общее количество параметров
    """
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if current_param > 0:
        nav_buttons.append(InlineKeyboardButton(text='⬅️ Пред.', callback_data='admin_crypto_edit_prev'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    if current_param < total_params - 1:
        nav_buttons.append(InlineKeyboardButton(text='➡️ След.', callback_data='admin_crypto_edit_next'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text='✅ Готово', callback_data='admin_crypto_edit_done'))
    return builder.as_markup()

def crypto_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Меню управления крипто-платежами.
    
    Args:
        is_enabled: Включены ли крипто-платежи сейчас
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        is_enabled,
        'Включено',
        'admin_crypto_mgmt_set:1',
        'Выключено',
        'admin_crypto_mgmt_set:0',
    ))
    builder.row(InlineKeyboardButton(text='🔗 Изменить ссылку на товар', callback_data='admin_crypto_mgmt_edit_url'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить секретный ключ', callback_data='admin_crypto_mgmt_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()
