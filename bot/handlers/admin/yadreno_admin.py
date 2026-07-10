"""
Диалог с агентом Yadreno Admin и контекстная команда /yaa.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    yadreno_admin_agent_kb,
    yadreno_admin_cancel_key_kb,
    yadreno_admin_chat_kb,
    yadreno_admin_no_key_kb,
)
from bot.services.page_context import get_page_context
from bot.services.yadreno_admin import (
    UPLOAD_TMP_DIR,
    YADRENO_ADMIN_CHAT_TOPIC_ID,
    YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID,
    YADRENO_ADMIN_YAA_TOPIC_ID,
    YadrenoAdminError,
    YadrenoAdminLatest,
    YadrenoAdminProgressEvent,
    YadrenoAdminUpload,
    cancel_active_dialog,
    detect_public_server_ip,
    fetch_latest_dialog_event,
    get_active_request_id,
    is_local_request_active,
    run_dialog,
    run_dialog_with_uploads,
    resume_active_dialog,
    start_new_chat,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.page_renderer import (
    build_visible_keyboard_snapshot,
    get_page_data,
    get_page_stored_data,
    render_page,
    serialize_inline_button_rows,
)
from bot.utils.text import (
    escape_html,
    get_message_text_for_storage,
    safe_edit_or_send,
)
from database.requests import (
    create_bot_database_backup,
    get_display_timezone,
    get_setting,
    get_yadreno_admin_api_key,
    set_yadreno_admin_server_ip,
    set_yadreno_admin_api_key,
)

router = Router()

YAA_REDACTED_USER_KEY = "[redacted_user_key]"
YAA_KEY_DELIVERY_PAGE = "key_delivery"
YAA_KEY_DELIVERY_CONTEXT_RAW = "key_delivery_raw_value"
YAA_KEY_DELIVERY_CONTEXT_KEYS = frozenset({
    YAA_KEY_DELIVERY_CONTEXT_RAW,
    "key_raw_value",
})
YAA_KEY_DELIVERY_PLACEHOLDERS = frozenset({
    "%ключ_для_копирования%".casefold(),
    "%ключ_ссылка%".casefold(),
    "%ключ_ссылка_url%".casefold(),
})
YADRENO_ADMIN_FSM_TOPIC_KEY = "yadreno_topic_id"
YADRENO_ADMIN_ALLOWED_TOPIC_IDS = frozenset({
    YADRENO_ADMIN_CHAT_TOPIC_ID,
    YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID,
    YADRENO_ADMIN_YAA_TOPIC_ID,
})
YADRENO_ADMIN_UPLOAD_MAX_MB = 10
YADRENO_ADMIN_UPLOAD_MAX_BYTES = YADRENO_ADMIN_UPLOAD_MAX_MB * 1024 * 1024
YADRENO_ADMIN_UPLOAD_MAX_FILES = 5
YADRENO_ADMIN_ALBUM_DEBOUNCE_SEC = 1.0


@dataclass
class _YadrenoAlbumBuffer:
    """Short-lived Telegram media group buffer for one Yadreno Admin turn."""

    user_id: int
    topic_id: int
    api_key: str
    media_group_id: str
    first_message: Message
    messages: list[Message] = field(default_factory=list)
    flush_task: asyncio.Task | None = None


_yadreno_album_buffers: dict[tuple[int, int, str], _YadrenoAlbumBuffer] = {}
_yadreno_album_locks: dict[tuple[int, int, str], asyncio.Lock] = {}


def _missing_key_text() -> str:
    """Текст экрана настройки api_key."""
    return (
        "🤖 <b>Yadreno Admin</b>\n\n"
        "Чтобы начать диалог с агентом, сначала укажите свой <code>api_key</code>.\n\n"
        "🔑 <b>Как получить ключ:</b>\n"
        "Получите его в <a href=\"https://t.me/YadrenoAdmin_Bot\">@YadrenoAdmin_Bot</a> в разделе «Профиль».\n\n"
        "🎬 <b>Что умеет агент:</b>\n"
        "Посмотрите <a href=\"https://www.youtube.com/watch?v=ACPu03aAJns\">видео с примерами возможностей</a>.\n\n"
        "💬 <b>Остались вопросы?</b>\n"
        "Задайте их в <a href=\"https://t.me/YadrenoAdmin_Bot\">@YadrenoAdmin_Bot</a> — он бесплатно проконсультирует вас по любым вопросам YadrenoVPN."
    )


def _chat_intro_text() -> str:
    """Текст экрана чата с агентом."""
    return (
        "🤖 <b>Yadreno Admin</b>\n\n"
        "Напишите задачу обычным сообщением — агент сможет смотреть и менять этот сервер.\n\n"
        "Чтобы остановить текущий запрос, отправьте <code>/cancel</code>."
    )


def _customization_intro_text() -> str:
    """Build the intro text for the separate YadrenoVPN customization chat."""
    return (
        "🛠 <b>Кастомизация YadrenoVPN</b>\n\n"
        "Этот чат предназначен для настройки страниц, кнопок, текстов, медиа "
        "и пользовательских расширений YadrenoVPN.\n\n"
        "По умолчанию агент работает через безопасные слои кастомизации: "
        "поля <code>*_custom</code>, штатные настройки, публичные API и "
        "<code>custom_extensions/</code>. Изменение ядра проекта отключено "
        "скрытой настройкой, если администратор явно не разрешил его.\n\n"
        "Опишите, что нужно изменить. Для редактирования конкретной страницы "
        "удобнее открыть её в боте и вызвать <code>/yaa</code> прямо оттуда."
    )


def _progress_text(title: str, content: str) -> str:
    """Форматирует progress-событие хаба для HTML-сообщения Telegram."""
    body = escape_html(content.strip()) if content else "Обновляю статус..."
    return f"{title}\n\n{body}"


def _format_final_response(content: str, viewer_url: str | None = None) -> str:
    """Форматирует финальный ответ агента для Telegram."""
    response = content or "Готово."
    if viewer_url:
        response += f'\n\n<a href="{escape_html(viewer_url)}">Полная версия ответа</a>'
    return response


def _format_latest_event(latest: YadrenoAdminLatest) -> str | None:
    """Форматирует snapshot для кнопки ручного восстановления."""
    if latest.final is not None:
        return _format_final_response(
            latest.final.content,
            latest.final.viewer_url,
        )
    if latest.progress is not None:
        title = (
            "📋 <b>План работы</b>"
            if latest.progress.event == "task_update"
            else "🤖 <b>Yadreno Admin</b>"
        )
        return _progress_text(title, latest.progress.content)
    return None


def _callback_topic_id(data: str | None, prefix: str) -> int:
    """Достаёт topic_id из callback data, сохраняя legacy fallback."""
    raw = data or ""
    if raw == prefix:
        return YADRENO_ADMIN_CHAT_TOPIC_ID
    _, _, suffix = raw.partition(":")
    try:
        return int(suffix)
    except (TypeError, ValueError):
        return YADRENO_ADMIN_CHAT_TOPIC_ID


def _normalize_yadreno_topic_id(raw_topic_id: Any) -> int:
    """Возвращает разрешённый topic_id чата агента."""
    try:
        topic_id = int(raw_topic_id)
    except (TypeError, ValueError):
        return YADRENO_ADMIN_CHAT_TOPIC_ID
    if topic_id in YADRENO_ADMIN_ALLOWED_TOPIC_IDS:
        return topic_id
    return YADRENO_ADMIN_CHAT_TOPIC_ID


async def _activate_yadreno_chat_lane(
    state: FSMContext,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> int:
    """Переводит администратора в чат агента и запоминает активную lane."""
    normalized_topic_id = _normalize_yadreno_topic_id(topic_id)
    await state.set_state(AdminStates.yadreno_chat)
    await state.update_data(**{YADRENO_ADMIN_FSM_TOPIC_KEY: normalized_topic_id})
    return normalized_topic_id


async def _current_yadreno_chat_topic_id(state: FSMContext) -> int:
    """Читает активную lane чата агента из FSM."""
    data = await state.get_data()
    return _normalize_yadreno_topic_id(data.get(YADRENO_ADMIN_FSM_TOPIC_KEY))


class _YadrenoProgressRenderer:
    """Редактирует промежуточные события Yadreno Admin в текущем чате."""

    def __init__(self, anchor: Message, topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID):
        self._anchor = anchor
        self._topic_id = topic_id
        self._live_status_message: Message | None = anchor
        self._status_messages: dict[str, Message] = {}
        self._task_message: Message | None = None
        self._last_live_status_text = _progress_text(
            "🤖 <b>Yadreno Admin</b>",
            "⏳ Ведётся агентская работа...",
        )

    @property
    def final_target(self) -> Message:
        """Сообщение, которое нужно заменить финальным ответом."""
        return self._live_status_message or self._anchor

    async def handle(self, event: YadrenoAdminProgressEvent) -> None:
        """Показывает status/task_update и продолжает polling."""
        if event.event == "status":
            await self._show_status(event)
            return
        if event.event == "task_update":
            await self._show_task_update(event)

    async def _show_status(self, event: YadrenoAdminProgressEvent) -> None:
        slot = event.slot or "status"
        text = _progress_text("🤖 <b>Yadreno Admin</b>", event.content)
        is_live_status = slot in {"status", "heartbeat"}

        if is_live_status:
            self._last_live_status_text = text
            target = self._live_status_message or self._anchor
            updated = await safe_edit_or_send(
                target,
                text,
                reply_markup=yadreno_admin_agent_kb(self._topic_id),
            )
            self._live_status_message = updated
            self._status_messages[slot] = updated
            if self._task_message is None:
                self._anchor = updated
            return

        target = self._status_messages.get(slot)
        force_new = target is None
        if target is None:
            target = self._live_status_message or self._anchor

        updated = await safe_edit_or_send(
            target,
            text,
            reply_markup=yadreno_admin_agent_kb(self._topic_id),
            force_new=force_new,
        )
        self._status_messages[slot] = updated

    async def _show_task_update(self, event: YadrenoAdminProgressEvent) -> None:
        text = _progress_text("📋 <b>План работы</b>", event.content)
        if self._task_message is not None:
            self._task_message = await safe_edit_or_send(
                self._task_message,
                text,
                reply_markup=yadreno_admin_agent_kb(self._topic_id),
            )
            return

        target = self._live_status_message or self._anchor
        self._task_message = await safe_edit_or_send(
            target,
            text,
            reply_markup=yadreno_admin_agent_kb(self._topic_id),
        )
        self._anchor = self._task_message
        self._live_status_message = await safe_edit_or_send(
            self._task_message,
            self._last_live_status_text,
            reply_markup=yadreno_admin_agent_kb(self._topic_id),
            force_new=True,
        )
        self._status_messages["status"] = self._live_status_message
        self._status_messages["heartbeat"] = self._live_status_message

    async def delete_progress_messages(self) -> None:
        """Удаляет все сообщения progress-рендерера без падения сценария."""
        messages = [
            self._anchor,
            self._task_message,
            self._live_status_message,
            *self._status_messages.values(),
        ]
        seen: set[int] = set()
        for msg in messages:
            if msg is None:
                continue
            message_id = getattr(msg, "message_id", None)
            key = int(message_id) if message_id is not None else id(msg)
            if key in seen:
                continue
            seen.add(key)
            try:
                await msg.delete()
            except Exception:
                pass


async def _show_yadreno_entry(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Показывает экран настройки ключа или открывает режим чата."""
    api_key = get_yadreno_admin_api_key()
    message = target.message if isinstance(target, CallbackQuery) else target
    if not api_key:
        await state.clear()
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
        )
        return

    await _activate_yadreno_chat_lane(state, YADRENO_ADMIN_CHAT_TOPIC_ID)
    await safe_edit_or_send(
        message,
        _chat_intro_text(),
        reply_markup=yadreno_admin_chat_kb(YADRENO_ADMIN_CHAT_TOPIC_ID),
    )


