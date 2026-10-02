"""
Router of the “Referral system” section.

Setting up a referral program:
- On/off
- Accrual mode (days/balance)
- Setting levels (1-3)
- Text of conditions
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    is_referral_enabled,
    get_referral_reward_type,
    get_referral_levels,
    update_referral_level,
    update_referral_setting,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.placeholder_help import EDITOR_PLACEHOLDER_HELP
from bot.keyboards.admin import (
    referral_main_kb,
    referral_level_kb,
    referral_back_kb,
    back_and_home_kb
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send

router = Router()


async def show_referral_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the main menu of the referral system."""
    await state.set_state(AdminStates.referral_menu)
    
    enabled = is_referral_enabled()
    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    
    status_emoji = "🟢" if enabled else "⚪"
    status_text = "включена" if enabled else "выключена"
    
    if reward_type == 'days':
        type_text = "📅 Дни к ключу"
    else:
        type_text = "💰 На баланс"
    
    text = (
        f"🔗 <b>Реферальная система</b>\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"📊 Режим начисления: <b>{type_text}</b>\n\n"
        f"<b>Уровни:</b>\n"
    )
    
    for level in levels:
        level_num = level['level_number']
        percent = level['percent']
        is_enabled = level['enabled']
        status = "✅" if is_enabled else "⚪"
        text += f"{status} Уровень {level_num}: {percent}%\n"
    
    text += "\nВыберите действие:"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_main_kb(enabled, reward_type, levels)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_referral")
async def admin_referral(callback: CallbackQuery, state: FSMContext):
    """Login to the referral system section."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_referral_menu(callback, state)


async def _set_referral_enabled(
    callback: CallbackQuery,
    state: FSMContext,
    target_enabled: bool | None,
) -> None:
    """Set the referral system state and redraw its administrator screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = bool(is_referral_enabled())
    desired = not current if target_enabled is None else target_enabled
    if desired == current:
        status = "уже включена" if desired else "уже выключена"
        await callback.answer(f"Реферальная система {status}")
        return

    update_referral_setting('referral_enabled', '1' if desired else '0')
    await show_referral_menu(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_set:[01]$"))
async def referral_set(callback: CallbackQuery, state: FSMContext):
    """Set the referral system to the explicitly selected state."""
    target_enabled = str(callback.data).rsplit(':', 1)[1] == '1'
    await _set_referral_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_referral_toggle")
async def referral_toggle(callback: CallbackQuery, state: FSMContext):
    """Keep already sent one-button referral controls compatible."""
    await _set_referral_enabled(callback, state, None)


@router.callback_query(F.data == "admin_referral_toggle_type")
async def referral_toggle_type(callback: CallbackQuery, state: FSMContext):
    """Switching the accrual mode."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = get_referral_reward_type()
    new_value = 'balance' if current == 'days' else 'days'
    update_referral_setting('referral_reward_type', new_value)
    
    if new_value == 'days':
        await callback.answer("Режим: Дни к ключу")
    else:
        await callback.answer("Режим: На баланс")
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_level:(\d+)$"))
async def referral_level_view(callback: CallbackQuery, state: FSMContext):
    """View the level."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    level_num = int(callback.data.split(':')[1])
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.referral_level_edit)
    await state.update_data(current_level=level_num)
    
    status = "включён" if level['enabled'] else "выключен"
    
    text = (
        f"📊 <b>Уровень {level_num}</b>\n\n"
        f"Процент: <b>{level['percent']}%</b>\n"
        f"Статус: <b>{status}</b>\n\n"
        "Выберите действие:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_level_kb(level_num, level['percent'], level['enabled'])
    )
    await callback.answer()


async def _set_referral_level_enabled(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    level_num: int,
    target_enabled: bool | None,
) -> None:
    """Set one referral level state and redraw its administrator screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    levels = get_referral_levels()
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break

    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return

    current = bool(level['enabled'])
    desired = not current if target_enabled is None else target_enabled
    if desired == current:
        status = "уже включён" if desired else "уже выключен"
        await callback.answer(f"Уровень {level_num} {status}")
        return

    update_referral_level(level_num, level['percent'], desired)
    await referral_level_view(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_level_set:(\d+):([01])$"))
async def referral_level_set(callback: CallbackQuery, state: FSMContext):
    """Set one referral level to the explicitly selected state."""
    _, level_num, raw_state = str(callback.data).rsplit(':', 2)
    await _set_referral_level_enabled(
        callback,
        state,
        level_num=int(level_num),
        target_enabled=raw_state == '1',
    )


@router.callback_query(F.data.regexp(r"^admin_referral_level_toggle:(\d+)$"))
async def referral_level_toggle(callback: CallbackQuery, state: FSMContext):
    """Keep already sent one-button referral level controls compatible."""
    level_num = int(str(callback.data).rsplit(':', 1)[1])
    await _set_referral_level_enabled(
        callback,
        state,
        level_num=level_num,
        target_enabled=None,
    )


@router.callback_query(F.data.regexp(r"^admin_referral_level_percent:(\d+)$"))
async def referral_level_percent_start(callback: CallbackQuery, state: FSMContext):
    """Request a new percentage for a level."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    level_num = int(callback.data.split(':')[1])
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.referral_level_edit)
    await state.update_data(
        editing_level_percent=level_num,
        editing_level_message=callback.message
    )
    
    text = (
        f"📊 <b>Уровень {level_num}</b>\n\n"
        f"Текущий процент: <b>{level['percent']}%</b>\n\n"
        "Введите новый процент (1-100):"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_back_kb()
    )
    await callback.answer()


@router.message(AdminStates.referral_level_edit)
async def referral_level_percent_input(message: Message, state: FSMContext):
    """Processing the entry of a new percentage."""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    level_num = data.get('editing_level_percent')
    editing_message = data.get('editing_level_message')
    
    if not level_num:
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    text = get_message_text_for_storage(message, 'plain')
    
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await safe_edit_or_send(message, "❌ Введите число от 1 до 100:")
        return
    
    new_percent = int(text)
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if level:
        update_referral_level(level_num, new_percent, level['enabled'])
    
    try:
        await message.delete()
    except:
        pass
    
    await state.update_data(editing_level_percent=None, editing_level_message=None)
    
    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
            self.data = f"admin_referral_level:{level_num}"
        async def answer(self, *args, **kwargs):
            pass
    
    fake = FakeCallback(editing_message, message.from_user)
    await referral_level_view(fake, state)


@router.callback_query(F.data == "admin_referral_conditions")
async def referral_conditions_start(callback: CallbackQuery, state: FSMContext):
    """Editing the text of conditions using a universal editor."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.handlers.admin.message_editor import show_message_editor
    
    await show_message_editor(
        callback.message, state,
        key='referral',
        back_callback='admin_referral',
        allowed_types=['text', 'photo', 'video', 'animation'],
        help_text=EDITOR_PLACEHOLDER_HELP['referral'],
    )
    await callback.answer()

