from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

def groups_list_kb(groups: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Keyboard for list of tariff groups with ⬆️ buttons for sorting.
    
    Args:
        groups: List of groups from get_all_groups()
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='➕ Добавить группу', callback_data='admin_group_add'))
    for group in groups:
        row_buttons = [InlineKeyboardButton(text=f"📂 {group['name']}", callback_data=f"admin_group_view:{group['id']}")]
        if len(groups) > 1:
            row_buttons.append(InlineKeyboardButton(text='⬆️', callback_data=f"admin_group_up:{group['id']}"))
        builder.row(*row_buttons)
    builder.row(back_button('admin_payments'), home_button())
    return builder.as_markup()

def group_view_kb(group_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard for viewing tariff groups.
    
    Args:
        group_id: Group ID
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✏️ Переименовать', callback_data=f'admin_group_edit:{group_id}'))
    if group_id != 1:
        builder.row(InlineKeyboardButton(text='🗑️ Удалить группу', callback_data=f'admin_group_delete:{group_id}'))
    builder.row(back_button('admin_groups'), home_button())
    return builder.as_markup()

def group_delete_confirm_kb(group_id: int) -> InlineKeyboardMarkup:
    """
    Group deletion confirmation keypad.
    
    Args:
        group_id: Group ID
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'admin_group_delete_confirm:{group_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'admin_group_view:{group_id}'))
    return builder.as_markup()

def group_select_kb(groups: List[Dict[str, Any]], callback_prefix: str, back_callback: str) -> InlineKeyboardMarkup:
    """
    Group selection keyboard (used when creating a tariff/server).
    
    Args:
        groups: List of groups
        callback_prefix: Prefix for callback_data (e.g. "tariff_group_select")
        back_callback: Callback for the back button
    """
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.row(InlineKeyboardButton(text=f"📂 {group['name']}", callback_data=f"{callback_prefix}:{group['id']}"))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=back_callback))
    return builder.as_markup()