async def _show_yadreno_customization_entry(
    target: Message | CallbackQuery,
    state: FSMContext,
) -> None:
    """Open the separate YadrenoVPN customization lane."""
    api_key = get_yadreno_admin_api_key()
    message = target.message if isinstance(target, CallbackQuery) else target
    if not api_key:
        await state.clear()
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
        )
        return

    await _activate_yadreno_chat_lane(state, YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID)
    await safe_edit_or_send(
        message,
        _customization_intro_text(),
        reply_markup=yadreno_admin_chat_kb(YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID),
    )


@router.callback_query(F.data == "admin_yadreno")
async def show_yadreno_admin(callback: CallbackQuery, state: FSMContext):
    """Открывает раздел Yadreno Admin."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    await _show_yadreno_entry(callback, state)


@router.callback_query(F.data == "admin_yadreno_customization")
async def show_yadreno_customization(callback: CallbackQuery, state: FSMContext):
    """Open the separate YadrenoVPN customization section."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    await _show_yadreno_customization_entry(callback, state)


@router.callback_query(F.data.startswith("admin_yadreno_new_chat"))
async def start_yadreno_new_chat(callback: CallbackQuery, state: FSMContext):
    """Открывает новый чат, если агент сейчас не занят."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer()
        await _show_yadreno_entry(callback, state)
        return

    try:
        result = await start_new_chat(
            callback.from_user.id,
            api_key,
            topic_id=_callback_topic_id(callback.data, "admin_yadreno_new_chat"),
        )
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if result.status == "busy":
        await callback.answer(
            result.response_text or "Агент ещё работает. Нажмите «Отмена».",
            show_alert=True,
        )
        return

    topic_id = _callback_topic_id(callback.data, "admin_yadreno_new_chat")
    await _activate_yadreno_chat_lane(state, topic_id)
    await safe_edit_or_send(
        callback.message,
        "🆕 <b>Новый чат открыт</b>\n\n"
        "Контекст сброшен. Напишите новую задачу обычным сообщением.",
        reply_markup=yadreno_admin_chat_kb(topic_id),
    )
    await callback.answer("Новый чат открыт")


@router.callback_query(F.data.startswith("admin_yadreno_cancel"))
async def cancel_yadreno_dialog_button(callback: CallbackQuery):
    """Отменяет активный запрос агента с кнопки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer("Сначала укажите api_key", show_alert=True)
        return

    topic_id = _callback_topic_id(callback.data, "admin_yadreno_cancel")
    if get_active_request_id(callback.from_user.id, topic_id=topic_id) is None:
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    try:
        cancel_result = await cancel_active_dialog(
            callback.from_user.id,
            api_key,
            topic_id=topic_id,
        )
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if cancel_result.status == "idle":
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    if cancel_result.status == "orphan_cleared":
        await safe_edit_or_send(
            callback.message,
            "🛑 <b>Запрос остановлен</b>\n\n"
            "Хаб подтвердил, что задача уже не выполнялась, и безопасно снял зависший lock. "
            "Можно начать новый диалог.",
            reply_markup=yadreno_admin_chat_kb(topic_id),
        )
        await callback.answer("Зависший запрос очищен")
        return

    if cancel_result.status == "unsafe_unknown":
        await safe_edit_or_send(
            callback.message,
            "⚠️ <b>Состояние не определено</b>\n\n"
            f"{escape_html(cancel_result.response_text or 'Безопасно очистить запрос не удалось.')}",
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )
        await callback.answer("Lock не очищен", show_alert=True)
        return

    if cancel_result.status in {"orphan_suspected", "orphan_confirmed"}:
        await safe_edit_or_send(
            callback.message,
            "⚠️ <b>Проверяю зависший запрос</b>\n\n"
            f"{escape_html(cancel_result.response_text or 'Повторите отмену через несколько секунд.')}",
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )
        await callback.answer("Повторите отмену через пару секунд", show_alert=True)
        return

    await safe_edit_or_send(
        callback.message,
        "🛑 <b>Запрос отменяется</b>\n\n"
        "Хаб видит живую задачу. Агент завершит работу на ближайшей безопасной точке "
        "и сам снимет lock.",
        reply_markup=yadreno_admin_agent_kb(topic_id),
    )
    await callback.answer("Отмена отправлена")


