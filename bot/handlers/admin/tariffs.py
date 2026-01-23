"""
Роутер управления тарифами.

Обрабатывает:
- Список тарифов
- Добавление тарифа (пошаговый диалог)
- Просмотр тарифа
- Редактирование (листание параметров)
- Скрытие/показ (soft delete)
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
    is_crypto_enabled
)
from bot.utils.admin import is_admin
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
    back_and_home_kb
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================




def format_tariff_value(param: dict, value) -> str:
    """Форматирует значение параметра для отображения."""
    if value is None:
        return "—"
    if 'format' in param:
        return param['format'](value)
    return str(value)


# ============================================================================
# СПИСОК ТАРИФОВ
# ============================================================================

@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(callback: CallbackQuery, state: FSMContext):
    """Показывает список тарифов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.tariffs_list)
    await state.update_data(tariff_data={})  # Очищаем временные данные
    
    tariffs = get_all_tariffs(include_hidden=True)
    
    if not tariffs:
        text = (
            "📋 *Тарифы*\n\n"
            "Тарифов пока нет.\n"
            "Нажмите «➕ Добавить тариф» чтобы создать первый!"
        )
    else:
        lines = ["📋 *Тарифы*\n"]
        
        for tariff in tariffs:
            status = "🟢" if tariff['is_active'] else "🔴"
            price_usd = tariff['price_cents'] / 100
            lines.append(
                f"{status} *{tariff['name']}* — "
                f"${price_usd:.2f} / ⭐ {tariff['price_stars']} / "
                f"{tariff['duration_days']} дн."
            )
            
            if tariff.get('external_id'):
                lines[-1] += f" (ID: {tariff['external_id']})"
        
        text = "\n".join(lines)
    
    await callback.message.edit_text(
        text,
        reply_markup=tariffs_list_kb(tariffs),
        parse_mode="Markdown"
    )
    await callback.answer()


async def render_tariff_view(message: Message, tariff_id: int, state: FSMContext):
    """Отрисовывает экран просмотра тарифа."""
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        return
    
    await state.set_state(AdminStates.tariff_view)
    await state.update_data(tariff_id=tariff_id)
    
    status_emoji = "🟢 Активен" if tariff['is_active'] else "🔴 Скрыт"
    price_usd = tariff['price_cents'] / 100
    
    lines = [
        f"📋 *{tariff['name']}*\n",
        f"💰 Цена (USDT): `${price_usd:.2f}`",
        f"⭐ Цена (Stars): `{tariff['price_stars']}`",
        f"📅 Длительность: `{tariff['duration_days']} дней`",
    ]
    
    if tariff.get('external_id'):
        lines.append(f"🔗 ID тарифа (Ya.Seller): `{tariff['external_id']}`")
    
    lines.extend([
        f"📊 Порядок: `{tariff.get('display_order', 0)}`",
        f"\n{status_emoji}",
    ])
    
    await message.edit_text(
        "\n".join(lines),
        reply_markup=tariff_view_kb(tariff_id, tariff['is_active']),
        parse_mode="Markdown"
    )


# ============================================================================
# ПРОСМОТР ТАРИФА
# ============================================================================

