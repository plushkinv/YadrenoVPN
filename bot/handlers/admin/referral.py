"""
Роутер раздела «Реферальная система».

Настройка реферальной программы:
- Включение/выключение
- Режим начисления (дни/баланс)
- Настройка уровней (1-3)
- Текст условий
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    is_referral_enabled,
    get_referral_reward_type,
    get_referral_conditions_text,
    get_referral_levels,
    get_referral_notification_settings,
    update_referral_level,
    update_referral_setting,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
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
    """Показывает главное меню реферальной системы."""
    await state.set_state(AdminStates.referral_menu)
    
    enabled = is_referral_enabled()
    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    notification_settings = get_referral_notification_settings()
    from bot.utils.message_editor import get_message_data
    conditions_data = get_message_data('referral', '')
    conditions_text = conditions_data.get('text', '')
    
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

    levels_text = ', '.join(str(level) for level in notification_settings['levels'])
    new_ref_status = "Вкл" if notification_settings['new_ref_enabled'] else "Выкл"
    purchase_status = "Вкл" if notification_settings['purchase_enabled'] else "Выкл"
    text += (
        "\n<b>Скрытые уведомления рефоводу:</b>\n"
        f"👥 Уведомления о рефералах: <b>{new_ref_status}</b>\n"
    )
    if notification_settings['new_ref_enabled']:
        new_ref_text_status = "задан" if notification_settings['new_ref_text_set'] else "не задан"
        text += (
            f"  Уровни: <b>{levels_text}</b>\n"
            f"  Текст: <b>{new_ref_text_status}</b>\n"
        )
    text += f"💳 Уведомления о покупках: <b>{purchase_status}</b>\n"
    if notification_settings['purchase_enabled']:
        purchase_text_status = "задан" if notification_settings['purchase_text_set'] else "не задан"
        text += (
            f"  Уровни: <b>{levels_text}</b>\n"
            f"  Текст: <b>{purchase_text_status}</b>\n"
        )
    
    if conditions_text:
        text += f"\n📝 Текст условий задан\n"
    
    text += "\nВыберите действие:"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_main_kb(enabled, reward_type, levels)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_referral")
async def admin_referral(callback: CallbackQuery, state: FSMContext):
    """Вход в раздел реферальной системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data == "admin_referral_toggle")
async def referral_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение реферальной системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_referral_enabled()
    new_value = '0' if current else '1'
    update_referral_setting('referral_enabled', new_value)
    
    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"Реферальная система {status}")
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data == "admin_referral_toggle_type")
async def referral_toggle_type(callback: CallbackQuery, state: FSMContext):
    """Переключение режима начисления."""
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
    """Просмотр уровня."""
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


@router.callback_query(F.data.regexp(r"^admin_referral_level_toggle:(\d+)$"))
async def referral_level_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение уровня."""
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
    
    new_enabled = not level['enabled']
    update_referral_level(level_num, level['percent'], new_enabled)
    
    status = "включён ✅" if new_enabled else "выключен"
    await callback.answer(f"Уровень {level_num} {status}")
    
    await referral_level_view(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_level_percent:(\d+)$"))
async def referral_level_percent_start(callback: CallbackQuery, state: FSMContext):
    """Запрос нового процента для уровня."""
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
    """Обработка ввода нового процента."""
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
    """Редактирование текста условий через универсальный редактор."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.handlers.admin.message_editor import show_message_editor
    
    help_text = (
        "📝 <b>Справка: Реферальная страница</b>\n\n"
        "В тексте доступны плейсхолдеры, которые автоматически подставляются "
        "при показе пользователю:\n\n"
        "Переменные:\n"
        "• <code>%ссылка%</code> — реферальная ссылка пользователя\n"
        "• <code>%статистика%</code> — статистика по уровням и баланс"
    )
    
    await show_message_editor(
        callback.message, state,
        key='referral',
        back_callback='admin_referral',
        allowed_types=['text', 'photo'],
        help_text=help_text,
    )
    await callback.answer()