@router.callback_query(F.data.startswith("admin_yadreno_nudge"))
async def nudge_yadreno_dialog(callback: CallbackQuery):
    """Показывает последний snapshot через /latest без consume polling."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer("Сначала укажите api_key", show_alert=True)
        return

    topic_id = _callback_topic_id(callback.data, "admin_yadreno_nudge")
    try:
        latest = await fetch_latest_dialog_event(
            callback.from_user.id,
            api_key,
            topic_id=topic_id,
        )
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if latest is None:
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    text = _format_latest_event(latest)
    if text is None:
        active_request_id = get_active_request_id(
            callback.from_user.id,
            topic_id=topic_id,
        )
        if latest.resume_allowed and active_request_id is not None and not is_local_request_active(
            callback.from_user.id,
            topic_id=topic_id,
        ):
            await callback.answer("Восстанавливаю связь")
            progress = _YadrenoProgressRenderer(
                callback.message,
                topic_id=topic_id,
            )
            try:
                final = await resume_active_dialog(
                    callback.from_user.id,
                    api_key,
                    topic_id=topic_id,
                    progress_callback=progress.handle,
                )
            except YadrenoAdminError as e:
                await safe_edit_or_send(
                    progress.final_target,
                    f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
                    reply_markup=yadreno_admin_agent_kb(topic_id),
                )
                return
            if final is not None:
                await safe_edit_or_send(
                    progress.final_target,
                    _format_final_response(final.content, final.viewer_url),
                    reply_markup=yadreno_admin_agent_kb(topic_id),
                )
                return
        await callback.answer("Пока свежих данных нет", show_alert=False)
        return

    active_request_id = get_active_request_id(
        callback.from_user.id,
        topic_id=topic_id,
    )
    if latest.resume_allowed and latest.final is None and active_request_id is not None and not is_local_request_active(
        callback.from_user.id,
        topic_id=topic_id,
    ):
        await callback.answer("Восстанавливаю связь")
        progress = _YadrenoProgressRenderer(
            await safe_edit_or_send(
                callback.message,
                text,
                reply_markup=yadreno_admin_agent_kb(topic_id),
            ),
            topic_id=topic_id,
        )
        try:
            final = await resume_active_dialog(
                callback.from_user.id,
                api_key,
                topic_id=topic_id,
                progress_callback=progress.handle,
            )
        except YadrenoAdminError as e:
            await safe_edit_or_send(
                progress.final_target,
                f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
                reply_markup=yadreno_admin_agent_kb(topic_id),
            )
            return
        if final is not None:
            await safe_edit_or_send(
                progress.final_target,
                _format_final_response(final.content, final.viewer_url),
                reply_markup=yadreno_admin_agent_kb(topic_id),
            )
            return

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=yadreno_admin_agent_kb(topic_id),
    )
    await callback.answer("Обновил")


@router.callback_query(F.data == "admin_yadreno_set_key")
async def start_yadreno_key_input(callback: CallbackQuery, state: FSMContext):
    """Переводит администратора в режим ввода api_key."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.yadreno_waiting_api_key)
    await state.update_data(
        yadreno_editing_message=callback.message,
        yadreno_editing_message_id=callback.message.message_id,
    )
    await safe_edit_or_send(
        callback.message,
        "🔑 <b>Ключ Yadreno Admin</b>\n\n"
        "Отправьте свой <code>api_key</code> из раздела «Профиль» в "
        "<a href=\"https://t.me/YadrenoAdmin_Bot\">@YadrenoAdmin_Bot</a>.",
        reply_markup=yadreno_admin_cancel_key_kb(),
    )
    await callback.answer()


