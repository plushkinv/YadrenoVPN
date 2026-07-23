"""
Tariff management router.

Processes:
- List of tariffs
- Adding a tariff (step-by-step dialogue)
- View tariff
- Editing (scrolling through parameters)
- Hide/show (soft delete)
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_all_tariffs,
    get_tariff_by_id,
    add_tariff,
    update_tariff_field,
    toggle_tariff_active,
    get_groups_count,
    get_all_groups,
    get_group_by_id,
    update_tariff
)
from bot.utils.admin import is_admin
from bot.utils.tariff_prices import format_tariff_price_display
from bot.services.money import format_money_minor
from bot.states.admin_states import (
    AdminStates,
    TARIFF_PARAMS,
    get_tariff_param_by_index,
    get_tariff_params_list,
    get_total_tariff_params
)
from bot.keyboards.admin import (
    tariffs_list_kb,
    tariff_view_kb,
    add_tariff_step_kb,
    add_tariff_confirm_kb,
    edit_tariff_kb,
    back_and_home_kb,
    group_select_kb
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send

router = Router()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================




def format_tariff_value(param: dict, value) -> str:
    """Formats the parameter value for display."""
    if value is None:
        return "—"
    if 'format' in param:
        return param['format'](value)
    return str(value)


# ============================================================================
# LIST OF RATES
# ============================================================================

@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(callback: CallbackQuery, state: FSMContext):
    """Shows a list of tariffs."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.tariffs_list)
    await state.update_data(tariff_data={})  # Clearing temporary data
    
    tariffs = get_all_tariffs(include_hidden=True)
    
    if not tariffs:
        text = (
            "📋 <b>Тарифы</b>\n\n"
            "Тарифов пока нет.\n"
            "Нажмите «➕ Добавить тариф» чтобы создать первый!"
        )
    else:
        lines = ["📋 <b>Тарифы</b>\n"]
        
        for tariff in tariffs:
            status = "🟢" if tariff['is_active'] else "⚪"
            traffic_gb = tariff.get('traffic_limit_gb', 0)
            traffic_text = f"{traffic_gb} ГБ" if traffic_gb > 0 else "Безлим"
            
            lines.append(
                f"{status} <b>{tariff['name']}</b> — "
                f"{format_money_minor(tariff.get('price_minor', 0), tariff.get('base_currency', 'RUB'))} / "
                f"{tariff['duration_days']} дн. / {traffic_text}"
            )
            

        
        text = "\n".join(lines)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=tariffs_list_kb(tariffs)
    )
    await callback.answer()


async def render_tariff_view(message: Message, tariff_id: int, state: FSMContext):
    """Draws the tariff viewing screen."""
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        return
    
    await state.set_state(AdminStates.tariff_view)
    await state.update_data(tariff_id=tariff_id)
    
    status_emoji = "🟢 Активен" if tariff['is_active'] else "⚪ Скрыт"
    price_display = format_tariff_price_display(tariff)
    lines = [
        f"📋 <b>{tariff['name']}</b>\n",
        f"💰 Цена: <code>{price_display}</code>",
        f"📅 Длительность: <code>{tariff['duration_days']} дней</code>",
    ]
    
    # Traffic limit
    traffic_gb = tariff.get('traffic_limit_gb', 0)
    traffic_text = f"{traffic_gb} ГБ" if traffic_gb > 0 else "Безлимит"
    lines.append(f"📦 Лимит трафика: <code>{traffic_text}</code>")
    
    # Device limit
    max_ips = tariff.get('max_ips', 1)
    ips_text = f"{max_ips} устр."
    lines.append(f"💻 Лимит устройств: <code>{ips_text}</code>")
    
    # Group (only shown if > 1 group)
    groups_count = get_groups_count()
    if groups_count > 1:
        group = get_group_by_id(tariff.get('group_id', 1))
        group_name = group['name'] if group else 'Основная'
        lines.append(f"📂 Группа: <code>{group_name}</code>")
    
    lines.extend([
        f"📊 Порядок: <code>{tariff.get('display_order', 0)}</code>",
        f"\n{status_emoji}",
    ])
    
    await safe_edit_or_send(message, 
        "\n".join(lines),
        reply_markup=tariff_view_kb(tariff_id, tariff['is_active'], groups_count > 1)
    )


# ============================================================================
# VIEW TARIFF
# ============================================================================

