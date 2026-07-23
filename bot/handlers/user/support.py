"""Database-backed user support flow."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.services.support import extract_support_payload, send_user_message_to_admins
from bot.states.user_states import SupportUserStates
from bot.utils.page_dynamic_data import build_support_context_values
from bot.utils.page_renderer import render_page
from bot.utils.user_pages import render_access_blocked_page
from database.requests import (
    create_support_thread,
    get_or_create_user,
    get_support_thread,
    is_user_banned,
    record_support_message,
)

logger = logging.getLogger(__name__)

router = Router()

SUPPORT_START_PAGE_KEY = "support_start"


async def _start_support_dialog(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    thread_id: int | None = None,
) -> None:
    user_id = target.from_user.id
    message = target.message if isinstance(target, CallbackQuery) else target

    if is_user_banned(user_id):
        await render_access_blocked_page(message, force_new=not isinstance(target, CallbackQuery))
        return

    await state.set_state(SupportUserStates.waiting_for_message)
    await state.update_data(support_thread_id=thread_id)
    await render_page(
        target,
        page_key="support_reply_start" if thread_id else SUPPORT_START_PAGE_KEY,
        context=build_support_context_values(thread_id=thread_id),
        force_new=not isinstance(target, CallbackQuery),
    )


@router.message(Command("support"), StateFilter("*"))
async def cmd_support(message: Message, state: FSMContext):
    """Open built-in support from a command."""
    await _start_support_dialog(message, state)


@router.callback_query(F.data == "support_start")
async def support_start_callback(callback: CallbackQuery, state: FSMContext):
    """Open built-in support from a page button."""
    await _start_support_dialog(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("support_reply:"))
async def support_reply_callback(callback: CallbackQuery, state: FSMContext):
    """Continue an existing support thread owned by the user."""
    try:
        thread_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        logger.warning("Malformed support reply callback: %r", callback.data)
        await render_page(callback, page_key="support_thread_unavailable")
        await callback.answer()
        return

    thread = get_support_thread(thread_id)
    if not thread or int(thread["user_telegram_id"]) != callback.from_user.id:
        logger.warning(
            "Unavailable support thread %s requested by user %s",
            thread_id,
            callback.from_user.id,
        )
        await render_page(callback, page_key="support_thread_unavailable")
        await callback.answer()
        return

    await _start_support_dialog(callback, state, thread_id=thread_id)
    await callback.answer()


@router.message(SupportUserStates.waiting_for_message, ~F.text.startswith("/"))
async def process_support_message(message: Message, state: FSMContext):
    """Persist a user message and relay it to the assigned administrator(s)."""
    user_id = message.from_user.id
    if is_user_banned(user_id):
        await render_access_blocked_page(message, force_new=True)
        await state.clear()
        return

    payload = extract_support_payload(message)
    if not payload:
        await render_page(message, page_key="support_format_unsupported", force_new=True)
        return

    data = await state.get_data()
    thread_id = data.get("support_thread_id")
    user, _ = get_or_create_user(
        user_id,
        message.from_user.username,
        first_name=getattr(message.from_user, "first_name", None),
        last_name=getattr(message.from_user, "last_name", None),
    )

    if thread_id:
        thread = get_support_thread(int(thread_id))
        if not thread or int(thread["user_telegram_id"]) != user_id:
            logger.warning("Support FSM references unavailable thread %s", thread_id)
            await render_page(message, page_key="support_thread_unavailable", force_new=True)
            await state.clear()
            return
    else:
        thread = create_support_thread(user_id, initiator_type="user")
        if not thread:
            logger.error("Failed to create support thread for user %s", user_id)
            await render_page(message, page_key="support_failed", force_new=True)
            await state.clear()
            return

    try:
        record_support_message(
            int(thread["id"]),
            sender_type="user",
            sender_telegram_id=user_id,
            recipient_telegram_id=thread.get("assigned_admin_id"),
            text_html=payload["text_html"],
            media_type=payload["media_type"],
            media_file_id=payload["media_file_id"],
            source_chat_id=payload["source_chat_id"],
            source_message_id=payload["source_message_id"],
        )
        result = await send_user_message_to_admins(
            message.bot,
            thread=thread,
            user=user,
            source_message=message,
        )
    except Exception:
        logger.exception("Failed to process support message for user %s", user_id)
        await state.clear()
        await render_page(message, page_key="support_failed", force_new=True)
        return

    await state.clear()
    page_key = "support_sent" if result["sent"] > 0 else "support_failed"
    await render_page(message, page_key=page_key, force_new=True)