@router.message(AdminStates.yadreno_waiting_api_key, F.text, ~F.text.startswith('/'))
async def save_yadreno_key(message: Message, state: FSMContext):
    """Сохраняет api_key и возвращает администратора в чат."""
    if not is_admin(message.from_user.id):
        return

    api_key = get_message_text_for_storage(message, 'plain')
    if not api_key:
        await safe_edit_or_send(
            message,
            "❌ <b>Ключ пустой</b>\n\nОтправьте непустой <code>api_key</code>.",
            reply_markup=yadreno_admin_cancel_key_kb(),
            force_new=True,
        )
        return

    data = await state.get_data()
    editing_message = data.get('yadreno_editing_message')

    try:
        await message.delete()
    except Exception:
        pass

    set_yadreno_admin_api_key(api_key)
    server_ip = await detect_public_server_ip(use_cache=False)
    set_yadreno_admin_server_ip(server_ip)

    await _activate_yadreno_chat_lane(state, YADRENO_ADMIN_CHAT_TOPIC_ID)
    target = editing_message or message
    ip_line = (
        f"\n\n🌐 IP сервера: <code>{escape_html(server_ip)}</code>"
        if server_ip
        else "\n\n🌐 IP сервера автоматически определить не удалось."
    )
    await safe_edit_or_send(
        target,
        "✅ <b>Ключ сохранён</b>\n\n"
        "Теперь можно писать задачи агенту обычными сообщениями."
        f"{ip_line}",
        reply_markup=yadreno_admin_chat_kb(YADRENO_ADMIN_CHAT_TOPIC_ID),
        force_new=editing_message is None,
    )


@router.message(Command("cancel"), AdminStates.yadreno_chat)
async def cancel_yadreno_dialog(message: Message, state: FSMContext):
    """Отменяет текущий запрос агента."""
    if not is_admin(message.from_user.id):
        return
    topic_id = await _current_yadreno_chat_topic_id(state)
    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
            force_new=True,
        )
        return

    try:
        cancelled = await cancel_active_dialog(
            message.from_user.id,
            api_key,
            topic_id=topic_id,
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            message,
            f"❌ <b>Не удалось отменить запрос</b>\n\n{escape_html(str(e))}",
            force_new=True,
        )
        return

    text = (
        "🛑 <b>Запрос отменяется</b>\n\n"
        "Агент завершит работу на следующей итерации."
        if cancelled
        else "ℹ️ <b>Активного запроса нет</b>"
    )
    await safe_edit_or_send(
        message,
        text,
        reply_markup=yadreno_admin_agent_kb(topic_id),
        force_new=True,
    )


@router.message(AdminStates.yadreno_chat, F.text, ~F.text.startswith('/'))
async def handle_yadreno_chat_message(message: Message, state: FSMContext):
    """Отправляет сообщение администратора агенту и показывает ответ."""
    if not is_admin(message.from_user.id):
        return

    topic_id = await _current_yadreno_chat_topic_id(state)
    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
            force_new=True,
        )
        return

    text = get_message_text_for_storage(message, 'plain')
    thinking = await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n⏳ Думаю...",
        reply_markup=yadreno_admin_agent_kb(topic_id),
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(
        thinking,
        topic_id=topic_id,
    )
    try:
        final = await run_dialog(
            message.from_user.id,
            api_key,
            text,
            topic_id=topic_id,
            progress_callback=progress.handle,
        )
        await safe_edit_or_send(
            progress.final_target,
            _format_final_response(final.content, final.viewer_url),
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )


