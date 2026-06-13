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
    yadreno_admin_agent_kb,
    yadreno_admin_cancel_key_kb,
    yadreno_admin_chat_kb,
    yadreno_admin_no_key_kb,
)
from bot.services.page_context import get_page_context
from bot.services.yadreno_admin import (
    YadrenoAdminError,
    YadrenoAdminLatest,
    YadrenoAdminProgressEvent,
    cancel_active_dialog,
    detect_public_server_ip,
    fetch_latest_dialog_event,
    get_active_request_id,
    run_dialog,
    start_new_chat,
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
    get_display_timezone,
    get_setting,
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


class _YadrenoProgressRenderer:
    """Редактирует промежуточные события Yadreno Admin в текущем чате."""

    def __init__(self, anchor: Message):
        self._anchor = anchor
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
                reply_markup=yadreno_admin_agent_kb(),
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
            reply_markup=yadreno_admin_agent_kb(),
            force_new=force_new,
        )
        self._status_messages[slot] = updated

    async def _show_task_update(self, event: YadrenoAdminProgressEvent) -> None:
        text = _progress_text("📋 <b>План работы</b>", event.content)
        if self._task_message is not None:
            self._task_message = await safe_edit_or_send(
                self._task_message,
                text,
                reply_markup=yadreno_admin_agent_kb(),
            )
            return

        target = self._live_status_message or self._anchor
        self._task_message = await safe_edit_or_send(
            target,
            text,
            reply_markup=yadreno_admin_agent_kb(),
        )
        self._anchor = self._task_message
        self._live_status_message = await safe_edit_or_send(
            self._task_message,
            self._last_live_status_text,
            reply_markup=yadreno_admin_agent_kb(),
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


@router.callback_query(F.data == "admin_yadreno_new_chat")
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

    if get_active_request_id(callback.from_user.id) is not None:
        await callback.answer(
            "Агент ещё работает. Дождитесь ответа или нажмите «Отмена».",
            show_alert=True,
        )
        return

    try:
        result = await start_new_chat(callback.from_user.id, api_key)
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if result.status == "busy":
        await callback.answer(
            result.response_text or "Агент ещё работает.",
            show_alert=True,
        )
        return

    await state.set_state(AdminStates.yadreno_chat)
    await safe_edit_or_send(
        callback.message,
        "🆕 <b>Новый чат открыт</b>\n\n"
        "Контекст сброшен. Напишите новую задачу обычным сообщением.",
        reply_markup=yadreno_admin_chat_kb(),
    )
    await callback.answer("Новый чат открыт")


@router.callback_query(F.data == "admin_yadreno_cancel")
async def cancel_yadreno_dialog_button(callback: CallbackQuery):
    """Отменяет активный запрос агента с кнопки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer("Сначала укажите api_key", show_alert=True)
        return

    if get_active_request_id(callback.from_user.id) is None:
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    try:
        cancelled = await cancel_active_dialog(callback.from_user.id, api_key)
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if not cancelled:
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    await safe_edit_or_send(
        callback.message,
        "🛑 <b>Запрос отменяется</b>\n\n"
        "Агент завершит работу на следующей итерации.",
        reply_markup=yadreno_admin_agent_kb(),
    )
    await callback.answer("Отмена отправлена")


@router.callback_query(F.data == "admin_yadreno_nudge")
async def nudge_yadreno_dialog(callback: CallbackQuery):
    """Показывает последний snapshot через /latest без consume polling."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer("Сначала укажите api_key", show_alert=True)
        return

    try:
        latest = await fetch_latest_dialog_event(callback.from_user.id, api_key)
    except YadrenoAdminError as e:
        await callback.answer(str(e)[:180], show_alert=True)
        return

    if latest is None:
        await callback.answer("Активного запроса нет", show_alert=False)
        return

    text = _format_latest_event(latest)
    if text is None:
        await callback.answer("Пока свежих данных нет", show_alert=False)
        return

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=yadreno_admin_agent_kb(),
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
    await safe_edit_or_send(
        message,
        text,
        reply_markup=yadreno_admin_agent_kb(),
        force_new=True,
    )


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
        reply_markup=yadreno_admin_agent_kb(),
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
        await safe_edit_or_send(
            progress.final_target,
            _format_final_response(final.content, final.viewer_url),
            reply_markup=yadreno_admin_agent_kb(),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(),
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
    display_timezone = get_display_timezone()
    timezone_hint = (
        "Скрытая настройка часового пояса отображения дат хранится в settings.display_timezone. "
        "Если администратор просит изменить часовой пояс, обнови строку settings с key='display_timezone'. "
        "Поддерживаются IANA-зоны и алиасы: Москва, МСК, UTC+3. "
        f"Текущее значение: {display_timezone}. "
        "Не меняй даты в таблицах users, vpn_keys и payments: они хранятся в UTC.\n\n"
    )
    placeholder_hint = ""
    if page_key == 'key_delivery':
        placeholder_hint = (
            "Плейсхолдеры страницы key_delivery:\n"
            "- %ключ% — ссылка или ключ в моноширинном виде для копирования.\n"
            "- %ссылка% — чистая ссылка без code/pre; HTTP/HTTPS subscription-ссылка будет кликабельной в Telegram.\n\n"
            "Медиа у key_delivery — динамический QR-код, pages.image_custom для этой страницы не используется.\n\n"
        )
    elif page_key == 'renew_payment':
        placeholder_hint = (
            "Плейсхолдеры страницы renew_payment:\n"
            "- %имяключа% — название продлеваемого ключа, уже экранированное для HTML.\n\n"
        )
    elif page_key in {'my_keys', 'my_keys_empty'}:
        from bot.utils.my_keys_page import (
            DEFAULT_MY_KEYS_ITEM_TEMPLATE,
            MY_KEYS_ITEM_TEMPLATE_SETTING,
        )

        item_template = get_setting(
            MY_KEYS_ITEM_TEMPLATE_SETTING,
            DEFAULT_MY_KEYS_ITEM_TEMPLATE,
        )
        placeholder_hint = (
            "Плейсхолдеры страницы my_keys:\n"
            "- %списокключей% — готовый HTML-список ключей пользователя.\n\n"
            "Формат одной записи списка хранится в скрытой настройке settings.my_keys_item_template. "
            "Обычной админки для неё нет: если администратор просит изменить формат отображения ключей, "
            "обнови именно строку settings с key='my_keys_item_template'.\n"
            "Плейсхолдеры скрытого шаблона: %статус%, %имяключа%, %трафик%, %датаокончания%, "
            "%сервер%, %инбаунд%, %протокол%, %id%.\n"
            f"Текущее значение settings.my_keys_item_template: {item_template or ''}\n\n"
        )
    elif page_key == 'key_details':
        placeholder_hint = (
            "Плейсхолдеры страницы key_details:\n"
            "- %информацияключа% — готовый HTML-блок с названием, статусом, сервером, протоколом, трафиком и сроком ключа.\n"
            "- %историяопераций% — готовый HTML-блок истории оплат/операций; может быть пустым.\n\n"
            "Кнопки управления конкретным ключом (показать, продлить, заменить, переименовать) "
            "генерируются кодом как runtime-кнопки и не лежат в pages.buttons_*.\n\n"
        )
    elif page_key in {
        'key_replace_server_select',
        'key_replace_inbound_select',
        'new_key_server_select',
        'new_key_inbound_select',
    }:
        placeholder_hint = (
            f"Плейсхолдеры страницы {page_key}:\n"
            "- %данныеэкрана% — готовый HTML-блок с внутренними данными текущего шага.\n\n"
            "Кнопки списков серверов/протоколов генерируются кодом как runtime-кнопки "
            "и не лежат в pages.buttons_*.\n\n"
        )
    elif page_key == 'key_replace_confirm':
        placeholder_hint = (
            "Плейсхолдеры страницы key_replace_confirm:\n"
            "- %данныезамены% — готовый HTML-блок с ключом, новым сервером и предупреждением.\n\n"
            "Кнопки подтверждения/отмены генерируются кодом как runtime-кнопки "
            "и не лежат в pages.buttons_*.\n\n"
        )
    elif page_key == 'key_rename_prompt':
        placeholder_hint = (
            "Плейсхолдеры страницы key_rename_prompt:\n"
            "- %данныеключа% — готовый HTML-блок с текущим именем ключа.\n\n"
            "Кнопка отмены генерируется кодом как runtime-кнопка и не лежит в pages.buttons_*.\n\n"
        )
    elif page_key in {'key_show_unconfigured', 'renew_payment_unavailable', 'new_key_no_servers'}:
        placeholder_hint = (
            f"Страница {page_key} не требует динамических плейсхолдеров. "
            "Можно менять текст, картинку и статические кнопки из pages.buttons_*.\n\n"
        )
    attachment_block = (
        f"\n{attachment_context}\n"
        if attachment_context
        else ""
    )
    return (
        "Команда /yaa вызвана администратором прямо на пользовательской странице VPN-бота.\n"
        f"Текущая страница: {page_key}\n"
        "Считай, что пользователь говорит именно про эту страницу, даже если не назвал её явно.\n\n"
        f"{timezone_hint}"
        f"{placeholder_hint}"
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

    before = _serialize_for_compare(_get_yaa_editable_state(page_context.page_key))
    attachment_context = _extract_yaa_attachment_context(message)
    prompt = _build_yaa_prompt(page_context.page_key, task, attachment_context)
    status_message = await safe_edit_or_send(
        message,
        "🤖 <b>Yadreno Admin</b>\n\n"
        "⏳ Ведётся агентская работа...",
        reply_markup=yadreno_admin_agent_kb(),
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
            reply_markup=yadreno_admin_agent_kb(),
        )
        return

    after = _serialize_for_compare(_get_yaa_editable_state(page_context.page_key))
    if before != after:
        await progress.delete_progress_messages()
        if page_context.page_key == 'key_delivery':
            from bot.utils.key_sender import rerender_key_delivery_page_context

            if await rerender_key_delivery_page_context(page_context, message.from_user.id):
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

    await safe_edit_or_send(
        progress.final_target,
        _format_final_response(final.content, final.viewer_url),
        reply_markup=yadreno_admin_agent_kb(),
    )


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
        reply_markup=yadreno_admin_agent_kb(),
        force_new=True,
    )
