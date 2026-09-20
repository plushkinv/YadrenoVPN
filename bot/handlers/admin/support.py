import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.requests import (
    SupportThreadClosedError,
    claim_support_thread,
    create_support_thread,
    get_support_thread,
    get_user_by_telegram_id,
    get_user_by_id,
    mark_user_bot_blocked,
    record_support_message,
    release_support_thread_assignment,
)
from bot.keyboards.support import support_admin_cancel_kb, support_admin_home_kb
from bot.services.support import (
    cleanup_claimed_admin_notifications,
    extract_support_payload,
    format_support_user_line,
    support_identity_line,
    send_admin_message_to_user,
    support_thread_operation,
    support_unsupported_text,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_accounts import account_selector, selected_user, identity_line
from bot.utils.delivery import is_bot_blocked_error
from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


class _AdminSupportThreadState(RuntimeError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


async def _send_admin_support_message_locked(
    message: Message,
    *,
    thread_id: int,
    mode: str,
    admin_id: int,
    payload: dict,
):
    """Claims, delivers and records one admin reply under the thread lock."""
    newly_claimed = False
    async with support_thread_operation(thread_id):
        thread = get_support_thread(thread_id)
        if not thread:
            raise _AdminSupportThreadState("not_found")
        if thread.get("status") == "closed":
            raise _AdminSupportThreadState("closed")

        if mode == "reply":
            claim_status = claim_support_thread(thread_id, admin_id)
            if claim_status == "claimed":
                newly_claimed = True
            elif claim_status in {"assigned_other", "not_found", "closed"}:
                raise _AdminSupportThreadState(claim_status)

        try:
            await send_admin_message_to_user(
                message.bot,
                thread=thread,
                source_message=message,
            )
            record_support_message(
                thread_id,
                sender_type="admin",
                sender_telegram_id=admin_id,
                recipient_telegram_id=thread.get("user_telegram_id") if thread.get('channel') != 'web' else None,
                text_html=payload["text_html"],
                media_type=payload["media_type"],
                media_file_id=payload["media_file_id"],
                source_chat_id=payload["source_chat_id"],
                source_message_id=payload["source_message_id"],
            )
        except SupportThreadClosedError as exc:
            raise _AdminSupportThreadState("closed") from exc
        except Exception:
            if newly_claimed:
                release_support_thread_assignment(thread_id, admin_id)
            raise
    return thread, newly_claimed


async def _show_admin_thread_state_error(
    message: Message,
    state: FSMContext,
    status: str,
) -> None:
    if status == "assigned_other":
        text = (
            "⚠️ <b>Диалог уже в работе</b>\n\n"
            "Другой администратор уже взял это обращение."
        )
    elif status == "closed":
        text = (
            "❌ <b>Диалог закрыт</b>\n\n"
            "Закрытая тикет-сессия больше не принимает сообщения."
        )
    else:
        text = "❌ <b>Диалог не найден</b>"
    await safe_edit_or_send(
        message,
        text,
        reply_markup=support_admin_home_kb(),
        force_new=True,
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin_support_start:"))
async def admin_support_start(callback: CallbackQuery, state: FSMContext):
    """The admin starts a new support chain from the user's card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    try:
        selector = callback.data.split(":", 1)[1]
    except (TypeError, ValueError, IndexError):
        await callback.answer("❌ Некорректный пользователь", show_alert=True)
        return

    user = selected_user(selector)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.set_state(AdminStates.support_waiting_message)
    await state.update_data(
        support_mode="new",
        support_user_telegram_id=user['telegram_id'],
        support_user_id=user['id'],
        support_back_callback=f"admin_user_view:{account_selector(user)}",
    )

    text = (
        "💬 <b>Сообщение пользователю</b>\n\n"
        f"👤 Пользователь: {format_support_user_line(user)}\n"
        f"{identity_line(user)}\n\n"
        "Отправьте сообщение, которое нужно передать пользователю.\n\n"
        "Можно отправить текст, фото, видео или GIF."
    )
    if user['telegram_id'] is None:
        text += '\n\nСообщение будет доступно в веб-переписке через установленный модуль поддержки.'
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=support_admin_cancel_kb(f"admin_user_view:{account_selector(user)}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_support_reply:"))
async def admin_support_reply(callback: CallbackQuery, state: FSMContext):
    """The admin responds to the existing support chain."""
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

    if thread.get("status") == "closed":
        await callback.answer(
            "❌ Диалог закрыт и больше не принимает сообщения",
            show_alert=True,
        )
        return

    assigned_admin_id = thread.get("assigned_admin_id")
    if assigned_admin_id and int(assigned_admin_id) != callback.from_user.id:
        await callback.answer("Диалог уже взял другой администратор", show_alert=True)
        return

    user = (get_user_by_telegram_id(int(thread["user_telegram_id"])) if thread.get('user_telegram_id') is not None
            else get_user_by_id(thread['user_id']))
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.set_state(AdminStates.support_waiting_message)
    await state.update_data(
        support_mode="reply",
        support_thread_id=thread_id,
        support_user_telegram_id=thread.get("user_telegram_id"),
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
        f"{support_identity_line(thread)}\n"
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
    """Sends an admin message to the user."""
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
        user = (get_user_by_id(data['support_user_id']) if data.get('support_user_id')
                else get_user_by_telegram_id(int(user_telegram_id or 0)))
        if not user:
            await safe_edit_or_send(
                message,
                "❌ <b>Пользователь не найден</b>",
                reply_markup=support_admin_home_kb(),
                force_new=True,
            )
            await state.clear()
            return

        if user.get('telegram_id') is None:
            from database.requests import create_account_support_thread
            thread = create_account_support_thread(user['id'], admin_id=admin_id)
        else:
            thread = create_support_thread(
                int(user['telegram_id']), initiator_type="admin",
                initiator_admin_id=admin_id, assigned_admin_id=admin_id,
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

    else:
        await safe_edit_or_send(
            message,
            "❌ <b>Ошибка состояния</b>\n\nПовторите действие заново.",
            reply_markup=support_admin_home_kb(),
            force_new=True,
        )
        await state.clear()
        return

    thread_id = int(thread["id"])
    try:
        thread, newly_claimed = await _send_admin_support_message_locked(
            message,
            thread_id=thread_id,
            mode=str(mode),
            admin_id=admin_id,
            payload=payload,
        )
    except _AdminSupportThreadState as error:
        await _show_admin_thread_state_error(message, state, error.status)
        return
    except Exception as e:
        if thread.get('user_telegram_id') is not None and is_bot_blocked_error(e):
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
