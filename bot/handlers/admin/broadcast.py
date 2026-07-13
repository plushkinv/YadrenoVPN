"""
Handlers for the “Mailmail” section in the admin panel.

Functional:
- Sending messages to all users with filters
- Setting up auto-notifications about key expiration
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
    broadcast_stop_kb, broadcast_notifications_kb, broadcast_back_kb,
    broadcast_notify_back_kb, home_only_kb,
    BROADCAST_FILTERS
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send
from bot.utils.delivery import is_bot_blocked_error
from bot.utils.event_placeholders import build_user_event_context, render_event_placeholders

router = Router()

BROADCAST_IN_PROGRESS_KEY = 'broadcast_in_progress'
BROADCAST_STOP_REQUESTED_KEY = 'broadcast_stop_requested'
BROADCAST_STOP_REQUESTED = 'stop_requested'
BROADCAST_STALE_RESET = 'stale_reset'
BROADCAST_IDLE = 'idle'

_broadcast_runtime_active = False
_broadcast_state_lock = asyncio.Lock()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================




def get_broadcast_message() -> dict | None:
    """
    Receives a saved message for distribution.
    
    Returns:
        Dictionary with keys 'text' and 'photo_file_id' or None
    """
    msg_json = get_setting('broadcast_message')
    if msg_json:
        try:
            return json.loads(msg_json)
        except json.JSONDecodeError:
            return None
    return None


def save_broadcast_message(text: str, photo_file_id: str | None = None) -> None:
    """Saves the message for distribution."""
    data = {'text': text, 'photo_file_id': photo_file_id}
    set_setting('broadcast_message', json.dumps(data, ensure_ascii=False))


def render_broadcast_message_text(text: str, telegram_id: int | None) -> str:
    """Renders the mailing text in the event context of a specific recipient."""
    context = build_user_event_context(telegram_id)
    return render_event_placeholders(text, 'broadcast', context, mode='html')


def is_broadcast_in_progress() -> bool:
    """Checks whether the mailing is currently in progress."""
    return get_setting(BROADCAST_IN_PROGRESS_KEY, '0') == '1'


def set_broadcast_in_progress(value: bool) -> None:
    """Sets the broadcast flag."""
    set_setting(BROADCAST_IN_PROGRESS_KEY, '1' if value else '0')


def is_broadcast_stop_requested() -> bool:
    """Checks whether the administrator has requested to stop the current distribution."""
    return get_setting(BROADCAST_STOP_REQUESTED_KEY, '0') == '1'


def set_broadcast_stop_requested(value: bool) -> None:
    """Sets the soft stop flag for broadcasting."""
    set_setting(BROADCAST_STOP_REQUESTED_KEY, '1' if value else '0')


def is_broadcast_runtime_active() -> bool:
    """Returns True if the mailing loop is alive in the current bot process."""
    return _broadcast_runtime_active


def _set_broadcast_runtime_active(value: bool) -> None:
    """Updates the in-memory flag of live mailing."""
    global _broadcast_runtime_active
    _broadcast_runtime_active = value


def reset_broadcast_state() -> None:
    """Resets all distribution status flags."""
    set_broadcast_in_progress(False)
    set_broadcast_stop_requested(False)


async def try_mark_broadcast_started() -> bool:
    """Atomically reserves the right to launch a newsletter."""
    async with _broadcast_state_lock:
        if is_broadcast_runtime_active() or is_broadcast_in_progress():
            return False

        _set_broadcast_runtime_active(True)
        set_broadcast_in_progress(True)
        set_broadcast_stop_requested(False)
        return True


async def finish_broadcast_state() -> None:
    """Removes the runtime flag and resets the flags after the broadcast is completed."""
    async with _broadcast_state_lock:
        _set_broadcast_runtime_active(False)
        reset_broadcast_state()


async def request_broadcast_stop_or_reset() -> str:
    """
    Requests to stop live broadcasting or resets a stuck DB flag.

    Returns:
        One of BROADCAST_STOP_REQUESTED, BROADCAST_STALE_RESET, BROADCAST_IDLE.
    """
    async with _broadcast_state_lock:
        if is_broadcast_runtime_active():
            set_broadcast_stop_requested(True)
            return BROADCAST_STOP_REQUESTED

        if is_broadcast_in_progress() or is_broadcast_stop_requested():
            reset_broadcast_state()
            return BROADCAST_STALE_RESET

        return BROADCAST_IDLE


def get_broadcast_menu_text(in_progress: bool = False) -> str:
    """Generates the text of the main mailing screen."""
    text = (
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте сообщение всем пользователям бота.\n\n"
        "1️⃣ Отредактируйте сообщение\n"
        "2️⃣ Выберите фильтр получателей\n"
        "3️⃣ Нажмите «Начать рассылку»"
    )

    if in_progress:
        text += "\n\n⏳ Сейчас идёт рассылка. Её можно остановить кнопкой ниже."

    return text


async def render_broadcast_menu(
    message: Message,
    current_filter: str | None = None,
    force_new: bool = False,
) -> None:
    """Shows the current mailing main screen."""
    msg_data = get_broadcast_message()
    has_message = msg_data is not None and msg_data.get('text')
    current_filter = current_filter or get_setting('broadcast_filter', 'all')
    in_progress = is_broadcast_in_progress()
    user_count = count_users_for_broadcast(current_filter)

    await safe_edit_or_send(
        message,
        get_broadcast_menu_text(in_progress),
        reply_markup=broadcast_main_kb(has_message, current_filter, in_progress, user_count),
        force_new=force_new,
    )


# ============================================================================
# MAIN NEWSLETTER SCREEN
# ============================================================================

@router.callback_query(F.data == "admin_broadcast")
async def show_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the main screen of the mailing section."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_menu)
    await render_broadcast_menu(callback.message)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """An empty handler for the separator."""
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()


# ============================================================================
# EDITING A MESSAGE
# ============================================================================

@router.callback_query(F.data == "broadcast_edit_message")
async def broadcast_edit_message(callback: CallbackQuery, state: FSMContext):
    """Starts editing the message for distribution."""
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
    """Saves the message for distribution."""
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
    
    # Returning to the mailing menu
    await state.set_state(AdminStates.broadcast_menu)
    
    await render_broadcast_menu(message, force_new=True)


# ============================================================================
# PREVIEW MESSAGE
# ============================================================================

@router.callback_query(F.data == "broadcast_preview")
async def broadcast_preview(callback: CallbackQuery):
    """Shows a preview of the message for the newsletter."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    msg_data = get_broadcast_message()
    
    if not msg_data or not msg_data.get('text'):
        await callback.answer("❌ Сообщение не задано", show_alert=True)
        return
    
    await callback.answer("📤 Отправляю превью...")
    
    preview_text = render_broadcast_message_text(
        msg_data.get('text', ''),
        callback.from_user.id,
    )

    # Send the preview as a separate message
    if msg_data.get('photo_file_id'):
        await safe_edit_or_send(callback.message,
            photo=msg_data['photo_file_id'],
            text=preview_text,
            force_new=True
        )
    else:
        await safe_edit_or_send(callback.message,
            text=preview_text,
            force_new=True
        )


