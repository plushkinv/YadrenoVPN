from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

def tariffs_list_kb(tariffs: List[Dict[str, Any]], include_hidden: bool=True) -> InlineKeyboardMarkup:
    """
    Tariff list keyboard.
    If there is >1 group, tariffs are visually separated by headings.
    
    Args:
        tariffs: List of tariffs from the database
        include_hidden: Show hidden rates
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
    Tariff viewing keyboard.
    
    Args:
        tariff_id: Tariff ID
        is_active: Is the tariff active?
        show_group_button: Whether to show the “Change Group” button (for >1 group)
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
    Keyboard for adding a tariff step.
    
    Args:
        step: Current step (1-N)
        total_steps: Total number of steps
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_tariffs'))
    return builder.as_markup()

def add_tariff_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard to confirm adding a tariff."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Сохранить', callback_data='admin_tariff_add_save'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_tariffs'))
    return builder.as_markup()

def edit_tariff_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Tariff editing keyboard with navigation.
    
    Args:
        current_param: Index of the current parameter
        total_params: Total number of parameters
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
