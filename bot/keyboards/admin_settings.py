from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button, state_pair_buttons

def bot_settings_kb(current_mode: str = 'subscription') -> InlineKeyboardMarkup:
    """
    Keyboard of the 'Bot Settings' section.

    Args:
        current_mode: The bot's current operating mode ('subscription' | 'key').
                      Only affects the label of the mode switch button.
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        current_mode == 'subscription',
        'Подписка',
        'admin_select_bot_mode:subscription',
        'Ключи',
        'admin_select_bot_mode:key',
        right_active_emoji='🟢',
    ))
    builder.row(InlineKeyboardButton(text='🔄 Обновления', callback_data='admin_update_bot'))
    builder.row(InlineKeyboardButton(text='✏️ Изменить тексты', callback_data='admin_edit_texts'))
    builder.row(InlineKeyboardButton(text='📥 Скачать логи', callback_data='admin_logs_menu'))
    builder.row(InlineKeyboardButton(text='🛑 Остановить бота', callback_data='admin_stop_bot'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def bot_mode_toggle_confirm_kb(target_mode: str) -> InlineKeyboardMarkup:
    """
    Confirmation keyboard for switching bot mode.

    Args:
        target_mode: Mode to switch to ('subscription' | 'key')
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, переключить',
                             callback_data=f'admin_set_bot_mode:{target_mode}'),
        InlineKeyboardButton(text='❌ Отмена', callback_data='admin_bot_settings'),
    )
    return builder.as_markup()


def extensions_diagnostics_kb(setting_buttons: Optional[List[Dict[str, str]]] = None) -> InlineKeyboardMarkup:
    """Custom extension diagnostic screen keyboard."""
    builder = InlineKeyboardBuilder()
    for button in setting_buttons or []:
        text = str(button.get('text') or '').strip()
        callback_data = str(button.get('callback_data') or '').strip()
        if text and callback_data:
            builder.row(InlineKeyboardButton(text=text, callback_data=callback_data))
    builder.row(InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_extensions_diagnostics'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def custom_reset_preview_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset preview keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🧹 Сбросить кастомизацию', callback_data='admin_custom_reset_confirm'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_custom_reset_cancel'))
    return builder.as_markup()


def custom_reset_cancel_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset phrase input keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_custom_reset_cancel'))
    return builder.as_markup()


def custom_reset_done_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset completion keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='⚙️ Настройки бота', callback_data='admin_bot_settings'))
    builder.row(home_button())
    return builder.as_markup()


def trial_settings_kb(enabled: bool, tariff_name: Optional[str]=None) -> InlineKeyboardMarkup:
    """
    Trial subscription control keyboard.
    
    Args:
        enabled: Whether trial subscription is enabled
        tariff_name: Name of the selected tariff or None
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        'Включено',
        'admin_trial_set:1',
        'Выключено',
        'admin_trial_set:0',
    ))
    builder.row(InlineKeyboardButton(text='✏️ Изменить текст', callback_data='admin_trial_edit_text'))
    tariff_label = tariff_name if tariff_name else 'не задан'
    builder.row(InlineKeyboardButton(text=f'📋 Тариф: {tariff_label}', callback_data='admin_trial_select_tariff'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()

def trial_tariff_select_kb(tariffs: List[Dict[str, Any]], selected_id: Optional[int]=None) -> InlineKeyboardMarkup:
    """
    Keyboard for selecting a tariff for a trial subscription.
    
    Displays all tariffs except Admin Tariff.
    
    Args:
        tariffs: List of all tariffs (including inactive ones)
        selected_id: ID of the currently selected tariff
    """
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if tariff.get('name') == 'Admin Tariff':
            continue
        status = '🟢' if tariff.get('is_active') else '⚪'
        is_selected = tariff['id'] == selected_id
        selected_suffix = ' — выбрано' if is_selected else ''
        builder.row(InlineKeyboardButton(text=f"{status} {tariff['name']} ({tariff['duration_days']} дн.){selected_suffix}", callback_data=f"admin_trial_set_tariff:{tariff['id']}"))
    builder.row(back_button('admin_trial'), home_button())
    return builder.as_markup()

def trial_edit_text_cancel_kb() -> InlineKeyboardMarkup:
    """Keyboard for undoing trial subscription text editing."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_trial'))
    return builder.as_markup()

def referral_main_kb(enabled: bool, reward_type: str, levels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Main menu of the referral system.
    
    Args:
        enabled: Whether the system is enabled
        reward_type: Reward type ('days' or 'balance')
        levels: List of levels [{level_number, percent, enabled}, ...]
    """
    builder = InlineKeyboardBuilder()
    toggle_text = '🟢 Выключить' if enabled else '⚪ Включить'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data='admin_referral_toggle'))
    if reward_type == 'days':
        type_text = '📅 Режим: Дни к ключу'
    else:
        type_text = '💰 Режим: На баланс'
    builder.row(InlineKeyboardButton(text=type_text, callback_data='admin_referral_toggle_type'))
    for level in levels:
        level_num = level['level_number']
        percent = level['percent']
        is_enabled = level['enabled']
        status = '🟢' if is_enabled else '⚪'
        builder.row(InlineKeyboardButton(text=f'{status} Уровень {level_num}: {percent}%', callback_data=f'admin_referral_level:{level_num}'))
    builder.row(InlineKeyboardButton(text='📝 Реферальная страница', callback_data='admin_referral_conditions'))
    builder.row(back_button('admin_marketing'), home_button())
    return builder.as_markup()

def referral_level_kb(level_num: int, percent: int, enabled: bool) -> InlineKeyboardMarkup:
    """
    Level editing keyboard.
    
    Args:
        level_num: Level number (1-3)
        percent: Current percentage
        enabled: Whether the level is enabled
    """
    builder = InlineKeyboardBuilder()
    toggle_text = '🟢 Выключить' if enabled else '⚪ Включить'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f'admin_referral_level_toggle:{level_num}'))
    builder.row(InlineKeyboardButton(text=f'📊 Процент: {percent}%', callback_data=f'admin_referral_level_percent:{level_num}'))
    builder.row(back_button('admin_referral'), home_button())
    return builder.as_markup()

def referral_back_kb() -> InlineKeyboardMarkup:
    """Keyboard to return to the referral system menu."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_referral'))
    return builder.as_markup()