@router.callback_query(F.data.startswith("admin_tariff_view:"))
async def show_tariff_view(callback: CallbackQuery, state: FSMContext):
    """Shows tariff details."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    # Using helper for rendering
    await render_tariff_view(callback.message, tariff_id, state)
    await callback.answer()


# ============================================================================
# HIDING/SHOWING RATE
# ============================================================================

@router.callback_query(F.data.startswith("admin_tariff_toggle:"))
async def toggle_tariff(callback: CallbackQuery, state: FSMContext):
    """Toggles the visibility of the tariff."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    new_status = toggle_tariff_active(tariff_id)
    
    if new_status is None:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    status_text = "показан 👁️" if new_status else "скрыт 👁️‍🗨️"
    await callback.answer(f"Тариф {status_text}")
    
    # Refresh the viewing screen
    # Refresh the viewing screen
    await render_tariff_view(callback.message, tariff_id, state)


# ============================================================================
# ADDING A RATE
# ============================================================================

# Add states are ok
ADD_TARIFF_STATES = [
    AdminStates.add_tariff_name,
    AdminStates.add_tariff_price_rub,
    AdminStates.add_tariff_duration,
    AdminStates.add_tariff_traffic_limit,
    AdminStates.add_tariff_max_ips,
]


def get_add_step_state(step: int) -> AdminStates:
    """Returns the status for the add step."""
    params = get_tariff_params_list()
    params = [p for p in params if p['key'] != 'display_order']
    if step <= 0:
        return ADD_TARIFF_STATES[0]
    if step > len(params):
        return AdminStates.add_tariff_confirm
    
    # Find the corresponding state by key
    param = params[step - 1]
    key = param['key']
    
    state_map = {
        'name': AdminStates.add_tariff_name,
        'price_minor': AdminStates.add_tariff_price_rub,
        'duration_days': AdminStates.add_tariff_duration,
        'traffic_limit_gb': AdminStates.add_tariff_traffic_limit,
        'max_ips': AdminStates.add_tariff_max_ips,
        'display_order': AdminStates.add_tariff_confirm,  # display_order is skipped when adding
    }
    
    return state_map.get(key, AdminStates.add_tariff_confirm)


def get_add_step_text(step: int, data: dict) -> str:
    """Generates text for the step of adding a tariff."""
    params = get_tariff_params_list()
    # Remove display_order from adding (it will be 0 by default)
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    if step > total:
        return "Ошибка"
    
    param = params[step - 1]
    
    lines = [f"📝 <b>Добавление тарифа ({step}/{total})</b>\n"]
    
    # Showing already entered data
    for i in range(step - 1):
        p = params[i]
        value = data.get(p['key'], '—')
        display = format_tariff_value(p, value)
        lines.append(f"✅ {p['label']}: <code>{display}</code>")
    
    if step > 1:
        lines.append("")
    
    lines.append(f"Введите <b>{param['label'].lower()}</b>:")
    lines.append(f"<i>({param['hint']})</i>")
    
    # If there is additional help
    if param.get('help'):
        lines.append(f"\n{param['help']}")
    
    return "\n".join(lines)


@router.callback_query(F.data == "admin_tariff_add")
async def start_add_tariff(callback: CallbackQuery, state: FSMContext):
    """Starts the dialog for adding a tariff."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    
    # If > 1 group, first select the group
    groups_count = get_groups_count()
    if groups_count > 1:
        groups = get_all_groups()
        await state.set_state(AdminStates.tariff_select_group)
        await state.update_data(tariff_data={})
        
        await safe_edit_or_send(callback.message, 
            "📝 <b>Добавление тарифа</b>\n\n"
            "Выберите группу для нового тарифа:",
            reply_markup=group_select_kb(groups, "tariff_group_select", "admin_tariffs")
        )
        await callback.answer()
        return
    
    # One group - straight to data entry
    await state.set_state(AdminStates.add_tariff_name)
    await state.update_data(tariff_data={}, add_step=1, selected_group_id=1)
    
    params = get_tariff_params_list()
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    text = get_add_step_text(1, {})
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=add_tariff_step_kb(1, total)
    )
    await callback.answer()


@router.callback_query(AdminStates.tariff_select_group, F.data.startswith("tariff_group_select:"))
async def tariff_group_selected(callback: CallbackQuery, state: FSMContext):
    """Processing group selection for a new tariff."""
    group_id = int(callback.data.split(":")[1])
    
    data = await state.get_data()
    
    await state.set_state(AdminStates.add_tariff_name)
    await state.update_data(add_step=1, selected_group_id=group_id)
    
    params = get_tariff_params_list()
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    group = get_group_by_id(group_id)
    group_name = group['name'] if group else 'Основная'
    
    text = f"📂 Группа: <b>{group_name}</b>\n\n" + get_add_step_text(1, {})
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=add_tariff_step_kb(1, total)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_add_back")
async def add_tariff_back(callback: CallbackQuery, state: FSMContext):
    """Return to the previous adding step."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    
    if current_step <= 1:
        # Return to tariff list
        await show_tariffs_list(callback, state)
        return
    
    # One step back
    new_step = current_step - 1
    new_state = get_add_step_state(new_step)
    await state.set_state(new_state)
    await state.update_data(add_step=new_step)
    
    params = get_tariff_params_list()
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    text = get_add_step_text(new_step, data.get('tariff_data', {}))
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=add_tariff_step_kb(new_step, total)
    )
    await callback.answer()