def _serialize_for_compare(data: Any) -> str:
    """Сериализует структуру страницы для сравнения до/после."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def _get_yaa_editable_state(page_key: str) -> dict[str, Any]:
    """Возвращает состояние, изменение которого должно перерисовать /yaa-экран."""
    state: dict[str, Any] = {
        'page': get_page_data(page_key),
        'display_timezone': get_display_timezone(),
    }
    if page_key in {'my_keys', 'my_keys_empty'}:
        from bot.utils.my_keys_page import (
            DEFAULT_MY_KEYS_ITEM_TEMPLATE,
            MY_KEYS_ITEM_TEMPLATE_SETTING,
        )

        state['my_keys_item_template'] = get_setting(
            MY_KEYS_ITEM_TEMPLATE_SETTING,
            DEFAULT_MY_KEYS_ITEM_TEMPLATE,
        )
    return state


def _safe_upload_filename(raw_name: str | None, fallback: str) -> str:
    """Возвращает безопасное имя файла без директорий."""
    name = Path(raw_name or "").name.strip()
    return name or fallback


def _is_gif_document(document: Any | None) -> bool:
    """Проверяет, является ли Telegram document GIF-анимацией."""
    if document is None:
        return False
    mime_type = (getattr(document, "mime_type", None) or "").lower()
    file_name = (getattr(document, "file_name", None) or "").lower()
    return mime_type == "image/gif" or file_name.endswith(".gif")


def _is_metadata_only_media(message: Message) -> bool:
    """Возвращает True для видео/GIF, которые не скачиваются агентом."""
    return bool(message.video or message.animation or _is_gif_document(message.document))


def _message_upload_size(message: Message) -> int | None:
    """Возвращает размер uploadable-вложения без запроса get_file()."""
    if message.photo:
        return getattr(message.photo[-1], "file_size", None)
    if message.document:
        return getattr(message.document, "file_size", None)
    return None


def _format_upload_size(size_bytes: int) -> str:
    """Форматирует размер файла для сообщения администратору."""
    return f"{size_bytes / (1024 * 1024):.1f} МБ"


def _ensure_upload_size_allowed(message: Message) -> None:
    """Отклоняет uploadable-файлы больше локального лимита до get_file()."""
    size_bytes = _message_upload_size(message)
    if size_bytes is None or size_bytes <= YADRENO_ADMIN_UPLOAD_MAX_BYTES:
        return

    raise YadrenoAdminError(
        "Файл слишком большой для анализа: "
        f"{_format_upload_size(size_bytes)}. "
        f"Лимит загрузки в Yadreno Admin — {YADRENO_ADMIN_UPLOAD_MAX_MB} МБ. "
        "Видео и GIF для медиа страницы передаются без скачивания через Telegram file_id; "
        "для анализа отправьте фото/скриншот или файл меньшего размера."
    )


def _message_upload_meta(message: Message) -> tuple[str, str, str] | None:
    """Достаёт file_id, имя и MIME только для uploadable photo/document."""
    if message.photo:
        photo = message.photo[-1]
        filename = f"photo_{message.message_id}.jpg"
        return photo.file_id, filename, "image/jpeg"

    document = message.document
    if document:
        if _is_gif_document(document):
            return None
        filename = _safe_upload_filename(
            document.file_name,
            f"document_{message.message_id}",
        )
        content_type = document.mime_type or "application/octet-stream"
        return document.file_id, filename, content_type

    return None


async def _download_yadreno_upload(message: Message) -> list[YadrenoAdminUpload]:
    """Скачивает вложение Telegram во временный файл для upload API."""
    meta = _message_upload_meta(message)
    if meta is None:
        return []

    file_id, filename, content_type = meta
    _ensure_upload_size_allowed(message)
    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".bin"
    local_path = UPLOAD_TMP_DIR / (
        f"{message.from_user.id}_{message.message_id}_{uuid.uuid4().hex}{suffix}"
    )

    try:
        telegram_file = await message.bot.get_file(file_id)
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower():
            raise YadrenoAdminError(
                "Файл слишком большой для загрузки через Telegram Bot API. "
                f"Лимит анализа в Yadreno Admin — {YADRENO_ADMIN_UPLOAD_MAX_MB} МБ. "
                "Видео и GIF для медиа страницы передаются без скачивания через Telegram file_id."
            ) from e
        raise YadrenoAdminError(f"Telegram не дал скачать файл: {e}") from e
    if not telegram_file.file_path:
        raise YadrenoAdminError("Telegram не вернул путь к файлу")
    await message.bot.download_file(telegram_file.file_path, destination=local_path)
    return [
        YadrenoAdminUpload(
            path=local_path,
            filename=filename,
            content_type=content_type,
        )
    ]


def _cleanup_yadreno_uploads(uploads: list[YadrenoAdminUpload]) -> None:
    """Удаляет временные upload-файлы best-effort."""
    for upload in uploads:
        try:
            upload.path.unlink(missing_ok=True)
        except Exception:
            pass


def _extract_yaa_attachment_data(message: Message) -> dict[str, str] | None:
    """Возвращает компактные данные прикреплённого к /yaa файла."""
    if message.photo:
        photo = message.photo[-1]
        return {
            "media_type": "photo",
            "telegram_file_id": photo.file_id,
            "page_media_type": "photo",
            "usage": "ready_bot_api_file_id",
        }

    if message.video:
        video = message.video
        return {
            "media_type": "video",
            "telegram_file_id": video.file_id,
            "file_name": getattr(video, "file_name", None) or "",
            "mime_type": getattr(video, "mime_type", None) or "",
            "page_media_type": "video",
            "usage": "ready_bot_api_file_id",
            "analysis_supported": "false",
        }

    if message.animation:
        animation = message.animation
        return {
            "media_type": "animation",
            "telegram_file_id": animation.file_id,
            "file_name": getattr(animation, "file_name", None) or "",
            "mime_type": getattr(animation, "mime_type", None) or "",
            "page_media_type": "animation",
            "usage": "ready_bot_api_file_id",
            "analysis_supported": "false",
        }

    document = message.document
    if document and _is_gif_document(document):
        return {
            "media_type": "animation",
            "telegram_file_id": document.file_id,
            "file_name": document.file_name or "",
            "mime_type": document.mime_type or "",
            "page_media_type": "animation",
            "usage": "ready_bot_api_file_id",
            "analysis_supported": "false",
        }

    if document and (document.mime_type or "").startswith("image/"):
        return {
            "media_type": "image_document",
            "telegram_file_id": document.file_id,
            "file_name": document.file_name or "",
            "mime_type": document.mime_type or "",
            "page_media_type": "photo",
            "usage": "ready_bot_api_file_id",
        }

    if document:
        return {
            "media_type": "document",
            "file_name": document.file_name or "",
            "mime_type": document.mime_type or "",
            "usage": "not_page_image",
        }

    return None


def _extract_chat_attachment_context(message: Message) -> str:
    """Возвращает контекст Telegram-вложения для обычного чата Yadreno Admin."""
    if message.photo:
        photo = message.photo[-1]
        return (
            "\n\nК сообщению прикреплено изображение Telegram:\n"
            "- media_type: photo\n"
            f"- telegram_file_id: {photo.file_id}\n"
            "- Если пользователь просит поставить или заменить медиа страницы, "
            "можно использовать этот telegram_file_id как готовое значение pages.image_custom и записать pages.media_type_custom='photo'. "
            "Если пользователь просит анализ, анализируй загруженный файл.\n"
        )

    if message.video:
        video = message.video
        return (
            "\n\nК сообщению прикреплено видео Telegram:\n"
            "- media_type: video\n"
            f"- telegram_file_id: {video.file_id}\n"
            f"- file_name: {getattr(video, 'file_name', None) or ''}\n"
            f"- mime_type: {getattr(video, 'mime_type', None) or ''}\n"
            "- analysis_supported: false\n"
            "- Если пользователь просит поставить или заменить медиа страницы, "
            "можно использовать этот telegram_file_id как готовое значение pages.image_custom и записать pages.media_type_custom='video'. "
            "Видео не скачивается и не загружается на анализ; если нужен анализ, попроси скриншот или текстовое описание.\n"
        )

    if message.animation:
        animation = message.animation
        return (
            "\n\nК сообщению прикреплена GIF/animation Telegram:\n"
            "- media_type: animation\n"
            f"- telegram_file_id: {animation.file_id}\n"
            f"- file_name: {getattr(animation, 'file_name', None) or ''}\n"
            f"- mime_type: {getattr(animation, 'mime_type', None) or ''}\n"
            "- analysis_supported: false\n"
            "- Если пользователь просит поставить или заменить медиа страницы, "
            "можно использовать этот telegram_file_id как готовое значение pages.image_custom и записать pages.media_type_custom='animation'. "
            "GIF/animation не скачивается и не загружается на анализ; если нужен анализ, попроси скриншот или текстовое описание.\n"
        )

    document = message.document
    if document and _is_gif_document(document):
        return (
            "\n\nК сообщению прикреплена GIF/animation Telegram как document:\n"
            "- media_type: animation\n"
            f"- telegram_file_id: {document.file_id}\n"
            f"- file_name: {document.file_name or ''}\n"
            f"- mime_type: {document.mime_type or ''}\n"
            "- analysis_supported: false\n"
            "- Если пользователь просит поставить или заменить медиа страницы, "
            "можно использовать этот telegram_file_id как готовое значение pages.image_custom и записать pages.media_type_custom='animation'. "
            "GIF/animation не скачивается и не загружается на анализ; если нужен анализ, попроси скриншот или текстовое описание.\n"
        )

    if document and (document.mime_type or "").startswith("image/"):
        return (
            "\n\nК сообщению прикреплён image-документ Telegram:\n"
            "- media_type: image_document\n"
            f"- telegram_file_id: {document.file_id}\n"
            f"- file_name: {document.file_name or ''}\n"
            f"- mime_type: {document.mime_type or ''}\n"
            "- Если пользователь просит поставить или заменить медиа страницы, "
            "можно использовать этот telegram_file_id как готовое значение pages.image_custom и записать pages.media_type_custom='photo'. "
            "Если пользователь просит анализ, анализируй загруженный файл.\n"
        )

    return ""


def _yadreno_album_key(message: Message, topic_id: int) -> tuple[int, int, str]:
    """Build a stable key for one Telegram media group in one chat lane."""
    return (
        int(message.from_user.id),
        topic_id,
        str(message.media_group_id),
    )


def _yadreno_album_lock(key: tuple[int, int, str]) -> asyncio.Lock:
    """Return the per-album lock without creating locks outside the event loop."""
    lock = _yadreno_album_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _yadreno_album_locks[key] = lock
    return lock


def _message_prompt_text(message: Message) -> str:
    """Return plain prompt/caption from a Telegram message."""
    return get_message_text_for_storage(message, "plain").strip()


def _build_yadreno_album_prompt(messages: list[Message]) -> str:
    """Build one agent prompt from a Telegram media group."""
    prompt = next((text for msg in messages if (text := _message_prompt_text(msg))), "")
    if not prompt:
        prompt = "Проанализируй приложенные изображения и файлы."
        if all(_is_metadata_only_media(msg) for msg in messages):
            prompt = (
                "Пользователь прислал медиа без скачивания. "
                "Используй только Telegram metadata ниже; содержимое видео/GIF не анализируется."
            )

    contexts: list[str] = []
    for index, msg in enumerate(messages, 1):
        context = _extract_chat_attachment_context(msg)
        if context:
            contexts.append(f"\n\n--- Attachment {index} ---{context}")
    return f"{prompt}{''.join(contexts)}"


async def _handle_yadreno_chat_album_item(
    message: Message,
    topic_id: int,
    api_key: str,
) -> None:
    """Buffer one media-group item and schedule a single Yadreno Admin turn."""
    key = _yadreno_album_key(message, topic_id)
    lock = _yadreno_album_lock(key)
    async with lock:
        buffer = _yadreno_album_buffers.get(key)
        if buffer is None:
            buffer = _YadrenoAlbumBuffer(
                user_id=message.from_user.id,
                topic_id=topic_id,
                api_key=api_key,
                media_group_id=str(message.media_group_id),
                first_message=message,
            )
            _yadreno_album_buffers[key] = buffer
        buffer.api_key = api_key
        buffer.messages.append(message)
        if buffer.flush_task and not buffer.flush_task.done():
            buffer.flush_task.cancel()
        buffer.flush_task = asyncio.create_task(_flush_yadreno_album_after_delay(key))


async def _flush_yadreno_album_after_delay(key: tuple[int, int, str]) -> None:
    """Flush one buffered Telegram album after the debounce window."""
    try:
        await asyncio.sleep(YADRENO_ADMIN_ALBUM_DEBOUNCE_SEC)
    except asyncio.CancelledError:
        return

    lock = _yadreno_album_locks.get(key)
    if lock is None:
        return
    async with lock:
        buffer = _yadreno_album_buffers.pop(key, None)

    if buffer is None:
        return

    try:
        await _process_yadreno_album_buffer(buffer)
    finally:
        if key not in _yadreno_album_buffers:
            _yadreno_album_locks.pop(key, None)


async def _process_yadreno_album_buffer(buffer: _YadrenoAlbumBuffer) -> None:
    """Download uploadable album files and run one Yadreno Admin request."""
    prompt = _build_yadreno_album_prompt(buffer.messages)
    thinking = await safe_edit_or_send(
        buffer.first_message,
        "🤖 <b>Yadreno Admin</b>\n\n⏳ Загружаю файлы и запускаю агента...",
        reply_markup=yadreno_admin_agent_kb(buffer.topic_id),
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(
        thinking,
        topic_id=buffer.topic_id,
    )

    uploads: list[YadrenoAdminUpload] = []
    overflow_count = 0
    download_errors: list[str] = []
    metadata_only = any(_is_metadata_only_media(msg) for msg in buffer.messages)

    try:
        for msg in buffer.messages:
            if _message_upload_meta(msg) is None:
                continue
            if len(uploads) >= YADRENO_ADMIN_UPLOAD_MAX_FILES:
                overflow_count += 1
                continue
            try:
                uploads.extend(await _download_yadreno_upload(msg))
            except YadrenoAdminError as e:
                download_errors.append(str(e))

        if download_errors:
            prompt = (
                f"{prompt}\n\nНекоторые файлы альбома не удалось скачать:\n"
                + "\n".join(f"- {error}" for error in download_errors[:3])
            )
        if overflow_count:
            prompt = (
                f"{prompt}\n\nНе скачано файлов сверх локального лимита: "
                f"{overflow_count}."
            )

        if uploads:
            final = await run_dialog_with_uploads(
                buffer.user_id,
                buffer.api_key,
                prompt,
                uploads,
                topic_id=buffer.topic_id,
                progress_callback=progress.handle,
                overflow_count=overflow_count,
            )
        elif metadata_only:
            final = await run_dialog(
                buffer.user_id,
                buffer.api_key,
                prompt,
                topic_id=buffer.topic_id,
                progress_callback=progress.handle,
            )
        elif download_errors:
            raise YadrenoAdminError(download_errors[0])
        else:
            raise YadrenoAdminError("В альбоме нет поддерживаемых файлов")

        await safe_edit_or_send(
            progress.final_target,
            _format_final_response(final.content, final.viewer_url),
            reply_markup=yadreno_admin_agent_kb(buffer.topic_id),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(buffer.topic_id),
        )
    finally:
        _cleanup_yadreno_uploads(uploads)


def _extract_yaa_task_html(message: Message, command: CommandObject) -> str:
    """Извлекает аргумент команды, сохраняя Telegram HTML и custom emoji."""
    formatted_message = get_message_text_for_storage(message, "html")
    command_text = f"{command.prefix}{command.command}"
    if command.mention:
        command_text += f"@{command.mention}"

    if formatted_message.startswith(command_text):
        return formatted_message[len(command_text):].strip()
    return (command.args or "").strip()


def _redact_yaa_context(page_key: str, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    """Возвращает runtime context без пользовательских ключей."""
    result = dict(runtime_context or {})
    if page_key == YAA_KEY_DELIVERY_PAGE:
        for key in YAA_KEY_DELIVERY_CONTEXT_KEYS:
            if key in result:
                result[key] = YAA_REDACTED_USER_KEY
    return result


def _redact_yaa_text_replacements(
    page_key: str,
    text_replacements: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Возвращает подстановки без пользовательских ключей для read-only снимков."""
    if not text_replacements:
        return None
    result = dict(text_replacements)
    if page_key == YAA_KEY_DELIVERY_PAGE:
        for placeholder in list(result.keys()):
            if str(placeholder).casefold() in YAA_KEY_DELIVERY_PLACEHOLDERS:
                result[placeholder] = YAA_REDACTED_USER_KEY
    return result


