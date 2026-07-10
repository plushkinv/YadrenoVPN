import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from database.requests import (
    create_support_thread,
    get_or_create_user,
    get_support_thread,
    is_user_banned,
    record_support_message,
)
from bot.keyboards.support import support_user_cancel_kb
from bot.services.support import (
    extract_support_payload,
    send_user_message_to_admins,
)
from bot.states.user_states import SupportUserStates
from bot.utils.page_renderer import render_page, render_page_text
from bot.utils.page_dynamic_data import build_support_context_values
from bot.utils.placeholders import apply_page_placeholders
from bot.utils.user_pages import render_access_blocked_page

logger = logging.getLogger(__name__)

router = Router()

SUPPORT_START_PAGE_KEY = "support_start"
SUPPORT_STATUS_PAGE_KEY = "support_status"


def default_support_start_page_text() -> str:
    """Дефолтный текст входа во встроенную поддержку."""
    return "%поддержка_заголовок%\n\n%поддержка_инструкция%"


def build_support_start_page_context(*, thread_id: int | None = None) -> dict[str, str]:
    """Собирает context для страницы входа в поддержку."""
    return build_support_context_values(thread_id=thread_id)


def build_support_status_page_context(*, title_html: str, body_html: str) -> dict[str, str]:
    """Собирает context для страницы результата обращения в поддержку."""
    return {
        "support_status_title_html": title_html,
        "support_status_body_html": body_html,
    }


async def render_support_status_page(
    target: Message | CallbackQuery,
    *,
    title_html: str,
    body_html: str | None = None,
    body_text: str | None = None,
    visibility: dict[str, bool] | None = None,
    append_buttons: list[list[InlineKeyboardButton]] | None = None,
    force_new: bool = False,
) -> None:
    """Рендерит page-backed статус обращения в поддержку."""
    if body_html is None:
        from bot.utils.text import escape_html

        body_html = escape_html('' if body_text is None else str(body_text))

    await render_page(
        target,
        page_key=SUPPORT_STATUS_PAGE_KEY,
        context=build_support_status_page_context(
            title_html=title_html,
            body_html=body_html,
        ),
        visibility=visibility,
        append_buttons=append_buttons,
        force_new=force_new,
    )


def render_support_start_page_text(context: dict[str, str]) -> str:
    """Рендерит текст support_start из pages с fallback на дефолт."""
    text = render_page_text(SUPPORT_START_PAGE_KEY, context=context)
    if text is not None:
        return text
    fallback_context = {"page_key": SUPPORT_START_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_support_start_page_text(),
        context=fallback_context,
    )


async def _start_support_dialog(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    thread_id: int | None = None,
) -> None:
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id
        message = target.message
    else:
        user_id = target.from_user.id
        message = target

    if is_user_banned(user_id):
        await render_access_blocked_page(message, force_new=not isinstance(target, CallbackQuery))
        return

    await state.set_state(SupportUserStates.waiting_for_message)
    await state.update_data(support_thread_id=thread_id)

    await render_page(
        target,
        page_key=SUPPORT_START_PAGE_KEY,
        context=build_support_start_page_context(thread_id=thread_id),
        append_buttons=support_user_cancel_kb().inline_keyboard,
        force_new=not isinstance(target, CallbackQuery),
    )


@router.message(Command("support"), StateFilter("*"))
async def cmd_support(message: Message, state: FSMContext):
    """Команда входа в поддержку."""
    await _start_support_dialog(message, state)


@router.callback_query(F.data == "support_start")
async def support_start_callback(callback: CallbackQuery, state: FSMContext):
    """Кнопка входа в поддержку с главной страницы."""
    await _start_support_dialog(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("support_reply:"))
async def support_reply_callback(callback: CallbackQuery, state: FSMContext):
    """Пользователь отвечает в существующей цепочке поддержки."""
    try:
        thread_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("❌ Некорректный диалог", show_alert=True)
        return

    thread = get_support_thread(thread_id)
    if not thread or int(thread["user_telegram_id"]) != callback.from_user.id:
        await callback.answer("❌ Диалог не найден", show_alert=True)
        return

    await _start_support_dialog(callback, state, thread_id=thread_id)
    await callback.answer()


@router.message(SupportUserStates.waiting_for_message, ~F.text.startswith("/"))
async def process_support_message(message: Message, state: FSMContext):
    """Принимает сообщение пользователя и отправляет его админу или всем админам."""
    user_id = message.from_user.id
    if is_user_banned(user_id):
        await render_access_blocked_page(message, force_new=True)
        await state.clear()
        return

    payload = extract_support_payload(message)
    if not payload:
        await render_support_status_page(
            message,
            title_html="❌ <b>Формат не поддерживается</b>",
            body_text="Отправьте текст, фото, видео или GIF.",
            visibility={"btn_back_main": False},
            append_buttons=support_user_cancel_kb().inline_keyboard,
            force_new=True,
        )
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
            await render_support_status_page(
                message,
                title_html="❌ <b>Диалог не найден</b>",
                body_text="Начните новое обращение в поддержку.",
                force_new=True,
            )
            await state.clear()
            return
    else:
        thread = create_support_thread(user_id, initiator_type="user")
        if not thread:
            await render_support_status_page(
                message,
                title_html="❌ <b>Не удалось создать обращение</b>",
                body_text="Попробуйте позже.",
                force_new=True,
            )
            await state.clear()
            return

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
    await state.clear()

    if result["sent"] <= 0:
        await render_support_status_page(
            message,
            title_html="⚠️ <b>Сообщение не отправлено</b>",
            body_text="Сейчас ни один администратор не получил обращение. Попробуйте позже.",
            force_new=True,
        )
        return

    await render_support_status_page(
        message,
        title_html="✅ <b>Сообщение отправлено</b>",
        body_text="Ответ придёт сюда, в бот.",
        force_new=True,
    )
