from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button

def servers_list_kb(servers: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Server list keyboard.
    If there is >1 group, the servers are visually separated by headers.
    
    Args:
        servers: List of servers from the database
    """
    from database.requests import get_groups_count, get_all_groups, get_server_group_ids
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_servers_refresh'))
    builder.row(InlineKeyboardButton(text='➕ Добавить сервер', callback_data='admin_server_add'))
    groups_count = get_groups_count()
    if groups_count > 1:
        all_groups_list = get_all_groups()
        grouped_servers = {}
        for s in servers:
            g_ids = get_server_group_ids(s['id'])
            if not g_ids:
                g_ids = [1] # Fallback if there are no groups
            for g_id in g_ids:
                if g_id not in grouped_servers:
                    grouped_servers[g_id] = []
                grouped_servers[g_id].append(s)
        
        for group in all_groups_list:
            g_id = group['id']
            if g_id in grouped_servers and grouped_servers[g_id]:
                g_name = group['name']
                builder.row(InlineKeyboardButton(text=f'📂⬇ {g_name}', callback_data='noop'))
                for server in grouped_servers[g_id]:
                    status_emoji = '🟢' if server.get('is_active') else '🔴'
                    text = f"  {status_emoji} {server['name']}"
                    builder.row(InlineKeyboardButton(text=text, callback_data=f"admin_server_view:{server['id']}"))
    else:
        for server in servers:
            status_emoji = '🟢' if server.get('is_active') else '🔴'
            text = f"{status_emoji} {server['name']}"
            builder.row(InlineKeyboardButton(text=text, callback_data=f"admin_server_view:{server['id']}"))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()

def server_view_kb(server_id: int, is_active: bool, show_group_button: bool=False) -> InlineKeyboardMarkup:
    """
    Server view keyboard.

    Args:
        server_id: Server ID
        is_active: Whether the server is active
        show_group_button: Whether to show the "Edit Group" button
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✏️ Изменить настройки', callback_data=f'admin_server_edit:{server_id}'))
    toggle_text = '⏸️ Деактивировать' if is_active else '🔄 Активировать'
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f'admin_server_toggle:{server_id}'))
    if show_group_button:
        builder.row(InlineKeyboardButton(text='📂 Изменить группу', callback_data=f'admin_server_change_group:{server_id}'))
    builder.row(InlineKeyboardButton(text='🗑️ Удалить сервер', callback_data=f'admin_server_delete:{server_id}'))
    builder.row(back_button('admin_servers'), home_button())
    return builder.as_markup()

def server_groups_kb(server_id: int, all_groups: List[Dict[str, Any]], selected_group_ids: List[int]) -> InlineKeyboardMarkup:
    """
    Keyboard for selecting server groups with checkboxes (toggle).

    Args:
        server_id: Server ID
        all_groups: All groups
        selected_group_ids: IDs of groups the server is already a member of
    """
    builder = InlineKeyboardBuilder()
    for group in all_groups:
        is_selected = group['id'] in selected_group_ids
        check = '✅' if is_selected else '⬜'
        builder.row(InlineKeyboardButton(text=f"{check} {group['name']}", callback_data=f"admin_server_toggle_group:{server_id}:{group['id']}"))
    builder.row(back_button(f'admin_server_view:{server_id}'))
    return builder.as_markup()

def add_server_step_kb(step: int, total_steps: int=6) -> InlineKeyboardMarkup:
    """
    Keyboard for the add server step.
    
    Args:
        step: Current step (1-6)
        total_steps: Total number of steps
    """
    builder = InlineKeyboardBuilder()
    buttons = []
    if step > 1:
        buttons.append(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_server_add_back'))
    buttons.append(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_servers'))
    builder.row(*buttons)
    return builder.as_markup()


def add_server_auth_method_kb() -> InlineKeyboardMarkup:
    """Authentication method selector shown before server fields."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔑 По API-ключу', callback_data='admin_server_auth_api_token'),
        InlineKeyboardButton(text='👤 Логин и пароль', callback_data='admin_server_auth_login_password'),
    )
    builder.row(back_button('admin_servers'), home_button())
    return builder.as_markup()

def add_server_confirm_kb() -> InlineKeyboardMarkup:
    """Keyboard to confirm adding a server."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Сохранить', callback_data='admin_server_add_save'))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_server_add_back'), InlineKeyboardButton(text='❌ Отмена', callback_data='admin_servers'))
    return builder.as_markup()

def add_server_test_failed_kb(allow_save_anyway: bool = True) -> InlineKeyboardMarkup:
    """Keyboard when the connection test fails."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Проверить снова', callback_data='admin_server_add_test'))
    if allow_save_anyway:
        builder.row(InlineKeyboardButton(text='✅ Сохранить всё равно', callback_data='admin_server_add_save'))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_server_add_back'), InlineKeyboardButton(text='❌ Отмена', callback_data='admin_servers'))
    return builder.as_markup()

def edit_server_kb(current_param: int, total_params: int=6) -> InlineKeyboardMarkup:
    """
    Server editing keyboard with navigation.
    
    Args:
        current_param: Index of the current parameter (0-5)
        total_params: Total number of parameters
    """
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if current_param > 0:
        nav_buttons.append(InlineKeyboardButton(text='⬅️ Пред.', callback_data='admin_server_edit_prev'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    if current_param < total_params - 1:
        nav_buttons.append(InlineKeyboardButton(text='➡️ След.', callback_data='admin_server_edit_next'))
    else:
        nav_buttons.append(InlineKeyboardButton(text='—', callback_data='noop'))
    builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text='✅ Готово', callback_data='admin_server_edit_done'))
    return builder.as_markup()

def confirm_delete_kb(server_id: int) -> InlineKeyboardMarkup:
    """Server deletion confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'admin_server_delete_confirm:{server_id}'))
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'admin_server_view:{server_id}'))
    return builder.as_markup()