def _redact_yaa_visible_keyboard_urls(
    page_key: str,
    rows: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    """Делает redacted-значение в URL-снимках читаемым для агента."""
    if page_key != YAA_KEY_DELIVERY_PAGE or not rows:
        return rows

    encoded_redacted = quote(YAA_REDACTED_USER_KEY, safe='')
    normalized_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        normalized_row: list[dict[str, Any]] = []
        for button in row:
            item = dict(button)
            url = item.get("url")
            if isinstance(url, str):
                item["url"] = url.replace(encoded_redacted, YAA_REDACTED_USER_KEY)
            normalized_row.append(item)
        normalized_rows.append(normalized_row)
    return normalized_rows


def _build_yaa_runtime_context(page_key: str, page_context: Any | None) -> dict[str, Any]:
    """Собирает runtime-часть контекста /yaa в JSON-friendly формате."""
    if page_context is None:
        return {}

    runtime: dict[str, Any] = {}
    visibility = dict(page_context.visibility or {})
    context = _redact_yaa_context(page_key, page_context.context)
    prepend_buttons = serialize_inline_button_rows(page_context.prepend_buttons)
    append_buttons = serialize_inline_button_rows(page_context.append_buttons)

    if visibility:
        runtime["visibility"] = visibility
    if context:
        runtime["context"] = context
    if prepend_buttons:
        runtime["prepend_buttons"] = prepend_buttons
    if append_buttons:
        runtime["append_buttons"] = append_buttons

    return runtime


def _build_yaa_prompt(
    page_key: str,
    task_html: str,
    backup_path: str,
    attachment: dict[str, str] | None = None,
    page_context: Any | None = None,
) -> str:
    """Формирует компактный JSON-контекст команды /yaa."""
    stored_page = get_page_stored_data(page_key) or {
        "text": {"source": "default", "value": "", "custom": None},
        "image": {"source": "default", "value": "", "custom": None},
        "buttons": [],
    }
    visibility = page_context.visibility if page_context else None
    runtime_context = _redact_yaa_context(
        page_key,
        page_context.context if page_context else None,
    )
    prepend_buttons = page_context.prepend_buttons if page_context else None
    append_buttons = page_context.append_buttons if page_context else None
    visible_keyboard = build_visible_keyboard_snapshot(
        buttons=stored_page.get("buttons") or [],
        visibility=visibility,
        context=runtime_context,
        text_replacements=_redact_yaa_text_replacements(
            page_key,
            page_context.text_replacements if page_context else None,
        ),
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )
    visible_keyboard = _redact_yaa_visible_keyboard_urls(page_key, visible_keyboard)

    context: dict[str, Any] = {
        "source": "/yaa",
        "page_key": page_key,
        "database_path": "database/vpn_bot.db",
        "backup": {
            "created": True,
            "path": backup_path,
        },
        "stored_page": stored_page,
        "visible_keyboard": visible_keyboard,
        "runtime": _build_yaa_runtime_context(page_key, page_context),
        "task_format": "telegram_html",
        "task_html": task_html,
    }
    if attachment:
        context["attachment"] = attachment

    return (
        "Команда /yaa вызвана администратором прямо на пользовательской странице VPN-бота. "
        "Служебный контекст:\n"
        f"Задача администратора: {task_html}\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'), default=str)}"
    )


@router.message(Command("yaa"))
async def handle_yaa_command(message: Message, command: CommandObject, state: FSMContext):
    """Контекстная команда администратора с пользовательской страницы."""
    if not is_admin(message.from_user.id):
        return

    task_html = _extract_yaa_task_html(message, command)
    if not (command.args or "").strip():
        await safe_edit_or_send(
            message,
            "🤖 <b>Yadreno Admin</b>\n\n"
            "Добавьте задачу после команды, например:\n"
            "<code>/yaa сделай кнопку поддержки зелёной</code>",
            force_new=True,
        )
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
            force_new=True,
        )
        return

    page_context = get_page_context(message.from_user.id)
    if not page_context:
        await safe_edit_or_send(
            message,
            "🤖 <b>Yadreno Admin</b>\n\n"
            "Сейчас я не знаю, какую пользовательскую страницу вы имеете в виду. "
            "Откройте поддерживаемую страницу и повторите команду.",
            force_new=True,
        )
        return

    try:
        backup_path = await asyncio.to_thread(create_bot_database_backup)
    except Exception as e:
        await safe_edit_or_send(
            message,
            "❌ <b>Не удалось создать резервную копию</b>\n\n"
            f"Запрос агенту не отправлен: {escape_html(str(e))}",
            force_new=True,
        )
        return

    before = _serialize_for_compare(_get_yaa_editable_state(page_context.page_key))
    attachment = _extract_yaa_attachment_data(message)
    prompt = _build_yaa_prompt(
        page_context.page_key,
        task_html,
        backup_path,
        attachment,
        page_context=page_context,
    )
    status_message = await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n"
        "⏳ Ведётся агентская работа...",
        reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_YAA_TOPIC_ID),
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(
        status_message,
        topic_id=YADRENO_ADMIN_YAA_TOPIC_ID,
    )
    try:
        await message.delete()
    except Exception:
        pass

    uploads: list[YadrenoAdminUpload] = []
    try:
        uploads = await _download_yadreno_upload(message)
        if uploads:
            final = await run_dialog_with_uploads(
                message.from_user.id,
                api_key,
                prompt,
                uploads,
                topic_id=YADRENO_ADMIN_YAA_TOPIC_ID,
                progress_callback=progress.handle,
            )
        else:
            final = await run_dialog(
                message.from_user.id,
                api_key,
                prompt,
                topic_id=YADRENO_ADMIN_YAA_TOPIC_ID,
                progress_callback=progress.handle,
            )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_YAA_TOPIC_ID),
        )
        return
    finally:
        _cleanup_yadreno_uploads(uploads)

    after = _serialize_for_compare(_get_yaa_editable_state(page_context.page_key))
    if before != after:
        await progress.delete_progress_messages()
        if page_context.page_key == 'key_delivery':
            from bot.utils.key_sender import rerender_key_delivery_page_context

            if await rerender_key_delivery_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'qr_payment':
            from bot.handlers.user.payments.base import rerender_qr_payment_page_context

            if await rerender_qr_payment_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'crypto_payment':
            from bot.handlers.user.payments.crypto import rerender_crypto_payment_page_context

            if await rerender_crypto_payment_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'balance_payment':
            from bot.handlers.user.payments.balance import rerender_balance_payment_page_context

            if await rerender_balance_payment_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'demo_payment':
            from bot.handlers.user.payments.demo import rerender_demo_payment_page_context

            if await rerender_demo_payment_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'payment_tariff_select':
            from bot.handlers.user.payments.tariff_select_page import rerender_payment_tariff_select_page_context

            if await rerender_payment_tariff_select_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'payment_status':
            from bot.handlers.user.payments.status_page import rerender_payment_status_page_context

            if await rerender_payment_status_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key in {'my_keys', 'my_keys_empty'}:
            from bot.handlers.user.keys import rerender_my_keys_page_context

            if await rerender_my_keys_page_context(page_context, message.from_user.id):
                return
        if page_context.page_key == 'key_details':
            from bot.handlers.user.keys import rerender_key_details_page_context

            if await rerender_key_details_page_context(page_context, message.from_user.id):
                return
        await render_page(
            page_context.message,
            page_key=page_context.page_key,
            visibility=page_context.visibility,
            context=page_context.context,
            text_replacements=page_context.text_replacements,
            prepend_buttons=page_context.prepend_buttons,
            append_buttons=page_context.append_buttons,
        )
        return

    await _activate_yadreno_chat_lane(state, YADRENO_ADMIN_YAA_TOPIC_ID)
    await safe_edit_or_send(
        progress.final_target,
        _format_final_response(final.content, final.viewer_url)
        + "\n\nМожно ответить обычным сообщением, без <code>/yaa</code>.",
        reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_YAA_TOPIC_ID),
    )


