from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

def payments_menu_kb(stars_enabled: bool, crypto_enabled: bool, cards_enabled: bool, qr_enabled: bool=False, monthly_reset_enabled: bool=False, demo_enabled: bool=False, wata_enabled: bool=False, platega_enabled: bool=False, cardlink_enabled: bool=False, notify_enabled: bool=False) -> InlineKeyboardMarkup:
    """
    Главное меню раздела оплат.

    Args:
        stars_enabled: Включены ли Telegram Stars
        crypto_enabled: Включены ли крипто-платежи
        cards_enabled: Включена ли оплата картами (ЮКасса Telegram Payments)
        qr_enabled: Включена ли прямая QR-оплата ЮКасса
        monthly_reset_enabled: Включён ли ежемесячный автосброс трафика
        demo_enabled: Включена ли демо-оплата
        wata_enabled: Включена ли оплата через WATA
        platega_enabled: Включена ли оплата через Platega
        cardlink_enabled: Включена ли оплата через Cardlink
        notify_enabled: Включены ли уведомления об оплатах
    """
    builder = InlineKeyboardBuilder()
    stars_status = '✅' if stars_enabled else '❌'
    crypto_status = '✅' if crypto_enabled else '❌'
    builder.row(
        InlineKeyboardButton(text=f'⭐ Telegram Stars: {stars_status}', callback_data='admin_payments_toggle_stars'),
        InlineKeyboardButton(text=f'💰 Крипто-платежи: {crypto_status}', callback_data='admin_payments_toggle_crypto'),
    )
    cards_status = '✅' if cards_enabled else '❌'
    qr_status = '✅' if qr_enabled else '❌'
    builder.row(
        InlineKeyboardButton(text=f'📱 TG payments (ЮКасса): {cards_status}', callback_data='admin_payments_cards'),
        InlineKeyboardButton(text=f'💳 ЮКасса (QR/СБП): {qr_status}', callback_data='admin_payments_qr'),
    )
    wata_status = '✅' if wata_enabled else '❌'
    platega_status = '✅' if platega_enabled else '❌'
    builder.row(
        InlineKeyboardButton(text=f'🌊 WATA (Карта/СБП): {wata_status}', callback_data='admin_payments_wata'),
        InlineKeyboardButton(text=f'💸 Platega (СБП): {platega_status}', callback_data='admin_payments_platega'),
    )
    cardlink_status = '✅' if cardlink_enabled else '❌'
    demo_status = '✅' if demo_enabled else '❌'
    builder.row(
        InlineKeyboardButton(text=f'🔗 Cardlink (Карта/СБП): {cardlink_status}', callback_data='admin_payments_cardlink'),
        InlineKeyboardButton(text=f'💳 Демо оплата (РФ): {demo_status}', callback_data='admin_payments_toggle_demo'),
    )
    notify_status = '✅' if notify_enabled else '❌'
    builder.row(InlineKeyboardButton(text=f'🔔 Сообщать об оплатах: {notify_status}', callback_data='admin_toggle_payment_notify'))
    reset_status = '✅' if monthly_reset_enabled else '❌'
    builder.row(InlineKeyboardButton(text=f'🔄 Автосброс трафика 1-го числа: {reset_status}', callback_data='admin_toggle_monthly_reset'))
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
    toggle_text = 'Выключить 🔴' if is_enabled else 'Включить 🟢'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data='admin_wata_mgmt_toggle'))
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
    toggle_text = 'Выключить 🔴' if is_enabled else 'Включить 🟢'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data='admin_platega_mgmt_toggle'))
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
    toggle_text = 'Выключить 🔴' if is_enabled else 'Включить 🟢'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data='admin_cardlink_mgmt_toggle'))
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
    """Клавиатура управления оплатой картами."""
    builder = InlineKeyboardBuilder()
    toggle_text = 'Выключить 🔴' if is_enabled else 'Включить 🟢'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data='admin_cards_mgmt_toggle'))
    builder.row(InlineKeyboardButton(text='🔗 Изменить Provider Token', callback_data='admin_cards_mgmt_edit_token'))
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
    status_text = '🟢 Выключить' if is_enabled else '⚪ Включить'
    builder.row(InlineKeyboardButton(text=status_text, callback_data='admin_crypto_mgmt_toggle'))
    builder.row(InlineKeyboardButton(text='🔗 Изменить ссылку на товар', callback_data='admin_crypto_mgmt_edit_url'))
    builder.row(InlineKeyboardButton(text='🔐 Изменить секретный ключ', callback_data='admin_crypto_mgmt_edit_secret'))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()
