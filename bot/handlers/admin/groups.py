"""
Router of the “Tariff Groups” section.

Processes:
- List of groups
- Adding a group
- Renaming a group
- Deleting a group (with transfer of tariffs/servers to “Main”)
- Sorting (⬆️ swap with previous)
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database.requests import (
    get_all_groups,
    get_group_by_id,
    add_group,
    update_group_name,
    delete_group,
    move_group_up,
    get_groups_count,
    get_tariffs_by_group,
    get_active_servers_by_group
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.keyboards.admin import (
    groups_list_kb,
    group_view_kb,
    group_delete_confirm_kb,
    back_and_home_kb
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send

router = Router()


# ============================================================================
# LIST OF GROUPS
# ============================================================================

@router.callback_query(F.data == "admin_groups")
async def show_groups_list(callback: CallbackQuery, state: FSMContext):
    """Shows a list of tariff groups."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.payments_menu)
    
    groups = get_all_groups()
    
    # We collect statistics for each group
    groups_info = []
    for group in groups:
        tariffs_count = len(get_tariffs_by_group(group['id']))
        servers_count = len(get_active_servers_by_group(group['id']))
        groups_info.append({
            **group,
            'tariffs_count': tariffs_count,
            'servers_count': servers_count
        })
    
    text = (
        "📂 <b>Группы тарифов</b>\n\n"
        "Группы ограничивают доступ: ключи можно продлевать и переносить "
        "только в рамках своей группы.\n\n"
    )
    
    if len(groups) == 1:
        text += (
            "ℹ️ Сейчас одна группа — ограничения не действуют.\n"
            "Добавьте вторую группу, чтобы разделить тарифы и серверы.\n"
        )
    
    for g in groups_info:
        is_default = " _(по умолчанию)_" if g['id'] == 1 else ""
        text += f"\n📂 <b>{g['name']}</b>{is_default}\n"
        text += f"   Тарифов: {g['tariffs_count']} | Серверов: {g['servers_count']}\n"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=groups_list_kb(groups)
    )
    await callback.answer()


# ============================================================================
# ADDING A GROUP
# ============================================================================