@router.message(AdminStates.yadreno_chat, F.photo | F.document | F.video | F.animation)
async def handle_yadreno_chat_attachment(message: Message, state: FSMContext):
    """Отправляет фото, видео, GIF или документ в Yadreno Admin."""
    if not is_admin(message.from_user.id):
        return

    topic_id = await _current_yadreno_chat_topic_id(state)
    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await safe_edit_or_send(
            message,
            _missing_key_text(),
            reply_markup=yadreno_admin_no_key_kb(),
            force_new=True,
        )
        return

    if message.media_group_id:
        await _handle_yadreno_chat_album_item(message, topic_id, api_key)
        return

    raw_prompt = get_message_text_for_storage(message, 'plain').strip()
    metadata_only = _is_metadata_only_media(message)
    attachment_context = _extract_chat_attachment_context(message)
    if metadata_only and not raw_prompt:
        await safe_edit_or_send(
            message,
            "🤖 <b>Yadreno Admin</b>\n\n"
            "Видео и GIF не отправляются на анализ. Добавьте подпись с задачей "
            "или используйте <code>/yaa ...</code> на открытой странице, если нужно поставить это медиа.",
            reply_markup=yadreno_admin_chat_kb(topic_id),
            force_new=True,
        )
        return

    prompt = raw_prompt
    if not prompt:
        prompt = (
            "Проанализируй приложенное изображение."
            if message.photo
            else "Проанализируй приложенный файл."
        )
    prompt = f"{prompt}{attachment_context}"
    status_text = (
        "⏳ Передаю медиа агенту без скачивания..."
        if metadata_only
        else "⏳ Загружаю файл и запускаю агента..."
    )

    thinking = await safe_edit_or_send(
        message,
        f"🤖 <b>Yadreno Admin</b>\n\n{status_text}",
        reply_markup=yadreno_admin_agent_kb(topic_id),
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(
        thinking,
        topic_id=topic_id,
    )

    uploads: list[YadrenoAdminUpload] = []
    try:
        uploads = await _download_yadreno_upload(message)
        if uploads:
            final = await run_dialog_with_uploads(
                message.from_user.id,
                api_key,
                prompt,
                uploads,
                topic_id=topic_id,
                progress_callback=progress.handle,
            )
        elif metadata_only:
            final = await run_dialog(
                message.from_user.id,
                api_key,
                prompt,
                topic_id=topic_id,
                progress_callback=progress.handle,
            )
        else:
            raise YadrenoAdminError("В сообщении нет поддерживаемого файла")
        await safe_edit_or_send(
            progress.final_target,
            _format_final_response(final.content, final.viewer_url),
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(topic_id),
        )
    finally:
        _cleanup_yadreno_uploads(uploads)
