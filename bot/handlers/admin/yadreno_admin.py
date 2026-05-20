"""
Диалог с агентом Yadreno Admin и контекстная команда /yaa.
"""
from __future__ import annotations

import json
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    yadreno_admin_cancel_key_kb,
    yadreno_admin_chat_kb,
    yadreno_admin_no_key_kb,
)
from bot.services.page_context import get_page_context
from bot.services.yadreno_admin import (
    YadrenoAdminError,
    YadrenoAdminProgressEvent,
    cancel_active_dialog,
    detect_public_server_ip,
    run_dialog,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.page_renderer import get_page_data, render_page
from bot.utils.text import (
    escape_html,
    get_message_text_for_storage,
    safe_edit_or_send,
)
from database.requests import (
    get_page,
    get_yadreno_admin_api_key,
    set_yadreno_admin_server_ip,
    set_yadreno_admin_api_key,
)

router = Router()


def _missing_key_text() -> str:
    """Текст экрана настройки api_key."""
    return (
        "🤖 <b>Yadreno Admin</b>\n\n"
        "Чтобы начать диалог с агентом, сначала укажите свой <code>api_key</code>.\n\n"
        "Получить ключ можно в <a href=\"https://t.me/YadrenoAdmin_Bot\">@YadrenoAdmin_Bot</a> "
        "в разделе «Профиль»."
    )


def _chat_intro_text() -> str:
    """Текст экрана чата с агентом."""
    return (
        "🤖 <b>Yadreno Admin</b>\n\n"
        "Напишите задачу обычным сообщением — агент сможет смотреть и менять этот сервер.\n\n"
        "Чтобы остановить текущий запрос, отправьте <code>/cancel</code>."
    )


def _progress_text(title: str, content: str) -> str:
    """Форматирует progress-событие хаба для HTML-сообщения Telegram."""
    body = escape_html(content.strip()) if content else "Обновляю статус..."
    return f"{title}\n\n{body}"


class _YadrenoProgressRenderer:
    """Редактирует промежуточные события Yadreno Admin в текущем чате."""

    def __init__(self, anchor: Message):
        self._anchor = anchor
        self._status_messages: dict[str, Message] = {}
        self._task_message: Message | None = None

    @property
    def final_target(self) -> Message:
        """Сообщение, которое нужно заменить финальным ответом."""
        return self._anchor

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
        target = self._status_messages.get(slot)
        force_new = False
        if target is None:
            target = self._anchor
            force_new = bool(self._status_messages)

        updated = await safe_edit_or_send(target, text, force_new=force_new)
        self._status_messages[slot] = updated
        if not force_new:
            self._anchor = updated

    async def _show_task_update(self, event: YadrenoAdminProgressEvent) -> None:
        text = _progress_text("📋 <b>План работы</b>", event.content)
        target = self._task_message or self._anchor
        updated = await safe_edit_or_send(
            target,
            text,
            force_new=self._task_message is None,
        )
        self._task_message = updated


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

    await state.set_state(AdminStates.yadreno_chat)
    await safe_edit_or_send(
        message,
        _chat_intro_text(),
        reply_markup=yadreno_admin_chat_kb(),
    )


@router.callback_query(F.data == "admin_yadreno")
async def show_yadreno_admin(callback: CallbackQuery, state: FSMContext):
    """Открывает раздел Yadreno Admin."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    await _show_yadreno_entry(callback, state)


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

    await state.set_state(AdminStates.yadreno_chat)
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
        reply_markup=yadreno_admin_chat_kb(),
        force_new=editing_message is None,
    )


@router.message(Command("cancel"), AdminStates.yadreno_chat)
async def cancel_yadreno_dialog(message: Message):
    """Отменяет текущий запрос агента."""
    if not is_admin(message.from_user.id):
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

    try:
        cancelled = await cancel_active_dialog(message.from_user.id, api_key)
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
    await safe_edit_or_send(message, text, force_new=True)


@router.message(AdminStates.yadreno_chat, F.text, ~F.text.startswith('/'))
async def handle_yadreno_chat_message(message: Message):
    """Отправляет сообщение администратора агенту и показывает ответ."""
    if not is_admin(message.from_user.id):
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

    text = get_message_text_for_storage(message, 'plain')
    thinking = await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n⏳ Думаю...",
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(thinking)
    try:
        final = await run_dialog(
            message.from_user.id,
            api_key,
            text,
            progress_callback=progress.handle,
        )
        response = final.content or "Готово."
        if final.viewer_url:
            response += f'\n\n<a href="{escape_html(final.viewer_url)}">Полная версия ответа</a>'
        await safe_edit_or_send(
            progress.final_target,
            response,
            reply_markup=yadreno_admin_chat_kb(),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_chat_kb(),
        )


def _serialize_for_compare(data: Any) -> str:
    """Сериализует структуру страницы для сравнения до/после."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def _extract_yaa_attachment_context(message: Message) -> str:
    """Возвращает контекст прикреплённого к /yaa медиа для агента."""
    if message.photo:
        photo = message.photo[-1]
        return (
            "К исходной команде /yaa прикреплено изображение Telegram:\n"
            "- media_type: photo\n"
            f"- telegram_file_id: {photo.file_id}\n"
            "- назначение: если задача просит поставить или заменить картинку страницы, "
            "запиши этот telegram_file_id в pages.image_custom текущей страницы.\n"
            "- скачивать картинку для установки на страницу не нужно; это уже готовый "
            "Telegram file_id для отправки через Bot API.\n"
        )

    document = message.document
    if document and (document.mime_type or "").startswith("image/"):
        return (
            "К исходной команде /yaa прикреплён image-документ Telegram:\n"
            "- media_type: image_document\n"
            f"- telegram_file_id: {document.file_id}\n"
            f"- file_name: {document.file_name or ''}\n"
            f"- mime_type: {document.mime_type or ''}\n"
            "- назначение: если задача просит поставить или заменить картинку страницы, "
            "запиши этот telegram_file_id в pages.image_custom текущей страницы.\n"
            "- скачивать картинку для установки на страницу не нужно; это уже готовый "
            "Telegram file_id для отправки через Bot API.\n"
        )

    if document:
        return (
            "К исходной команде /yaa прикреплён файл, но это не изображение:\n"
            f"- file_name: {document.file_name or ''}\n"
            f"- mime_type: {document.mime_type or ''}\n"
            "Не используй этот файл как image_custom.\n"
        )

    return ""


def _build_yaa_prompt(page_key: str, task: str, attachment_context: str = "") -> str:
    """Формирует запрос агенту с точным контекстом страницы."""
    row = get_page(page_key) or {}
    page_data = get_page_data(page_key) or {}
    attachment_block = (
        f"\n{attachment_context}\n"
        if attachment_context
        else ""
    )
    return (
        "Команда /yaa вызвана администратором прямо на пользовательской странице VPN-бота.\n"
        f"Текущая страница: {page_key}\n"
        "Считай, что пользователь говорит именно про эту страницу, даже если не назвал её явно.\n\n"
        "Текущее состояние страницы в БД:\n"
        f"- text_default: {row.get('text_default') or ''}\n"
        f"- text_custom: {row.get('text_custom') or ''}\n"
        f"- image_default: {row.get('image_default') or ''}\n"
        f"- image_custom: {row.get('image_custom') or ''}\n"
        f"- buttons_default: {row.get('buttons_default') or '[]'}\n"
        f"- buttons_custom: {row.get('buttons_custom') or '[]'}\n\n"
        "Фактически отображаемые данные после мёржа:\n"
        f"- text: {page_data.get('text') or ''}\n"
        f"- image: {page_data.get('image') or ''}\n"
        f"- buttons: {json.dumps(page_data.get('buttons') or [], ensure_ascii=False)}\n\n"
        f"{attachment_block}"
        f"Задача администратора: {task}"
    )


@router.message(Command("yaa"))
async def handle_yaa_command(message: Message, command: CommandObject):
    """Контекстная команда администратора с пользовательской страницы."""
    if not is_admin(message.from_user.id):
        return

    task = (command.args or "").strip()
    if not task:
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

    before = _serialize_for_compare(get_page_data(page_context.page_key))
    attachment_context = _extract_yaa_attachment_context(message)
    prompt = _build_yaa_prompt(page_context.page_key, task, attachment_context)
    status_message = await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n"
        "⏳ Ведётся агентская работа...",
        force_new=True,
    )
    progress = _YadrenoProgressRenderer(status_message)
    try:
        await message.delete()
    except Exception:
        pass

    try:
        final = await run_dialog(
            message.from_user.id,
            api_key,
            prompt,
            progress_callback=progress.handle,
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
        )
        return

    after = _serialize_for_compare(get_page_data(page_context.page_key))
    if before != after:
        try:
            await progress.final_target.delete()
        except Exception:
            pass
        await render_page(
            page_context.message,
            page_key=page_context.page_key,
            visibility=page_context.visibility,
            context=page_context.context,
            text_replacements=page_context.text_replacements,
            append_buttons=page_context.append_buttons,
        )
        return

    response = final.content or "Готово."
    if final.viewer_url:
        response += f'\n\n<a href="{escape_html(final.viewer_url)}">Полная версия ответа</a>'
    await safe_edit_or_send(progress.final_target, response)


@router.message(AdminStates.yadreno_chat, F.photo | F.document)
async def handle_yadreno_chat_attachment(message: Message):
    """Поясняет ограничение satellite-чата для файлового анализа."""
    if not is_admin(message.from_user.id):
        return

    await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n"
        "Файлы и фото в обычном satellite-чате пока не отправляются на хаб для анализа.\n\n"
        "Если нужно поставить картинку на текущую страницу, отправьте фото с подписью "
        "<code>/yaa поставь картинку</code>.\n\n"
        "Если нужен именно анализ изображения или файла, используйте основной "
        "<a href=\"https://t.me/YadrenoAdmin_Bot\">@YadrenoAdmin_Bot</a>: там файл "
        "скачивается ботом и загружается в файловое хранилище хаба.",
        reply_markup=yadreno_admin_chat_kb(),
        force_new=True,
    )
