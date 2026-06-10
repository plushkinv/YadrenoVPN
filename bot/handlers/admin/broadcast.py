"""
Обработчики раздела «Рассылка» в админ-панели.

Функционал:
- Рассылка сообщений всем пользователям с фильтрами
- Настройка автоуведомлений об истечении ключей
"""
import json
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_IDS
from database.requests import (
    get_setting, set_setting,
    get_users_for_broadcast, count_users_for_broadcast,
    mark_user_bot_blocked
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.keyboards.admin import (
    broadcast_main_kb, broadcast_confirm_kb,
    broadcast_notifications_kb, broadcast_back_kb,
    broadcast_notify_back_kb, home_only_kb,
    BROADCAST_FILTERS
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send
from bot.utils.delivery import is_bot_blocked_error

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================




def get_broadcast_message() -> dict | None:
    """
    Получает сохранённое сообщение для рассылки.
    
    Returns:
        Словарь с ключами 'text' и 'photo_file_id' или None
    """
    msg_json = get_setting('broadcast_message')
    if msg_json:
        try:
            return json.loads(msg_json)
        except json.JSONDecodeError:
            return None
    return None


def save_broadcast_message(text: str, photo_file_id: str | None = None) -> None:
    """Сохраняет сообщение для рассылки."""
    data = {'text': text, 'photo_file_id': photo_file_id}
    set_setting('broadcast_message', json.dumps(data, ensure_ascii=False))


def is_broadcast_in_progress() -> bool:
    """Проверяет, идёт ли рассылка сейчас."""
    return get_setting('broadcast_in_progress', '0') == '1'


def set_broadcast_in_progress(value: bool) -> None:
    """Устанавливает флаг рассылки."""
    set_setting('broadcast_in_progress', '1' if value else '0')


# ============================================================================
# ГЛАВНЫЙ ЭКРАН РАССЫЛКИ
# ============================================================================

@router.callback_query(F.data == "admin_broadcast")
async def show_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает главный экран раздела рассылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_menu)
    
    # Получаем данные для отображения
    msg_data = get_broadcast_message()
    has_message = msg_data is not None and msg_data.get('text')
    
    current_filter = get_setting('broadcast_filter', 'all')
    in_progress = is_broadcast_in_progress()
    user_count = count_users_for_broadcast(current_filter)
    
    text = (
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_main_kb(has_message, current_filter, in_progress, user_count)
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой обработчик для разделителя."""
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()


# ============================================================================
# РЕДАКТИРОВАНИЕ СООБЩЕНИЯ
# ============================================================================

@router.callback_query(F.data == "broadcast_edit_message")
async def broadcast_edit_message(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование сообщения для рассылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_waiting_message)
    
    text = (
        "✉️ <b>Редактирование сообщения</b>\n\n"
        "Отправьте мне сообщение, которое хотите разослать.\n\n"
        "Можно отправить:\n"
        "• Текст (с форматированием)\n"
        "• Фото с подписью\n\n"
        "💡 Сообщение будет отправлено пользователям в точности как вы его прислали."
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_back_kb()
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_message)
async def broadcast_save_message(message: Message, state: FSMContext):
    """Сохраняет сообщение для рассылки."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    text = None
    photo_file_id = None
    
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = get_message_text_for_storage(message, 'html')
    elif message.text:
        text = get_message_text_for_storage(message, 'html')
    else:
        await safe_edit_or_send(message,
            "❌ Поддерживаются только текст или фото с подписью.",
            reply_markup=broadcast_back_kb()
        )
        return
    
    save_broadcast_message(text, photo_file_id)
    
    await safe_edit_or_send(message,
        "✅ <b>Сообщение сохранено!</b>\n\n"
        "Теперь можете посмотреть превью или начать рассылку."
    )
    
    # Возвращаемся в меню рассылки
    await state.set_state(AdminStates.broadcast_menu)
    
    msg_data = get_broadcast_message()
    has_message = msg_data is not None
    current_filter = get_setting('broadcast_filter', 'all')
    in_progress = is_broadcast_in_progress()
    user_count = count_users_for_broadcast(current_filter)
    
    text = (
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await safe_edit_or_send(message,
        text,
        reply_markup=broadcast_main_kb(has_message, current_filter, in_progress, user_count),
        force_new=True
    )


# ============================================================================
# ПРЕВЬЮ СООБЩЕНИЯ
# ============================================================================

@router.callback_query(F.data == "broadcast_preview")
async def broadcast_preview(callback: CallbackQuery):
    """Показывает превью сообщения для рассылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    msg_data = get_broadcast_message()
    
    if not msg_data or not msg_data.get('text'):
        await callback.answer("❌ Сообщение не задано", show_alert=True)
        return
    
    await callback.answer("📤 Отправляю превью...")
    
    # Отправляем превью как отдельное сообщение
    if msg_data.get('photo_file_id'):
        await safe_edit_or_send(callback.message,
            photo=msg_data['photo_file_id'],
            text=msg_data.get('text', ''),
            force_new=True
        )
    else:
        await safe_edit_or_send(callback.message,
            text=msg_data['text'],
            force_new=True
        )


# ============================================================================
# ФИЛЬТРЫ
# ============================================================================

@router.callback_query(F.data.startswith("broadcast_filter:"))
async def broadcast_set_filter(callback: CallbackQuery):
    """Устанавливает фильтр получателей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    filter_key = callback.data.split(":")[1]
    
    if filter_key not in BROADCAST_FILTERS:
        await callback.answer("❌ Неизвестный фильтр", show_alert=True)
        return
    
    set_setting('broadcast_filter', filter_key)
    
    # Обновляем экран
    msg_data = get_broadcast_message()
    has_message = msg_data is not None and msg_data.get('text')
    in_progress = is_broadcast_in_progress()
    user_count = count_users_for_broadcast(filter_key)
    
    text = (
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_main_kb(has_message, filter_key, in_progress, user_count)
    )
    await callback.answer(f"Фильтр: {BROADCAST_FILTERS[filter_key]}")


# ============================================================================
# ЗАПУСК РАССЫЛКИ
# ============================================================================

@router.callback_query(F.data == "broadcast_start")
async def broadcast_start(callback: CallbackQuery):
    """Показывает подтверждение рассылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем, не идёт ли уже рассылка
    if is_broadcast_in_progress():
        await callback.answer("⏳ Рассылка уже идёт!", show_alert=True)
        return
    
    # Проверяем наличие сообщения
    msg_data = get_broadcast_message()
    if not msg_data or not msg_data.get('text'):
        await callback.answer("❌ Сначала задайте сообщение!", show_alert=True)
        return
    
    current_filter = get_setting('broadcast_filter', 'all')
    user_count = count_users_for_broadcast(current_filter)
    
    if user_count == 0:
        await callback.answer("❌ Нет пользователей для рассылки!", show_alert=True)
        return
    
    filter_name = BROADCAST_FILTERS.get(current_filter, 'Все')
    
    text = (
        "🚀 <b>Подтверждение рассылки</b>\n\n"
        f"<b>Фильтр:</b> {filter_name}\n"
        f"<b>Получателей:</b> {user_count} чел.\n\n"
        "Начать рассылку?"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_confirm_kb(user_count)
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_in_progress")
async def broadcast_in_progress_callback(callback: CallbackQuery):
    """Уведомление о том, что рассылка уже идёт."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer("⏳ Рассылка уже идёт, дождитесь завершения", show_alert=True)


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, bot: Bot):
    """Запускает рассылку."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем ещё раз
    if is_broadcast_in_progress():
        await callback.answer("⏳ Рассылка уже идёт!", show_alert=True)
        return
    
    msg_data = get_broadcast_message()
    if not msg_data:
        await callback.answer("❌ Сообщение не задано!", show_alert=True)
        return
    
    current_filter = get_setting('broadcast_filter', 'all')
    user_ids = get_users_for_broadcast(current_filter)
    
    if not user_ids:
        await callback.answer("❌ Нет получателей!", show_alert=True)
        return
    
    # Устанавливаем флаг
    set_broadcast_in_progress(True)
    
    total = len(user_ids)
    sent = 0
    blocked = 0
    failed = 0
    
    # Начинаем рассылку
    await safe_edit_or_send(callback.message, 
        f"📤 <b>Рассылка запущена</b>\n\n"
        f"Отправлено: 0/{total}\n"
        f"🚫 Заблокировали бота: 0\n"
        f"⚠️ Ошибки отправки: 0"
    )
    await callback.answer()
    
    text = msg_data.get('text', '')
    photo_file_id = msg_data.get('photo_file_id')
    
    for i, user_id in enumerate(user_ids):
        try:
            if photo_file_id:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML"
                )
            sent += 1
        except Exception as e:
            if is_bot_blocked_error(e):
                mark_user_bot_blocked(user_id)
                blocked += 1
            elif isinstance(e, TelegramBadRequest):
                logger.warning(f"Ошибка отправки {user_id}: {e}")
                failed += 1
            else:
                logger.error(f"Неожиданная ошибка отправки {user_id}: {e}")
                failed += 1
        # Обновляем прогресс каждые 10 сообщений
        if (i + 1) % 10 == 0 or (i + 1) == total:
            try:
                await safe_edit_or_send(callback.message, 
                    f"📤 <b>Рассылка в процессе...</b>\n\n"
                    f"Отправлено: {sent}/{total}\n"
                    f"🚫 Заблокировали бота: {blocked}\n"
                    f"⚠️ Ошибки отправки: {failed}"
                )
            except TelegramBadRequest:
                pass  # Сообщение не изменилось
        
        # Задержка между сообщениями
        await asyncio.sleep(0.5)
    
    # Сбрасываем флаг
    set_broadcast_in_progress(False)
    
    # Итоговый отчёт
    await safe_edit_or_send(callback.message, 
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Отправлено: {sent}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибки отправки: {failed}",
        reply_markup=home_only_kb()
    )


# ============================================================================
# НАСТРОЙКИ АВТОУВЕДОМЛЕНИЙ
# ============================================================================

@router.callback_query(F.data == "broadcast_notifications")
async def broadcast_notifications(callback: CallbackQuery, state: FSMContext):
    """Показывает настройки автоуведомлений."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    days = int(get_setting('notification_days', '3'))
    
    text = (
        "⏰ <b>Автоуведомления</b>\n\n"
        "Бот автоматически напоминает пользователям об истечении VPN-ключей.\n\n"
        f"📅 Уведомлять за <b>{days}</b> дней до истечения\n"
        "📝 Текст уведомления настраивается отдельно"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_notifications_kb(days)
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_notify_days")
async def broadcast_notify_days(callback: CallbackQuery, state: FSMContext):
    """Начинает ввод количества дней."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_waiting_notify_days)
    
    current_days = get_setting('notification_days', '3')
    
    text = (
        "📅 <b>За сколько дней уведомлять?</b>\n\n"
        f"Текущее значение: <b>{current_days}</b> дней\n\n"
        "Введите число от 1 до 30:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_notify_back_kb()
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_notify_days)
async def broadcast_save_notify_days(message: Message, state: FSMContext):
    """Сохраняет количество дней для уведомления."""
    if not is_admin(message.from_user.id):
        return
    
    if not message.text or not message.text.isdigit():
        await safe_edit_or_send(message,
            "❌ Введите число!",
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    days = int(message.text)
    if not 1 <= days <= 30:
        await safe_edit_or_send(message,
            "❌ Число должно быть от 1 до 30!",
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    set_setting('notification_days', str(days))
    
    await safe_edit_or_send(message,
        f"✅ Теперь уведомления будут отправляться за <b>{days}</b> дней до истечения."
    )
    
    # Возвращаемся в настройки уведомлений
    await state.set_state(AdminStates.broadcast_menu)
    
    text = (
        "⏰ <b>Автоуведомления</b>\n\n"
        "Бот автоматически напоминает пользователям об истечении VPN-ключей.\n\n"
        f"📅 Уведомлять за <b>{days}</b> дней до истечения\n"
        "📝 Текст уведомления настраивается отдельно"
    )
    
    await safe_edit_or_send(message,
        text,
        reply_markup=broadcast_notifications_kb(days),
        force_new=True
    )


@router.callback_query(F.data == "broadcast_notify_text")
async def broadcast_notify_text(callback: CallbackQuery, state: FSMContext):
    """Показывает/редактирует текст уведомления через универсальный редактор."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.handlers.admin.message_editor import show_message_editor
    
    await show_message_editor(
        callback.message, state,
        key='notification_text',
        back_callback='broadcast_notifications',
        help_text=(
            "📝 <b>Справка: Текст уведомления об истечении</b>\n\n"
            "Переменные:\n"
            "• <code>%дней%</code> — количество дней до истечения\n"
            "• <code>%имяключа%</code> — имя ключа"
        ),
        allowed_types=['text', 'photo'],
    )
    await callback.answer()

