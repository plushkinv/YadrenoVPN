from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from database.requests import is_yadreno_admin_customization_enabled

def state_pair_buttons(
    is_left_active: bool,
    left_text: str,
    left_callback: str,
    right_text: str,
    right_callback: str,
    *,
    left_active_emoji: str = '🟢',
    right_active_emoji: str = '🔴',
):
    """Returns two status buttons with an active and an inactive indicator."""
    left_emoji = left_active_emoji if is_left_active else '⚪'
    right_emoji = '⚪' if is_left_active else right_active_emoji
    return (
        InlineKeyboardButton(text=f'{left_emoji} {left_text}', callback_data=left_callback),
        InlineKeyboardButton(text=f'{right_emoji} {right_text}', callback_data=right_callback),
    )

def back_button(callback: str='back') -> InlineKeyboardButton:
    """'Back' button."""
    return InlineKeyboardButton(text='⬅️ Назад', callback_data=callback)

def home_button() -> InlineKeyboardButton:
    """'Home' button."""
    return InlineKeyboardButton(text='🈴 На главную', callback_data='start')

def cancel_button() -> InlineKeyboardButton:
    """'Cancel' button."""
    return InlineKeyboardButton(text='❌ Отмена', callback_data='admin_servers')

def cancel_kb(callback_data: str) -> InlineKeyboardMarkup:
    """Keyboard with 'Cancel' button."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=callback_data))
    return builder.as_markup()

def back_and_home_kb(back_callback: str='back') -> InlineKeyboardMarkup:
    """Keyboard with 'Back' and 'Home' buttons."""
    builder = InlineKeyboardBuilder()
    builder.row(back_button(back_callback), home_button())
    return builder.as_markup()

def home_only_kb() -> InlineKeyboardMarkup:
    """Keyboard with only 'Home' button."""
    builder = InlineKeyboardBuilder()
    builder.row(home_button())
    return builder.as_markup()

def admin_main_menu_kb() -> InlineKeyboardMarkup:
    """Main menu of the admin panel."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🖥️ Сервера', callback_data='admin_servers'),
        InlineKeyboardButton(text='💳 Оплаты', callback_data='admin_payments')
    )
    builder.row(
        InlineKeyboardButton(text='👥 Пользователи', callback_data='admin_users'),
        InlineKeyboardButton(text='📣 Маркетинг', callback_data='admin_marketing')
    )
    builder.row(
        InlineKeyboardButton(text='⚙️ Настройки бота', callback_data='admin_bot_settings'),
        InlineKeyboardButton(text='🧩 Расширения', callback_data='admin_extensions_diagnostics')
    )
    builder.row(
        InlineKeyboardButton(
            text='🤖 Yadreno Admin',
            callback_data='admin_yadreno',
            style='primary',
        )
    )
    if is_yadreno_admin_customization_enabled():
        builder.row(
            InlineKeyboardButton(
                text='🛠 Кастомизация YadrenoVPN',
                callback_data='admin_yadreno_customization',
                style='primary',
            )
        )
    builder.row(InlineKeyboardButton(text='🤍 Поддержка автора', callback_data='admin_author_support', style='success'))
    builder.row(home_button())
    return builder.as_markup()

def marketing_menu_kb() -> InlineKeyboardMarkup:
    """Menu of marketing tools."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='📢 Рассылка', callback_data='admin_broadcast'))
    builder.row(InlineKeyboardButton(text='🔗 Реферальная система', callback_data='admin_referral'))
    builder.row(InlineKeyboardButton(text='🎟 Промокоды', callback_data='admin_promocodes'))
    builder.row(InlineKeyboardButton(text='🎫 Купоны', callback_data='admin_coupons'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()

def admin_logs_menu_kb() -> InlineKeyboardMarkup:
    """Log download menu."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='📄 Полный лог', callback_data='admin_download_log_full'), InlineKeyboardButton(text='⚠️ Ошибки', callback_data='admin_download_log_errors'))
    builder.row(InlineKeyboardButton(text='📤 Отправить в Ядрёно Админ', callback_data='admin_send_log_to_yadreno'))
    builder.row(InlineKeyboardButton(text='🧹 Очистить логи', callback_data='admin_clear_logs_confirm'))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()

def stop_bot_confirm_kb() -> InlineKeyboardMarkup:
    """Bot stop confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Точно остановить', callback_data='admin_stop_bot_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_bot_settings'))
    return builder.as_markup()

def force_overwrite_confirm_kb() -> InlineKeyboardMarkup:
    """Forced overwrite confirmation keypad."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Да, перезаписать', callback_data='admin_force_overwrite_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Нет, отмена', callback_data='admin_bot_settings'))
    return builder.as_markup()

def update_confirm_kb(has_updates: bool=True, has_blocking: bool=False, is_beta_only: bool=False) -> InlineKeyboardMarkup:
    """Bot update confirmation keyboard.
    
    Args:
        has_updates: Are there any updates available?
        has_blocking: Whether there is a blocking commit among the updates
        is_beta_only: Whether all available updates are beta versions
    """
    builder = InlineKeyboardBuilder()
    if has_updates:
        if has_blocking:
            button_text = '⚠️ Обновить до блокирующего коммита'
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))
        elif is_beta_only:
            button_text = '🧪 Накатить бета версию'
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))
        else:
            button_text = '✅ Обновить и перезапустить'
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))
    
    builder.row(InlineKeyboardButton(text='⚠️ Принудительно перезаписать', callback_data='admin_force_overwrite_confirm'))
    
    if has_updates:
        builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_bot_settings'))
    else:
        builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_bot_settings'))
    return builder.as_markup()

def author_support_kb() -> InlineKeyboardMarkup:
    """Keyboard for author support screen."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💳 Карты РФ', url='https://yoomoney.ru/fundraise/1GJ73GGRJBC.260318'),
        InlineKeyboardButton(text='💰 USDT', url='https://t.me/Ya_SellerBot?start=item-40')
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()