@router.callback_query(F.data == "admin_group_add")
async def group_add_start(callback: CallbackQuery, state: FSMContext):
    """Starts adding a new group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.group_add_name)
    
    sent = await safe_edit_or_send(callback.message, 
        "📂 <b>Новая группа</b>\n\n"
        "⚠️ После добавления второй группы у пользователей появится "
        "разделение тарифов и серверов по группам.\n\n"
        "Введите название группы (макс. 30 символов):",
        reply_markup=back_and_home_kb("admin_groups")
    )
    await state.update_data(add_group_chat_id=callback.message.chat.id, add_group_message_id=callback.message.message_id)
    await callback.answer()


@router.message(AdminStates.group_add_name)
async def group_add_name_handler(message: Message, state: FSMContext):
    """Processes entering the name of a new group."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    name = get_message_text_for_storage(message, 'plain').strip()
    
    if not name or len(name) > 30:
        await safe_edit_or_send(message,
            "⚠️ Название должно быть от 1 до 30 символов."
        )
        return
    
    # Deleting a user's message
    try:
        await message.delete()
    except:
        pass
    
    # Create a group
    group_id = add_group(name)
    
    data = await state.get_data()
    add_chat_id = data.get('add_group_chat_id')
    add_msg_id = data.get('add_group_message_id')
    
    await state.set_state(AdminStates.payments_menu)
    
    # Collecting data to display a list of groups
    groups = get_all_groups()
    groups_info = []
    for group in groups:
        tariffs_count = len(get_tariffs_by_group(group['id']))
        servers_count = len(get_active_servers_by_group(group['id']))
        groups_info.append({
            **group,
            'tariffs_count': tariffs_count,
            'servers_count': servers_count
        })
    
    text = (
        f"✅ Группа <b>{name}</b> создана!\n\n"
        "📂 <b>Группы тарифов</b>\n\n"
        "Группы ограничивают доступ: ключи можно продлевать и переносить "
        "только в рамках своей группы.\n\n"
    )
    
    if len(groups) == 1:
        text += (
            "ℹ️ Сейчас одна группа — ограничения не действуют.\n"
            "Добавьте вторую группу, чтобы разделить тарифы и серверы.\n"
        )
    
    for g in groups_info:
        is_default = " _(по умолчанию)_" if g['id'] == 1 else ""
        text += f"\n📂 <b>{g['name']}</b>{is_default}\n"
        text += f"   Тарифов: {g['tariffs_count']} | Серверов: {g['servers_count']}\n"
    
    # Editing the original message with the form
    if add_chat_id and add_msg_id:
        try:
            from bot.keyboards.admin import groups_list_kb
            await message.bot.edit_message_text(
                text,
                chat_id=add_chat_id,
                message_id=add_msg_id,
                reply_markup=groups_list_kb(groups)
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")
            await safe_edit_or_send(message, text, reply_markup=groups_list_kb(groups), force_new=True)
    else:
        from bot.keyboards.admin import groups_list_kb
        await safe_edit_or_send(message, text, reply_markup=groups_list_kb(groups), force_new=True)


# ============================================================================
# VIEW/EDIT GROUP
# ============================================================================

@router.callback_query(F.data.startswith("admin_group_view:"))
async def group_view_handler(callback: CallbackQuery, state: FSMContext):
    """Shows information about the group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    group_id = int(callback.data.split(":")[1])
    group = get_group_by_id(group_id)
    
    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return
    
    tariffs = get_tariffs_by_group(group_id)
    servers = get_active_servers_by_group(group_id)
    
    is_default = " _(по умолчанию)_" if group_id == 1 else ""
    
    text = (
        f"📂 <b>{group['name']}</b>{is_default}\n\n"
        f"🔢 Порядок: {group['sort_order']}\n"
        f"📋 Активных тарифов: {len(tariffs)}\n"
        f"🖥️ Активных серверов: {len(servers)}\n"
    )
    
    if tariffs:
        text += "\n<b>Тарифы:</b>\n"
        for t in tariffs:
            from bot.services.money import format_money_minor

            text += (
                f"  • {t['name']} — "
                f"{format_money_minor(t.get('price_minor', 0), t.get('base_currency', 'RUB'))}\n"
            )
    
    if servers:
        text += "\n<b>Серверы:</b>\n"
        for s in servers:
            text += f"  • {s['name']}\n"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=group_view_kb(group_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_group_edit:"))
async def group_edit_start(callback: CallbackQuery, state: FSMContext):
    """Starts renaming the group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    group_id = int(callback.data.split(":")[1])
    group = get_group_by_id(group_id)
    
    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return
    
    await state.set_state(AdminStates.group_edit_name)
    await state.update_data(edit_group_id=group_id, edit_message_id=callback.message.message_id)
    
    await safe_edit_or_send(callback.message, 
        f"✏️ <b>Переименование группы</b>\n\n"
        f"Текущее название: <b>{group['name']}</b>\n\n"
        "Введите новое название (макс. 30 символов):",
        reply_markup=back_and_home_kb(f"admin_group_view:{group_id}")
    )
    await callback.answer()


@router.message(AdminStates.group_edit_name)
async def group_edit_name_handler(message: Message, state: FSMContext):
    """Processes the entry of a new group name."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    name = get_message_text_for_storage(message, 'plain').strip()
    
    if not name or len(name) > 30:
        await safe_edit_or_send(message, "⚠️ Название должно быть от 1 до 30 символов.")
        return
    
    data = await state.get_data()
    group_id = data.get('edit_group_id')
    edit_msg_id = data.get('edit_message_id')
    
    if not group_id:
        await state.clear()
        await safe_edit_or_send(message, "❌ Ошибка состояния.")
        return
    
    # Deleting a user's message
    try:
        await message.delete()
    except:
        pass
    
    # Updating the name
    success = update_group_name(group_id, name)
    
    await state.set_state(AdminStates.payments_menu)
    
    if success and edit_msg_id:
        # Pattern: editing the original message
        group = get_group_by_id(group_id)
        tariffs = get_tariffs_by_group(group_id)
        servers = get_active_servers_by_group(group_id)
        
        is_default = " _(по умолчанию)_" if group_id == 1 else ""
        
        text = (
            f"✅ Группа переименована!\n\n"
            f"📂 <b>{group['name']}</b>{is_default}\n\n"
            f"🔢 Порядок: {group['sort_order']}\n"
            f"📋 Активных тарифов: {len(tariffs)}\n"
            f"🖥️ Активных серверов: {len(servers)}\n"
        )
        
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=edit_msg_id,
                reply_markup=group_view_kb(group_id)
            )
        except:
            await safe_edit_or_send(message, text, reply_markup=group_view_kb(group_id), force_new=True)
    else:
        await safe_edit_or_send(message, f"✅ Группа переименована в <b>{name}</b>")


# ============================================================================
# DELETING A GROUP
# ============================================================================

@router.callback_query(F.data.startswith("admin_group_delete:"))
async def group_delete_start(callback: CallbackQuery, state: FSMContext):
    """Group deletion confirmation."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    group_id = int(callback.data.split(":")[1])
    
    if group_id == 1:
        await callback.answer("❌ Группу «Основная» нельзя удалить", show_alert=True)
        return
    
    group = get_group_by_id(group_id)
    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return
    
    tariffs = get_tariffs_by_group(group_id)
    servers = get_active_servers_by_group(group_id)
    
    text = (
        f"⚠️ <b>Удаление группы «{group['name']}»</b>\n\n"
        f"📋 Тарифов: {len(tariffs)}\n"
        f"🖥️ Серверов: {len(servers)}\n\n"
    )
    
    if tariffs or servers:
        text += "❗ Все тарифы и серверы будут перенесены в группу «Основная».\n\n"
    
    text += "Вы уверены?"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=group_delete_confirm_kb(group_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_group_delete_confirm:"))
async def group_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Performs deletion of a group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    group_id = int(callback.data.split(":")[1])
    
    success = delete_group(group_id)
    
    if success:
        await callback.answer("✅ Группа удалена, содержимое перенесено в «Основная»")
    else:
        await callback.answer("❌ Не удалось удалить группу", show_alert=True)
    
    # Returning to the list of groups
    await show_groups_list(callback, state)


# ============================================================================
# SORTING GROUPS (⬆️)
# ============================================================================

@router.callback_query(F.data.startswith("admin_group_up:"))
async def group_move_up_handler(callback: CallbackQuery, state: FSMContext):
    """Moves the group up in the sorting."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    group_id = int(callback.data.split(":")[1])
    
    move_group_up(group_id)
    await callback.answer("🔄 Порядок обновлён")
    
    # Updating the list
    await show_groups_list(callback, state)