@router.callback_query(F.data.startswith("admin_tariff_view:"))
async def show_tariff_view(callback: CallbackQuery, state: FSMContext):
    """Показывает детали тарифа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    # Используем helper для отрисовки
    await render_tariff_view(callback.message, tariff_id, state)
    await callback.answer()


# ============================================================================
# СКРЫТИЕ/ПОКАЗ ТАРИФА
# ============================================================================

@router.callback_query(F.data.startswith("admin_tariff_toggle:"))
async def toggle_tariff(callback: CallbackQuery, state: FSMContext):
    """Переключает видимость тарифа."""
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
    
    # Обновляем экран просмотра
    # Обновляем экран просмотра
    await render_tariff_view(callback.message, tariff_id, state)


# ============================================================================
# ДОБАВЛЕНИЕ ТАРИФА
# ============================================================================

# Состояния добавления в порядке
ADD_TARIFF_STATES = [
    AdminStates.add_tariff_name,
    AdminStates.add_tariff_price_cents,
    AdminStates.add_tariff_price_stars,
    AdminStates.add_tariff_duration,
    AdminStates.add_tariff_external_id,
]


def get_add_step_state(step: int, include_crypto: bool) -> AdminStates:
    """Возвращает состояние для шага добавления."""
    params = get_tariff_params_list(include_crypto)
    if step <= 0:
        return ADD_TARIFF_STATES[0]
    if step > len(params):
        return AdminStates.add_tariff_confirm
    
    # Находим соответствующее состояние по key
    param = params[step - 1]
    key = param['key']
    
    state_map = {
        'name': AdminStates.add_tariff_name,
        'price_cents': AdminStates.add_tariff_price_cents,
        'price_stars': AdminStates.add_tariff_price_stars,
        'duration_days': AdminStates.add_tariff_duration,
        'external_id': AdminStates.add_tariff_external_id,
        'display_order': AdminStates.add_tariff_confirm,  # display_order пропускаем при добавлении
    }
    
    return state_map.get(key, AdminStates.add_tariff_confirm)


def get_add_step_text(step: int, data: dict, include_crypto: bool) -> str:
    """Формирует текст для шага добавления тарифа."""
    params = get_tariff_params_list(include_crypto)
    # Убираем display_order из добавления (он будет 0 по умолчанию)
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    if step > total:
        return "Ошибка"
    
    param = params[step - 1]
    
    lines = [f"📝 *Добавление тарифа ({step}/{total})*\n"]
    
    # Показываем уже введённые данные
    for i in range(step - 1):
        p = params[i]
        value = data.get(p['key'], '—')
        display = format_tariff_value(p, value)
        lines.append(f"✅ {p['label']}: `{display}`")
    
    if step > 1:
        lines.append("")
    
    lines.append(f"Введите *{param['label'].lower()}*:")
    lines.append(f"_({param['hint']})_")
    
    # Если есть дополнительная справка
    if param.get('help'):
        lines.append(f"\n{param['help']}")
    
    return "\n".join(lines)


@router.callback_query(F.data == "admin_tariff_add")
async def start_add_tariff(callback: CallbackQuery, state: FSMContext):
    """Начинает диалог добавления тарифа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    include_crypto = is_crypto_enabled()
    
    await state.set_state(AdminStates.add_tariff_name)
    await state.update_data(tariff_data={}, add_step=1, include_crypto=include_crypto)
    
    params = get_tariff_params_list(include_crypto)
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    text = get_add_step_text(1, {}, include_crypto)
    
    await callback.message.edit_text(
        text,
        reply_markup=add_tariff_step_kb(1, total),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_add_back")
async def add_tariff_back(callback: CallbackQuery, state: FSMContext):
    """Возврат на предыдущий шаг добавления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    include_crypto = data.get('include_crypto', False)
    
    if current_step <= 1:
        # Возврат к списку тарифов
        await show_tariffs_list(callback, state)
        return
    
    # На шаг назад
    new_step = current_step - 1
    new_state = get_add_step_state(new_step, include_crypto)
    await state.set_state(new_state)
    await state.update_data(add_step=new_step)
    
    params = get_tariff_params_list(include_crypto)
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    text = get_add_step_text(new_step, data.get('tariff_data', {}), include_crypto)
    
    await callback.message.edit_text(
        text,
        reply_markup=add_tariff_step_kb(new_step, total),
        parse_mode="Markdown"
    )
    await callback.answer()


async def process_add_tariff_step(message: Message, state: FSMContext):
    """Обрабатывает ввод на шаге добавления тарифа."""
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    tariff_data = data.get('tariff_data', {})
    include_crypto = data.get('include_crypto', False)
    
    params = get_tariff_params_list(include_crypto)
    params = [p for p in params if p['key'] != 'display_order']
    total = len(params)
    
    if current_step > total:
        return
    
    param = params[current_step - 1]
    value = message.text.strip()
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Конвертация
    if 'convert' in param:
        value = param['convert'](value)
    
    # Сохраняем значение
    tariff_data[param['key']] = value
    await state.update_data(tariff_data=tariff_data)
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Переход к следующему шагу или подтверждению
    if current_step < total:
        new_step = current_step + 1
        new_state = get_add_step_state(new_step, include_crypto)
        await state.set_state(new_state)
        await state.update_data(add_step=new_step)
        
        text = get_add_step_text(new_step, tariff_data, include_crypto)
        
        await message.answer(
            text,
            reply_markup=add_tariff_step_kb(new_step, total),
            parse_mode="Markdown"
        )
    else:
        # Все данные введены — показываем подтверждение
        await state.set_state(AdminStates.add_tariff_confirm)
        
        price_usd = tariff_data['price_cents'] / 100
        
        lines = [
            "✅ *Все данные введены!*\n",
            f"📌 Название: `{tariff_data['name']}`",
            f"💰 Цена (USDT): `${price_usd:.2f}`",
            f"⭐ Цена (Stars): `{tariff_data['price_stars']}`",
            f"📅 Длительность: `{tariff_data['duration_days']} дней`",
        ]
        
        if tariff_data.get('external_id'):
            lines.append(f"🔗 ID тарифа: `{tariff_data['external_id']}`")
        
        lines.append("\nСохранить тариф?")
        
        await message.answer(
            "\n".join(lines),
            reply_markup=add_tariff_confirm_kb(),
            parse_mode="Markdown"
        )


# Хендлеры для каждого состояния добавления
@router.message(AdminStates.add_tariff_name)
async def add_tariff_name_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_price_cents)
async def add_tariff_price_cents_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_price_stars)
async def add_tariff_price_stars_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_duration)
async def add_tariff_duration_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.message(AdminStates.add_tariff_external_id)
async def add_tariff_external_id_handler(message: Message, state: FSMContext):
    await process_add_tariff_step(message, state)


@router.callback_query(F.data == "admin_tariff_add_save")
async def add_tariff_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет новый тариф."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_data = data.get('tariff_data', {})
    
    try:
        tariff_id = add_tariff(
            name=tariff_data['name'],
            duration_days=tariff_data['duration_days'],
            price_cents=tariff_data['price_cents'],
            price_stars=tariff_data['price_stars'],
            external_id=tariff_data.get('external_id'),
            display_order=0
        )
        
        await callback.message.edit_text(
            f"✅ *Тариф успешно добавлен!*\n\n"
            f"📋 {tariff_data['name']}",
            parse_mode="Markdown"
        )
        
        await callback.answer("✅ Тариф добавлен!")
        
        # Показываем тариф
        # Показываем тариф
        await render_tariff_view(callback.message, tariff_id, state)
        
    except Exception as e:
        logger.error(f"Ошибка добавления тарифа: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка сохранения*\n\n`{e}`",
            reply_markup=back_and_home_kb("admin_tariffs"),
            parse_mode="Markdown"
        )
        await callback.answer("❌ Ошибка", show_alert=True)


# ============================================================================
# РЕДАКТИРОВАНИЕ ТАРИФА
# ============================================================================

def get_edit_tariff_text(tariff: dict, current_param: int, include_crypto: bool) -> str:
    """Формирует текст для экрана редактирования тарифа."""
    params = get_tariff_params_list(include_crypto)
    total = len(params)
    
    param = params[current_param]
    current_value = tariff.get(param['key'])
    display_value = format_tariff_value(param, current_value)
    
    lines = [
        f"✏️ *Редактирование: {tariff['name']}* ({current_param + 1}/{total})\n",
        f"📌 Параметр: *{param['label']}*",
        f"📝 Текущее значение: `{display_value}`\n",
        f"Введите новое значение или используйте кнопки навигации:",
        f"_({param['hint']})_"
    ]
    
    if param.get('help'):
        lines.append(f"\n{param['help']}")
    
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_tariff_edit:"))
async def start_edit_tariff(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование тарифа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    include_crypto = is_crypto_enabled()
    
    await state.set_state(AdminStates.edit_tariff)
    await state.update_data(tariff_id=tariff_id, edit_param=0, include_crypto=include_crypto)
    
    text = get_edit_tariff_text(tariff, 0, include_crypto)
    total = get_total_tariff_params(include_crypto)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_tariff_kb(0, total),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_edit_prev")
async def edit_tariff_prev(callback: CallbackQuery, state: FSMContext):
    """Предыдущий параметр при редактировании."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    include_crypto = data.get('include_crypto', False)
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    new_param = max(0, current_param - 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_tariff_text(tariff, new_param, include_crypto)
    total = get_total_tariff_params(include_crypto)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_tariff_kb(new_param, total),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tariff_edit_next")
async def edit_tariff_next(callback: CallbackQuery, state: FSMContext):
    """Следующий параметр при редактировании."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    include_crypto = data.get('include_crypto', False)
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    total = get_total_tariff_params(include_crypto)
    new_param = min(total - 1, current_param + 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_tariff_text(tariff, new_param, include_crypto)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_tariff_kb(new_param, total),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.edit_tariff)
async def edit_tariff_value(message: Message, state: FSMContext):
    """Обрабатывает ввод нового значения при редактировании."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    current_param = data.get('edit_param', 0)
    include_crypto = data.get('include_crypto', False)
    
    param = get_tariff_param_by_index(current_param, include_crypto)
    value = message.text.strip()
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}",
            parse_mode="Markdown"
        )
        return
    
    # Конвертация
    if 'convert' in param:
        value = param['convert'](value)
    
    # Сохраняем в БД
    success = update_tariff_field(tariff_id, param['key'], value)
    
    if not success:
        await message.answer("❌ Ошибка сохранения")
        return
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Обновляем экран с новым значением
    tariff = get_tariff_by_id(tariff_id)
    text = get_edit_tariff_text(tariff, current_param, include_crypto)
    total = get_total_tariff_params(include_crypto)
    
    await message.answer(
        f"✅ *{param['label']}* обновлено!\n\n" + text,
        reply_markup=edit_tariff_kb(current_param, total),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_tariff_edit_done")
async def edit_tariff_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования — возврат к просмотру."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    
    # Перенаправляем на просмотр тарифа
    # Перенаправляем на просмотр тарифа
    await render_tariff_view(callback.message, tariff_id, state)


@router.callback_query(F.data == "admin_tariff_edit_cancel")
async def edit_tariff_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования — возврат к просмотру."""
    await edit_tariff_done(callback, state)
