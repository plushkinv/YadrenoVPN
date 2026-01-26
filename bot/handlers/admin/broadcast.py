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
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import ADMIN_IDS
from database.requests import (
    get_setting, set_setting,
    get_users_for_broadcast, count_users_for_broadcast
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
        "📢 *Рассылка*\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_main_kb(has_message, current_filter, in_progress, user_count),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой обработчик для разделителя."""
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
        "✉️ *Редактирование сообщения*\n\n"
        "Отправьте мне сообщение, которое хотите разослать.\n\n"
        "Можно отправить:\n"
        "• Текст (с форматированием)\n"
        "• Фото с подписью\n\n"
        "💡 Сообщение будет отправлено пользователям в точности как вы его прислали."
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_back_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_message)
async def broadcast_save_message(message: Message, state: FSMContext):
    """Сохраняет сообщение для рассылки."""
    if not is_admin(message.from_user.id):
        return
    
    text = None
    photo_file_id = None
    
    if message.photo:
        # Фото с подписью (md_text сохраняет форматирование из Telegram UI)
        photo_file_id = message.photo[-1].file_id
        text = message.md_text or ""
    elif message.text:
        # Просто текст (md_text конвертирует entities в Markdown)
        text = message.md_text
    else:
        await message.answer(
            "❌ Поддерживаются только текст или фото с подписью.",
            reply_markup=broadcast_back_kb()
        )
        return
    
    save_broadcast_message(text, photo_file_id)
    
    await message.answer(
        "✅ *Сообщение сохранено!*\n\n"
        "Теперь можете посмотреть превью или начать рассылку.",
        parse_mode="Markdown"
    )
    
    # Возвращаемся в меню рассылки
    await state.set_state(AdminStates.broadcast_menu)
    
    msg_data = get_broadcast_message()
    has_message = msg_data is not None
    current_filter = get_setting('broadcast_filter', 'all')
    in_progress = is_broadcast_in_progress()
    user_count = count_users_for_broadcast(current_filter)
    
    text = (
        "📢 *Рассылка*\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await message.answer(
        text,
        reply_markup=broadcast_main_kb(has_message, current_filter, in_progress, user_count),
        parse_mode="Markdown"
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
        await callback.message.answer_photo(
            photo=msg_data['photo_file_id'],
            caption=msg_data.get('text', ''),
            parse_mode="MarkdownV2"
        )
    else:
        await callback.message.answer(
            msg_data['text'],
            parse_mode="MarkdownV2"
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
        "📢 *Рассылка*\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_main_kb(has_message, filter_key, in_progress, user_count),
        parse_mode="Markdown"
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
        "🚀 *Подтверждение рассылки*\n\n"
        f"*Фильтр:* {filter_name}\n"
        f"*Получателей:* {user_count} чел.\n\n"
        "Начать рассылку?"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_confirm_kb(user_count),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_in_progress")
async def broadcast_in_progress_callback(callback: CallbackQuery):
    """Уведомление о том, что рассылка уже идёт."""
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
    
    # Начинаем рассылку
    await callback.message.edit_text(
        f"📤 *Рассылка запущена*\n\n"
        f"Отправлено: 0/{total}\n"
        f"🚫 Заблокировали бота: 0",
        parse_mode="Markdown"
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
                    parse_mode="MarkdownV2"
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2"
                )
            sent += 1
        except TelegramForbiddenError:
            # Пользователь заблокировал бота
            blocked += 1
        except TelegramBadRequest as e:
            logger.warning(f"Ошибка отправки {user_id}: {e}")
            blocked += 1
        except Exception as e:
            logger.error(f"Неожиданная ошибка отправки {user_id}: {e}")
            blocked += 1
        
        # Обновляем прогресс каждые 10 сообщений
        if (i + 1) % 10 == 0 or (i + 1) == total:
            try:
                await callback.message.edit_text(
                    f"📤 *Рассылка в процессе...*\n\n"
                    f"Отправлено: {sent}/{total}\n"
                    f"🚫 Заблокировали бота: {blocked}",
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass  # Сообщение не изменилось
        
        # Задержка между сообщениями
        await asyncio.sleep(0.5)
    
    # Сбрасываем флаг
    set_broadcast_in_progress(False)
    
    # Итоговый отчёт
    await callback.message.edit_text(
        f"✅ *Рассылка завершена!*\n\n"
        f"📤 Отправлено: {sent}\n"
        f"🚫 Заблокировали бота: {blocked}",
        reply_markup=home_only_kb(),
        parse_mode="Markdown"
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
        "⏰ *Автоуведомления*\n\n"
        "Бот автоматически напоминает пользователям об истечении VPN-ключей.\n\n"
        f"📅 Уведомлять за *{days}* дней до истечения\n"
        "📝 Текст уведомления настраивается отдельно"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_notifications_kb(days),
        parse_mode="Markdown"
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
        "📅 *За сколько дней уведомлять?*\n\n"
        f"Текущее значение: *{current_days}* дней\n\n"
        "Введите число от 1 до 30:"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_notify_back_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_notify_days)
async def broadcast_save_notify_days(message: Message, state: FSMContext):
    """Сохраняет количество дней для уведомления."""
    if not is_admin(message.from_user.id):
        return
    
    if not message.text or not message.text.isdigit():
        await message.answer(
            "❌ Введите число!",
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    days = int(message.text)
    if not 1 <= days <= 30:
        await message.answer(
            "❌ Число должно быть от 1 до 30!",
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    set_setting('notification_days', str(days))
    
    await message.answer(
        f"✅ Теперь уведомления будут отправляться за *{days}* дней до истечения.",
        parse_mode="Markdown"
    )
    
    # Возвращаемся в настройки уведомлений
    await state.set_state(AdminStates.broadcast_menu)
    
    text = (
        "⏰ *Автоуведомления*\n\n"
        "Бот автоматически напоминает пользователям об истечении VPN-ключей.\n\n"
        f"📅 Уведомлять за *{days}* дней до истечения\n"
        "📝 Текст уведомления настраивается отдельно"
    )
    
    await message.answer(
        text,
        reply_markup=broadcast_notifications_kb(days),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "broadcast_notify_text")
async def broadcast_notify_text(callback: CallbackQuery, state: FSMContext):
    """Показывает/редактирует текст уведомления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_waiting_notify_text)
    
    current_text = get_setting('notification_text', '')
    
    text = (
        "📝 *Текст уведомления*\n\n"
        "Текущий текст:\n"
        f"```\n{current_text}\n```\n\n"
        "Отправьте новый текст.\n"
        "Используйте `{days}` для вставки количества оставшихся дней."
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=broadcast_notify_back_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_notify_text)
async def broadcast_save_notify_text(message: Message, state: FSMContext):
    """Сохраняет текст уведомления."""
    if not is_admin(message.from_user.id):
        return
    
    if not message.text:
        await message.answer(
            "❌ Отправьте текстовое сообщение!",
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    set_setting('notification_text', message.text)
    
    await message.answer(
        "✅ Текст уведомления сохранён!",
        parse_mode="Markdown"
    )
    
    # Возвращаемся в настройки уведомлений
    await state.set_state(AdminStates.broadcast_menu)
    
    days = int(get_setting('notification_days', '3'))
    
    text = (
        "⏰ *Автоуведомления*\n\n"
        "Бот автоматически напоминает пользователям об истечении VPN-ключей.\n\n"
        f"📅 Уведомлять за *{days}* дней до истечения\n"
        "📝 Текст уведомления настраивается отдельно"
    )
    
    await message.answer(
        text,
        reply_markup=broadcast_notifications_kb(days),
        parse_mode="Markdown"
    )
