"""Trusted local controls for the contextual broadcast editor."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    BROADCAST_FILTERS,
    broadcast_confirm_kb,
    broadcast_editor_dirty_exit_kb,
    broadcast_editor_kb,
)
from bot.services.broadcast_content import BROADCAST_KIND_POLL, preview_poll
from bot.services.broadcast_editor import (
    BroadcastEditorError,
    broadcast_stage_is_dirty,
    create_broadcast_confirmation,
    discard_broadcast_editor_stage,
    ensure_broadcast_editor_stage,
    get_broadcast_editor_state,
    save_broadcast_editor_stage,
)
from bot.services.yadreno_admin import (
    YADRENO_ADMIN_BROADCAST_TOPIC_ID,
    YadrenoAdminError,
    start_new_chat,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.event_placeholders import (
    build_user_event_context,
    render_event_placeholders,
)
from bot.utils.text import escape_html, safe_edit_or_send
from database.requests import get_yadreno_admin_api_key

from .broadcast import is_broadcast_in_progress, render_broadcast_menu

logger = logging.getLogger(__name__)
router = Router()


def _state_error_text(state: dict[str, Any]) -> str:
    status = str(state.get("status") or "")
    if status in {"conflict", "stage_conflict", "config_conflict"}:
        return (
            "⚠️ <b>Черновик изменился</b>\n\n"
            "Рабочий материал или версия черновика уже обновлены. "
            "Попросите редактора перечитать состояние и повторить правку."
        )
    return (
        "❌ <b>Не удалось сохранить черновик</b>\n\n"
        + escape_html(str(state.get("error") or state.get("validation_error") or "Проверьте материал."))
    )


async def _send_content_preview(bot: Bot, message: Message, content: dict[str, Any]) -> None:
    """Send a real Telegram preview of staged or saved material."""
    if content.get("kind") == BROADCAST_KIND_POLL:
        await preview_poll(bot, content, chat_id=message.chat.id)
        return
    rendered = render_event_placeholders(
        str(content.get("text") or ""),
        "broadcast",
        build_user_event_context(message.chat.id),
        mode="html",
    )
    photo_file_id = content.get("photo_file_id")
    if photo_file_id:
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_file_id,
            caption=rendered,
            parse_mode="HTML",
        )
        return
    await bot.send_message(chat_id=message.chat.id, text=rendered, parse_mode="HTML")


async def _close_hub_editor_lane(telegram_id: int) -> None:
    """Close the remote editor session without blocking local exit."""
    api_key = get_yadreno_admin_api_key()
    if not api_key:
        return
    try:
        await start_new_chat(
            telegram_id,
            api_key,
            topic_id=YADRENO_ADMIN_BROADCAST_TOPIC_ID,
        )
    except YadrenoAdminError as error:
        logger.warning("Could not close broadcast editor hub lane: %s", error)


async def _finish_exit(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    await _close_hub_editor_lane(telegram_id)
    await asyncio.to_thread(discard_broadcast_editor_stage, telegram_id)
    await state.set_state(AdminStates.broadcast_menu)
    await render_broadcast_menu(callback.message)
    await callback.answer("Редактор закрыт")


@router.callback_query(F.data == "broadcast_editor_preview")
async def broadcast_editor_preview(callback: CallbackQuery, bot: Bot) -> None:
    """Preview the local stage without changing working settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    stage, _snapshot = await asyncio.to_thread(
        ensure_broadcast_editor_stage,
        callback.from_user.id,
    )
    content = stage.get("content")
    state_payload = await asyncio.to_thread(get_broadcast_editor_state, callback.from_user.id)
    if not content or state_payload.get("validation_error"):
        await callback.answer(
            str(state_payload.get("validation_error") or "Материал не подготовлен"),
            show_alert=True,
        )
        return
    try:
        await _send_content_preview(bot, callback.message, content)
    except (TelegramAPIError, ValueError, TypeError) as error:
        logger.warning("Broadcast editor preview failed: %s", error)
        await callback.answer("Не удалось показать превью. Проверьте материал.", show_alert=True)
        return
    await callback.answer("Превью отправлено")


