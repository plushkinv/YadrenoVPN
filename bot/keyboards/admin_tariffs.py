from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

def tariffs_list_kb(tariffs: List[Dict[str, Any]], include_hidden: bool=True) -> InlineKeyboardMarkup:
    """
    Клавиатура списка тарифов.
    При наличии >1 группы тарифы визуально разделяются заголовками.
    
    Args:
        tariffs: Список тарифов из БД
        include_hidden: Показывать скрытые тарифы
    """
    from database.requests import get_groups_count, get_all_groups
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='➕ Добавить тариф', callback_data='admin_tariff_add'))
    groups_count = get_groups_count()
    if groups_count > 1:
        groups = {g['id']: g['name'] for g in get_all_groups()}
        grouped_tariffs = {}
        for t in tariffs:
            g_id = t.get('group_id', 1)
            if g_id not in grouped_tariffs:
                grouped_tariffs[g_id] = []
            grouped_tariffs[g_id].append(t)
        for (g_id, t_list) in grouped_tariffs.items():
            g_name = groups.get(g_id, 'Основная')
            builder.row(InlineKeyboardButton(text=f'📂⬇ {g_name}', callback_data='noop'))
            for tariff in t_list:
                status_emoji = '🟢' if tariff.get('is_active') else '⚪'
                price = tariff['price_cents'] / 100
                price_str = f'{price:g}'.replace('.', ',')
                text = f"  {status_emoji} {tariff['name']} — ${price_str}"
                builder.row(InlineKeyboardButton(text=text, callback_data=f"admin_tariff_view:{tariff['id']}"))
    else:
        for tariff in tariffs:
            status_emoji = '🟢' if tariff.get('is_active') else '⚪'
            price = tariff['price_cents'] / 100
            price_str = f'{price:g}'.replace('.', ',')
            text = f"{status_emoji} {tariff['name']} — ${price_str}"
            builder.row(InlineKeyboardButton(text=text, callback_data=f"admin_tariff_view:{tariff['id']}"))
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()

def tariff_view_kb(tariff_id: int, is_active: bool, show_group_button: bool=False) -> InlineKeyboardMarkup:
    """
    Клавиатура просмотра тарифа.
    
    Args:
        tariff_id: ID тарифа
        is_active: Активен ли тариф
        show_group_button: Показывать ли кнопку «Изменить группу» (при >1 группе)
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✏️ Изменить', callback_data=f'admin_tariff_edit:{tariff_id}'))
    if is_active:
        toggle_text = '👁️\u200d🗨️ Скрыть'
    else:
        toggle_text = '👁️ Показать'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f'admin_tariff_toggle:{tariff_id}'))
    if show_group_button:
        builder.row(InlineKeyboardButton(text='📂 Изменить группу', callback_data=f'admin_tariff_change_group:{tariff_id}'))
    builder.row(back_button('admin_tariffs'), home_button())
    return builder.as_markup()

def add_tariff_step_kb(step: int, total_steps: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для шага добавления тарифа.
    
    Args:
        step: Текущий шаг (1-N)
        total_steps: Общее количество шагов
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_tariffs'))
    return builder.as_markup()

def add_tariff_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения добавления тарифа."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Сохранить', callback_data='admin_tariff_add_save'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_tariffs'))
    return builder.as_markup()

def edit_tariff_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования тарифа с навигацией.
    
    Args:
        current_param: Индекс текущего параметра
        total_params: Общее количество параметров
    """
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if current_param > 0:
        nav_buttons.append(InlineKeyboardButton(text='⬅️ Пред.', callback_data='admin_tariff_edit_prev'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    if current_param < total_params - 1:
        nav_buttons.append(InlineKeyboardButton(text='➡️ След.', callback_data='admin_tariff_edit_next'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text='✅ Готово', callback_data='admin_tariff_edit_done'))
    return builder.as_markup()