async def process_add_tariff_step(message: Message, state: FSMContext):
    """Processes input at the rate adding step."""
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    tariff_data = data.get('tariff_data', {})
    
    params = get_tariff_params_list()
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    if current_step > total:
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    param = params[current_step - 1]
    value = get_message_text_for_storage(message, 'plain')
    
    # Validation
    if not param['validate'](value):
        await safe_edit_or_send(message,
            f"❌ {param['error']}\n\nПопробуйте ещё раз:"
        )
        return
    
    # Conversion
    if 'convert' in param:
        value = param['convert'](value)
    
    # Saving the value
    tariff_data[param['key']] = value
    await state.update_data(tariff_data=tariff_data)
    
    # Delete the message
    try:
        await message.delete()
    except:
        pass
    
    # Move to next step or confirmation
    if current_step < total:
        new_step = current_step + 1
        new_state = get_add_step_state(new_step)
        await state.set_state(new_state)
        await state.update_data(add_step=new_step)
        
        text = get_add_step_text(new_step, tariff_data)
        
        await safe_edit_or_send(message,
            text,
            reply_markup=add_tariff_step_kb(new_step, total),
            force_new=True
        )
    else:
        # All data has been entered - we show confirmation
        await state.set_state(AdminStates.add_tariff_confirm)
        
        lines = [
            "✅ <b>Все данные введены!</b>\n",
            f"📌 Название: <code>{tariff_data['name']}</code>",
            f"💰 Цена: <code>{format_money_minor(tariff_data.get('price_minor', 0))}</code>",
            f"📅 Длительность: <code>{tariff_data['duration_days']} дней</code>",
        ]
        
        # Traffic limit
        traffic_gb = tariff_data.get('traffic_limit_gb', 0)
        traffic_text = f"{traffic_gb} ГБ" if traffic_gb > 0 else "Безлимит"
        lines.append(f"📦 Лимит трафика: <code>{traffic_text}</code>")
        
        # Device limit
        max_ips = tariff_data.get('max_ips', 1)
        ips_text = f"{max_ips} устр."
        lines.append(f"💻 Лимит устройств: <code>{ips_text}</code>")
        
        lines.append("\nСохранить тариф?")
        
        await safe_edit_or_send(message,
            "\n".join(lines),
            reply_markup=add_tariff_confirm_kb(),
            force_new=True
        )


# Handlers for each add state
@router.message(AdminStates.add_tariff_name)
async def add_tariff_name_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_price_rub)
async def add_tariff_price_rub_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_duration)
async def add_tariff_duration_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)