# ============================================================================
# FILTERS
# ============================================================================

@router.callback_query(F.data.startswith("broadcast_filter:"))
async def broadcast_set_filter(callback: CallbackQuery):
    """Sets the recipient filter."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    filter_key = callback.data.split(":")[1]
    
    if filter_key not in BROADCAST_FILTERS:
        await callback.answer("❌ Неизвестный фильтр", show_alert=True)
        return
    
    set_setting('broadcast_filter', filter_key)
    
    await render_broadcast_menu(callback.message, current_filter=filter_key)
    await callback.answer(f"Фильтр: {BROADCAST_FILTERS[filter_key]}")


# ============================================================================
# LAUNCH NEWSLETTER
# ============================================================================

@router.callback_query(F.data == "broadcast_start")
async def broadcast_start(callback: CallbackQuery):
    """Shows confirmation of mailing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Checking to see if the mailing is already in progress
    if is_broadcast_in_progress():
        await callback.answer("⏳ Рассылка уже идёт!", show_alert=True)
        return
    
    # Checking for a message
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
    """Notification that the mailing is already underway."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer("⏳ Рассылка уже идёт. Её можно остановить кнопкой ниже.", show_alert=True)


@router.callback_query(F.data == "broadcast_stop")
async def broadcast_stop(callback: CallbackQuery):
    """Stops the current broadcast or resets a stuck flag."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    result = await request_broadcast_stop_or_reset()

    if result == BROADCAST_STOP_REQUESTED:
        await safe_edit_or_send(
            callback.message,
            "🛑 <b>Остановка рассылки</b>\n\n"
            "Остановка запрошена. Рассылка прекратится перед следующей отправкой.",
        )
        await callback.answer("🛑 Остановка запрошена")
        return

    if result == BROADCAST_STALE_RESET:
        await render_broadcast_menu(callback.message)
        await callback.answer("✅ Зависшая рассылка сброшена", show_alert=True)
        return

    await render_broadcast_menu(callback.message)
    await callback.answer("ℹ️ Активной рассылки нет", show_alert=True)


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, bot: Bot):
    """Launches a newsletter."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    if is_broadcast_in_progress() or is_broadcast_runtime_active():
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
    
    if not await try_mark_broadcast_started():
        await callback.answer("⏳ Рассылка уже идёт!", show_alert=True)
        return
    
    total = len(user_ids)
    sent = 0
    blocked = 0
    failed = 0
    stopped = False
    unexpected_error = None
    callback_answered = False

    text = msg_data.get('text', '')
    photo_file_id = msg_data.get('photo_file_id')

    try:
        await safe_edit_or_send(
            callback.message,
            f"📤 <b>Рассылка запущена</b>\n\n"
            f"Отправлено: 0/{total}\n"
            f"🚫 Заблокировали бота: 0\n"
            f"⚠️ Ошибки отправки: 0",
            reply_markup=broadcast_stop_kb(),
        )
        await callback.answer()
        callback_answered = True

        for user_id in user_ids:
            if is_broadcast_stop_requested():
                stopped = True
                break

            try:
                rendered_text = render_broadcast_message_text(text, int(user_id))
                if photo_file_id:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo_file_id,
                        caption=rendered_text,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(
                        chat_id=user_id,
                        text=rendered_text,
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

            processed = sent + blocked + failed

            # We update progress every 10 processed recipients.
            if processed % 10 == 0 or processed == total:
                try:
                    await safe_edit_or_send(
                        callback.message,
                        f"📤 <b>Рассылка в процессе...</b>\n\n"
                        f"Отправлено: {sent}/{total}\n"
                        f"🚫 Заблокировали бота: {blocked}\n"
                        f"⚠️ Ошибки отправки: {failed}",
                        reply_markup=broadcast_stop_kb(),
                    )
                except TelegramBadRequest:
                    pass  # The message has not changed

            if processed < total and is_broadcast_stop_requested():
                stopped = True
                break

            if processed < total:
                await asyncio.sleep(0.5)
    except Exception as e:
        unexpected_error = e
        logger.exception("Техническая ошибка во время рассылки")
    finally:
        await finish_broadcast_state()

    processed = sent + blocked + failed

    if unexpected_error is not None:
        if not callback_answered:
            try:
                await callback.answer("⚠️ Рассылка прервана технической ошибкой", show_alert=True)
            except Exception:
                pass

        try:
            await safe_edit_or_send(
                callback.message,
                f"⚠️ <b>Рассылка прервана технической ошибкой</b>\n\n"
                f"📤 Отправлено: {sent}\n"
                f"🚫 Заблокировали бота: {blocked}\n"
                f"⚠️ Ошибки отправки: {failed}\n"
                f"📌 Обработано: {processed}/{total}\n\n"
                "Флаг рассылки сброшен, можно запустить новую рассылку.",
                reply_markup=home_only_kb(),
            )
        except Exception as report_error:
            logger.error(f"Не удалось показать отчёт о прерванной рассылке: {report_error}")
        return

    if stopped:
        remaining = max(total - processed, 0)
        await safe_edit_or_send(
            callback.message,
            f"🛑 <b>Рассылка остановлена</b>\n\n"
            f"📤 Отправлено: {sent}\n"
            f"🚫 Заблокировали бота: {blocked}\n"
            f"⚠️ Ошибки отправки: {failed}\n"
            f"⏸️ Не отправлено: {remaining}",
            reply_markup=home_only_kb(),
        )
        return

    await safe_edit_or_send(
        callback.message,
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Отправлено: {sent}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибки отправки: {failed}",
        reply_markup=home_only_kb()
    )


# ============================================================================
# AUTO NOTIFICATION SETTINGS
# ============================================================================

@router.callback_query(F.data == "broadcast_notifications")
async def broadcast_notifications(callback: CallbackQuery, state: FSMContext):
    """Shows auto notification settings."""
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
    """Begins entering the number of days."""
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
    """Stores the number of days for notification."""
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
    
    # Returning to notification settings
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
    """Shows/edits notification text through a universal editor."""
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
            "• <code>%ключ_дней_до_окончания%</code> — количество дней до истечения\n"
            "• <code>%ключ_имя%</code> — имя ключа"
        ),
        allowed_types=['text', 'photo', 'video', 'animation'],
    )
    await callback.answer()

