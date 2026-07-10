import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.requests import (
    claim_support_thread,
    create_support_thread,
    get_support_thread,
    get_user_by_telegram_id,
    mark_user_bot_blocked,
    record_support_message,
    release_support_thread_assignment,
)
from bot.keyboards.support import support_admin_cancel_kb, support_admin_home_kb
from bot.services.support import (
    cleanup_claimed_admin_notifications,
    extract_support_payload,
    format_support_user_line,
    send_admin_message_to_user,
    support_unsupported_text,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.delivery import is_bot_blocked_error
from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data.startswith("admin_support_start:"))
async def admin_support_start(callback: CallbackQuery, state: FSMContext):
    """Админ начинает новую цепочку поддержки из карточки пользователя."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    try:
        user_telegram_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("❌ Некорректный пользователь", show_alert=True)
        return

    user = get_user_by_telegram_id(user_telegram_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.set_state(AdminStates.support_waiting_message)
    await state.update_data(
        support_mode="new",
        support_user_telegram_id=user_telegram_id,
        support_back_callback=f"admin_user_view:{user_telegram_id}",
    )

    text = (
        "💬 <b>Сообщение пользователю</b>\n\n"
        f"👤 Пользователь: {format_support_user_line(user)}\n"
        f"📱 Telegram ID: <code>{user_telegram_id}</code>\n\n"
        "Отправьте сообщение, которое нужно передать пользователю.\n\n"
        "Можно отправить текст, фото, видео или GIF."
    )
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=support_admin_cancel_kb(f"admin_user_view:{user_telegram_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_support_reply:"))
async def admin_support_reply(callback: CallbackQuery, state: FSMContext):
    """Админ отвечает в существующую цепочку поддержки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    try:
        thread_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("❌ Некорректный диалог", show_alert=True)
        return

    thread = get_support_thread(thread_id)
    if not thread:
        await callback.answer("❌ Диалог не найден", show_alert=True)
        return

    assigned_admin_id = thread.get("assigned_admin_id")
    if assigned_admin_id and int(assigned_admin_id) != callback.from_user.id:
        await callback.answer("Диалог уже взял другой администратор", show_alert=True)
        return

    user = get_user_by_telegram_id(int(thread["user_telegram_id"]))
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.set_state(AdminStates.support_waiting_message)
    await state.update_data(
        support_mode="reply",
        support_thread_id=thread_id,
        support_user_telegram_id=int(thread["user_telegram_id"]),
        support_back_callback="admin_panel",
    )

    note = (
        "После отправки ответа диалог закрепится за вами."
        if not assigned_admin_id else
        "Ответ уйдёт пользователю в эту цепочку."
    )
    text = (
        "💬 <b>Ответ пользователю</b>\n\n"
        f"👤 Пользователь: {format_support_user_line(user)}\n"
        f"📱 Telegram ID: <code>{thread['user_telegram_id']}</code>\n"
        f"🧵 Диалог: <code>{thread_id}</code>\n\n"
        f"{note}\n\n"
        "Отправьте текст, фото, видео или GIF."
    )
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=support_admin_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.support_waiting_message, ~F.text.startswith("/"))
async def process_admin_support_message(message: Message, state: FSMContext):
    """Отправляет сообщение админа пользователю."""
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        return

    payload = extract_support_payload(message)
    if not payload:
        data = await state.get_data()
        await safe_edit_or_send(
            message,
            support_unsupported_text(),
            reply_markup=support_admin_cancel_kb(data.get("support_back_callback", "admin_panel")),
            force_new=True,
        )
        return

    data = await state.get_data()
    mode = data.get("support_mode")
    newly_claimed = False
    thread = None

    if mode == "new":
        user_telegram_id = data.get("support_user_telegram_id")
        user = get_user_by_telegram_id(int(user_telegram_id or 0))
        if not user:
            await safe_edit_or_send(
                message,
                "❌ <b>Пользователь не найден</b>",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return

        thread = create_support_thread(
            int(user_telegram_id),
            initiator_type="admin",
            initiator_admin_id=admin_id,
            assigned_admin_id=admin_id,
        )
        if not thread:
            await safe_edit_or_send(
                message,
                "❌ <b>Не удалось создать диалог</b>\n\nПопробуйте позже.",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return

    elif mode == "reply":
        thread_id = int(data.get("support_thread_id") or 0)
        thread = get_support_thread(thread_id)
        if not thread:
            await safe_edit_or_send(
                message,
                "❌ <b>Диалог не найден</b>",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return

        claim_status = claim_support_thread(thread_id, admin_id)
        if claim_status == "claimed":
            newly_claimed = True
        elif claim_status == "assigned_other":
            await safe_edit_or_send(
                message,
                "⚠️ <b>Диалог уже в работе</b>\n\n"
                "Другой администратор уже взял это обращение.",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return
        elif claim_status == "not_found":
            await safe_edit_or_send(
                message,
                "❌ <b>Диалог не найден</b>",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return

        thread = get_support_thread(thread_id)
    else:
        await safe_edit_or_send(
            message,
            "❌ <b>Ошибка состояния</b>\n\nПовторите действие заново.",
            reply_markup=support_admin_home_kb(),
            force_new=True,
        )
        await state.clear()
        return

    try:
        await send_admin_message_to_user(
            message.bot,
            thread=thread,
            source_message=message,
        )
    except Exception as e:
        if newly_claimed:
            release_support_thread_assignment(int(thread["id"]), admin_id)
        if is_bot_blocked_error(e):
            mark_user_bot_blocked(int(thread["user_telegram_id"]))
            text = (
                "📵 <b>Сообщение не отправлено</b>\n\n"
                "Пользователь заблокировал бота."
            )
        else:
            logger.warning(
                "Не удалось отправить сообщение поддержки пользователю %s: %s",
                thread["user_telegram_id"],
                e,
            )
            text = (
                "⚠️ <b>Сообщение не отправлено</b>\n\n"
                "Telegram вернул ошибку доставки. Попробуйте позже."
            )
        await safe_edit_or_send(
            message,
            text,
            reply_markup=support_admin_home_kb(),
            force_new=True,
        )
        await state.clear()
        return

    record_support_message(
        int(thread["id"]),
        sender_type="admin",
        sender_telegram_id=admin_id,
        recipient_telegram_id=int(thread["user_telegram_id"]),
        text_html=payload["text_html"],
        media_type=payload["media_type"],
        media_file_id=payload["media_file_id"],
        source_chat_id=payload["source_chat_id"],
        source_message_id=payload["source_message_id"],
    )

    if newly_claimed:
        await cleanup_claimed_admin_notifications(
            message.bot,
            thread_id=int(thread["id"]),
            claimed_admin_id=admin_id,
        )

    await state.clear()
    await safe_edit_or_send(
        message,
        "✅ <b>Сообщение отправлено</b>\n\n"
        "Если пользователь ответит, сообщение придёт вам в эту цепочку.",
        reply_markup=support_admin_home_kb(),
        force_new=True,
    )