@router.message(AdminStates.add_tariff_traffic_limit)
async def add_tariff_traffic_limit_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_max_ips)
async def add_tariff_max_ips_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.callback_query(F.data == "admin_tariff_add_save")
async def add_tariff_save(callback: CallbackQuery, state: FSMContext):
    """Saves the new tariff."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_data = data.get('tariff_data', {})
    
    try:
        selected_group_id = data.get('selected_group_id', 1)
        tariff_id = add_tariff(
            name=tariff_data['name'],
            duration_days=tariff_data['duration_days'],
            price_minor=tariff_data['price_minor'],
            display_order=0,
            traffic_limit_gb=tariff_data.get('traffic_limit_gb', 0),
            group_id=selected_group_id,
            max_ips=tariff_data.get('max_ips', 1)
        )
        
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Тариф успешно добавлен!</b>\n\n"
            f"📋 {tariff_data['name']}"
        )
        
        await callback.answer("✅ Тариф добавлен!")
        
        # Showing the tariff
        # Showing the tariff
        await render_tariff_view(callback.message, tariff_id, state)
        
    except Exception as e:
        logger.error(f"Ошибка добавления тарифа: {e}")
        await safe_edit_or_send(callback.message, 
            f"❌ <b>Ошибка сохранения</b>\n\n<code>{e}</code>",
            reply_markup=back_and_home_kb("admin_tariffs")
        )
        await callback.answer("❌ Ошибка", show_alert=True)


# ============================================================================
# EDITING THE TARIFF
# ============================================================================

def get_edit_tariff_text(tariff: dict, current_param: int) -> str:
    """Generates text for the tariff editing screen."""
    params = get_tariff_params_list()
    total = len(params)
    
    param = params[current_param]
    current_value = tariff.get(param['key'])
    display_value = format_tariff_value(param, current_value)
    
    lines = [
        f"✏️ <b>Редактирование: {tariff['name']}</b> ({current_param + 1}/{total})\n",
        f"📌 Параметр: <b>{param['label']}</b>",
        f"📝 Текущее значение: <code>{display_value}</code>\n",
        f"Введите новое значение или используйте кнопки навигации:",
        f"<i>({param['hint']})</i>"
    ]
    
    if param.get('help'):
        lines.append(f"\n{param['help']}")
    
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_tariff_edit:"))
async def start_edit_tariff(callback: CallbackQuery, state: FSMContext):
    """Starts editing the tariff."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.edit_tariff)
    await state.update_data(tariff_id=tariff_id, edit_param=0)
    
    text = get_edit_tariff_text(tariff, 0)
    total = get_total_tariff_params()
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_tariff_kb(0, total)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_edit_prev")
async def edit_tariff_prev(callback: CallbackQuery, state: FSMContext):
    """Previous parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    new_param = max(0, current_param - 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_tariff_text(tariff, new_param)
    total = get_total_tariff_params()
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_tariff_kb(new_param, total)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_edit_next")
async def edit_tariff_next(callback: CallbackQuery, state: FSMContext):
    """Next parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    total = get_total_tariff_params()
    new_param = min(total - 1, current_param + 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_tariff_text(tariff, new_param)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_tariff_kb(new_param, total)
    )
    await callback.answer()


@router.message(AdminStates.edit_tariff)
async def edit_tariff_value(message: Message, state: FSMContext):
    """Handles the entry of a new value when editing."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    param = get_tariff_param_by_index(current_param)
    value = get_message_text_for_storage(message, 'plain')
    
    # Validation
    if not param['validate'](value):
        await safe_edit_or_send(message,
            f"❌ {param['error']}"
        )
        return
    
    # Conversion
    if 'convert' in param:
        value = param['convert'](value)
    
    # Saving in the database
    success = update_tariff_field(tariff_id, param['key'], value)
    
    if not success:
        await safe_edit_or_send(message, "❌ Ошибка сохранения")
        return
    
    # Delete the message
    try:
        await message.delete()
    except:
        pass
    
    # Refresh the screen with the new value
    tariff = get_tariff_by_id(tariff_id)
    text = get_edit_tariff_text(tariff, current_param)
    total = get_total_tariff_params()
    
    await safe_edit_or_send(message,
        f"✅ <b>{param['label']}</b> обновлено!\n\n" + text,
        reply_markup=edit_tariff_kb(current_param, total),
        force_new=True
    )


@router.callback_query(F.data == "admin_tariff_edit_done")
async def edit_tariff_done(callback: CallbackQuery, state: FSMContext):
    """Finish editing - return to viewing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    
    # Redirect to view tariff
    # Redirect to view tariff
    await render_tariff_view(callback.message, tariff_id, state)


@router.callback_query(F.data == "admin_tariff_edit_cancel")
async def edit_tariff_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel editing - return to viewing."""
    await edit_tariff_done(callback, state)


# ============================================================================
# CHANGE OF TARIFF GROUP
# ============================================================================

@router.callback_query(F.data.startswith("admin_tariff_change_group:"))
async def tariff_change_group_start(callback: CallbackQuery, state: FSMContext):
    """Shows a list of groups for changing the tariff group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    groups = get_all_groups()
    
    await safe_edit_or_send(callback.message, 
        f"📂 <b>Смена группы тарифа «{tariff['name']}»</b>\n\n"
        "Выберите новую группу:",
        reply_markup=group_select_kb(groups, "tariff_group_change", f"admin_tariff_view:{tariff_id}")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tariff_group_change:"))
async def tariff_change_group_execute(callback: CallbackQuery, state: FSMContext):
    """Changes the tariff group."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    new_group_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    
    if not tariff_id:
        await callback.answer("❌ Ошибка состояния", show_alert=True)
        return
    
    update_tariff(tariff_id, group_id=new_group_id)
    
    group = get_group_by_id(new_group_id)
    group_name = group['name'] if group else 'Основная'
    
    await callback.answer(f"✅ Группа изменена на «{group_name}»")
    
    # Updating the tariff viewing screen
    await render_tariff_view(callback.message, tariff_id, state)
