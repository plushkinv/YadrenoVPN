from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button, state_pair_buttons

def bot_settings_kb(current_mode: str = 'subscription') -> InlineKeyboardMarkup:
    """
    Клавиатура раздела 'Настройки бота'.

    Args:
        current_mode: Текущий режим работы бота ('subscription' | 'key').
                      Влияет только на лейбл кнопки переключения режима.
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
    builder.row(InlineKeyboardButton(text='🧩 Расширения', callback_data='admin_extensions_diagnostics'))
    builder.row(InlineKeyboardButton(text='🛑 Остановить бота', callback_data='admin_stop_bot'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def bot_mode_toggle_confirm_kb(target_mode: str) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения переключения режима бота.

    Args:
        target_mode: Режим, на который переключаемся ('subscription' | 'key')
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, переключить',
                             callback_data=f'admin_set_bot_mode:{target_mode}'),
        InlineKeyboardButton(text='❌ Отмена', callback_data='admin_bot_settings'),
    )
    return builder.as_markup()


def extensions_diagnostics_kb() -> InlineKeyboardMarkup:
    """Клавиатура экрана диагностики пользовательских расширений."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_extensions_diagnostics'))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()

def trial_settings_kb(enabled: bool, tariff_name: Optional[str]=None) -> InlineKeyboardMarkup:
    """
    Клавиатура управления пробной подпиской.
    
    Args:
        enabled: Включена ли пробная подписка
        tariff_name: Название выбранного тарифа или None
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
    Клавиатура выбора тарифа для пробной подписки.
    
    Отображает все тарифы кроме Admin Tariff.
    
    Args:
        tariffs: Список всех тарифов (включая неактивные)
        selected_id: ID текущего выбранного тарифа
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
    """Клавиатура отмены редактирования текста пробной подписки."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_trial'))
    return builder.as_markup()

def referral_main_kb(enabled: bool, reward_type: str, levels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Главное меню реферальной системы.
    
    Args:
        enabled: Включена ли система
        reward_type: Тип начисления ('days' или 'balance')
        levels: Список уровней [{level_number, percent, enabled}, ...]
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
    Клавиатура редактирования уровня.
    
    Args:
        level_num: Номер уровня (1-3)
        percent: Текущий процент
        enabled: Включён ли уровень
    """
    builder = InlineKeyboardBuilder()
    toggle_text = '🟢 Выключить' if enabled else '⚪ Включить'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f'admin_referral_level_toggle:{level_num}'))
    builder.row(InlineKeyboardButton(text=f'📊 Процент: {percent}%', callback_data=f'admin_referral_level_percent:{level_num}'))
    builder.row(back_button('admin_referral'), home_button())
    return builder.as_markup()

def referral_back_kb() -> InlineKeyboardMarkup:
    """Клавиатура возврата в меню реферальной системы."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_referral'))
    return builder.as_markup()