@router.callback_query(F.data == "broadcast_editor_save")
async def broadcast_editor_save(callback: CallbackQuery) -> None:
    """Atomically apply the stage while keeping the editor dialogue open."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    result = await asyncio.to_thread(save_broadcast_editor_stage, callback.from_user.id)
    if result.get("status") != "saved":
        await safe_edit_or_send(
            callback.message,
            _state_error_text(result),
            reply_markup=broadcast_editor_kb(),
        )
        await callback.answer("Не сохранено", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "💾 <b>Черновик сохранён</b>\n\n"
        "Рабочий материал, фильтр и выбранный стиль обновлены. "
        "Диалог с редактором остаётся открытым.",
        reply_markup=broadcast_editor_kb(),
    )
    await callback.answer("Сохранено")


@router.callback_query(F.data == "broadcast_editor_launch")
async def broadcast_editor_launch(callback: CallbackQuery, bot: Bot) -> None:
    """Save, preview, and create the second trusted launch confirmation."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    if is_broadcast_in_progress():
        await callback.answer("Рассылка уже идёт", show_alert=True)
        return
    saved = await asyncio.to_thread(save_broadcast_editor_stage, callback.from_user.id)
    if saved.get("status") != "saved":
        await safe_edit_or_send(
            callback.message,
            _state_error_text(saved),
            reply_markup=broadcast_editor_kb(),
        )
        await callback.answer("Не готово к запуску", show_alert=True)
        return
    stage, _snapshot = await asyncio.to_thread(
        ensure_broadcast_editor_stage,
        callback.from_user.id,
    )
    try:
        await _send_content_preview(bot, callback.message, stage["content"])
        confirmation = await asyncio.to_thread(
            create_broadcast_confirmation,
            callback.from_user.id,
        )
    except (BroadcastEditorError, TelegramAPIError, ValueError, TypeError) as error:
        logger.warning("Broadcast editor launch preparation failed: %s", error)
        await safe_edit_or_send(
            callback.message,
            "❌ <b>Не удалось подготовить запуск</b>\n\n" + escape_html(str(error)),
            reply_markup=broadcast_editor_kb(),
        )
        await callback.answer("Запуск не подготовлен", show_alert=True)
        return

    filter_name = BROADCAST_FILTERS.get(
        str(confirmation["filter"]),
        str(confirmation["filter"]),
    )
    count = int(confirmation["recipient_count"])
    await safe_edit_or_send(
        callback.message,
        "🚀 <b>Проверьте настоящее превью выше</b>\n\n"
        f"<b>Фильтр:</b> {escape_html(filter_name)}\n"
        f"<b>Получателей:</b> {count}\n\n"
        "Рассылка начнётся только после кнопки ниже.",
        reply_markup=broadcast_confirm_kb(count, str(confirmation["token"])),
    )
    await callback.answer("Ожидаю подтверждение")


@router.callback_query(F.data == "broadcast_editor_exit")
async def broadcast_editor_exit(callback: CallbackQuery, state: FSMContext) -> None:
    """Exit immediately when clean, or ask how to handle unsaved edits."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    dirty = await asyncio.to_thread(broadcast_stage_is_dirty, callback.from_user.id)
    if not dirty:
        await _finish_exit(callback, state)
        return
    await safe_edit_or_send(
        callback.message,
        "⚠️ <b>Есть несохранённые правки</b>\n\nЧто сделать перед выходом?",
        reply_markup=broadcast_editor_dirty_exit_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_editor_exit_save")
async def broadcast_editor_exit_save(callback: CallbackQuery, state: FSMContext) -> None:
    """Save staged changes and leave the editor."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    result = await asyncio.to_thread(save_broadcast_editor_stage, callback.from_user.id)
    if result.get("status") != "saved":
        await safe_edit_or_send(
            callback.message,
            _state_error_text(result),
            reply_markup=broadcast_editor_dirty_exit_kb(),
        )
        await callback.answer("Не удалось сохранить", show_alert=True)
        return
    await _finish_exit(callback, state)


@router.callback_query(F.data == "broadcast_editor_exit_discard")
async def broadcast_editor_exit_discard(callback: CallbackQuery, state: FSMContext) -> None:
    """Discard staged changes and leave the editor."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await _finish_exit(callback, state)


@router.callback_query(F.data == "broadcast_editor_exit_continue")
async def broadcast_editor_exit_continue(callback: CallbackQuery) -> None:
    """Return from the dirty-exit prompt to the editor controls."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "✍️ <b>Продолжаем редактирование</b>\n\n"
        "Напишите следующую правку обычным сообщением — повторять <code>/yaa</code> не нужно.",
        reply_markup=broadcast_editor_kb(),
    )
    await callback.answer()
