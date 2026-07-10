"""
Утилита для отправки VPN-ключей пользователю.
"""
import logging
from typing import Mapping, Optional

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from bot.services.vpn_api import get_client
from bot.utils.key_generator import generate_link, generate_json, generate_qr_code
from bot.utils.placeholders import apply_page_placeholders
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)

KEY_COPY_PLACEHOLDER = '%ключ_для_копирования%'
KEY_LINK_PLACEHOLDER = '%ключ_ссылка%'
KEY_LINK_URL_PLACEHOLDER = '%ключ_ссылка_url%'
KEY_DELIVERY_PAGE = 'key_delivery'
KEY_DELIVERY_CONTEXT_RAW = 'key_delivery_raw_value'
KEY_DELIVERY_CONTEXT_KIND = 'key_delivery_kind'
KEY_DELIVERY_CONTEXT_IS_NEW = 'key_delivery_is_new'
KEY_DELIVERY_CONTEXT_ATTACH_MARKUP = 'key_delivery_attach_markup'


# Дефолтный текст выдачи ключа в формате HTML
DEFAULT_KEY_DELIVERY_TEXT = (
    "✅ <b>Ваш VPN-ключ!</b>\n\n"
    f"{KEY_COPY_PLACEHOLDER}\n"
    "☝️ Нажмите, чтобы скопировать.\n\n"
    "📱 <b>Инструкция:</b>\n"
    "1. Скопируйте ссылку или отсканируйте QR-код.\n"
    "2. Импортируйте в свой клиент. Какой именно клиент подходит, смотри в инструкции по кнопке ниже.\n"
    "3. Нажмите подключиться!"
)


def format_key_copy_value(raw_value: str) -> str:
    """Форматирует ключ/подписку как копируемый моноширинный фрагмент."""
    return f"<code>{escape_html(raw_value)}</code>"


def format_key_plain_link(raw_value: str) -> str:
    """
    Возвращает чистую ссылку без code/pre.

    HTTP/HTTPS subscription-ссылки Telegram показывает кликабельными. Для
    custom-схем вроде vless:// ссылка остаётся обычным текстом, если клиент
    Telegram не поддерживает такой переход.
    """
    return escape_html(raw_value)


def build_key_delivery_text(
    template: str,
    raw_value: str,
    context: Optional[Mapping[str, object]] = None,
) -> str:
    """Подставляет плейсхолдеры выдачи ключа в редактируемый текст."""
    render_context = dict(context or {})
    render_context.update({
        KEY_DELIVERY_CONTEXT_RAW: raw_value,
        'page_key': KEY_DELIVERY_PAGE,
    })
    replacements = build_key_delivery_replacements(raw_value)
    try:
        from bot.utils.page_placeholder_context import enrich_page_placeholder_context_sync

        render_context = enrich_page_placeholder_context_sync(
            KEY_DELIVERY_PAGE,
            {'text': template, 'buttons': []},
            render_context,
            replacements,
        )
    except Exception as e:
        logger.warning("Не удалось дополнить context страницы выдачи ключа: %s", e)

    return apply_page_placeholders(
        template,
        replacements,
        render_context,
        mode='html',
    )


def build_key_delivery_replacements(raw_value: str) -> dict:
    """Возвращает безопасные HTML-подстановки для страницы выдачи ключа."""
    return {
        KEY_COPY_PLACEHOLDER: format_key_copy_value(raw_value),
        KEY_LINK_PLACEHOLDER: format_key_plain_link(raw_value),
    }


def build_compact_delivery_text(
    title: str,
    raw_value: str,
    copy_label: str,
    qr_hint: str,
) -> str:
    """Фоллбэк для caption Telegram: сначала пробуем оставить оба варианта ссылки."""
    compact = (
        f"{title}\n\n"
        f"👇 <b>{copy_label}:</b>\n"
        f"{format_key_copy_value(raw_value)}\n\n"
        "🔗 <b>Чистая ссылка:</b>\n"
        f"{format_key_plain_link(raw_value)}\n\n"
        f"{qr_hint}"
    )
    if len(compact) <= 1024:
        return compact

    return (
        f"{title}\n\n"
        f"👇 <b>{copy_label}:</b>\n"
        f"{format_key_copy_value(raw_value)}\n\n"
        f"{qr_hint}"
    )


def _get_target_message(messageable) -> Optional[Message]:
    """Возвращает сообщение, которое нужно редактировать через safe_edit_or_send."""
    if isinstance(messageable, Message):
        return messageable
    return getattr(messageable, 'message', None)


def _get_viewer_id(messageable) -> Optional[int]:
    """Возвращает Telegram ID пользователя, который видит страницу."""
    user = getattr(messageable, 'from_user', None)
    if user and not getattr(user, 'is_bot', False):
        return user.id
    message = getattr(messageable, 'message', None)
    message_user = getattr(message, 'from_user', None)
    if message_user and not getattr(message_user, 'is_bot', False):
        return message_user.id
    chat = getattr(messageable, 'chat', None) or getattr(message, 'chat', None)
    if chat and getattr(chat, 'type', None) == 'private':
        return chat.id
    return None


def _get_bot_username(messageable) -> str:
    """Возвращает username бота для плейсхолдеров страницы выдачи ключа."""
    bot = getattr(messageable, 'bot', None)
    if bot is None:
        message = getattr(messageable, 'message', None)
        bot = getattr(message, 'bot', None)
    return (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )


def _get_key_delivery_markup(
    fallback_markup: Optional[InlineKeyboardMarkup],
    raw_value: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
) -> Optional[InlineKeyboardMarkup]:
    """Берёт клавиатуру страницы из БД, если она доступна, иначе использует fallback."""
    try:
        from bot.utils.page_renderer import build_page_keyboard

        render_context = {
            KEY_DELIVERY_CONTEXT_RAW: raw_value,
            'page_key': KEY_DELIVERY_PAGE,
        }
        if viewer_id:
            render_context['telegram_id'] = viewer_id
        if bot_username:
            render_context['bot_username'] = bot_username

        markup = build_page_keyboard(
            KEY_DELIVERY_PAGE,
            context=render_context,
            text_replacements=build_key_delivery_replacements(raw_value),
        )
        return markup or fallback_markup
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру страницы выдачи ключа: %s", e)
        return fallback_markup


def _get_json_document_markup(
    fallback_markup: Optional[InlineKeyboardMarkup],
    raw_value: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
) -> Optional[InlineKeyboardMarkup]:
    """Возвращает page-backed кнопки выдачи ключа для JSON-файла."""
    return _get_key_delivery_markup(
        fallback_markup,
        raw_value,
        viewer_id=viewer_id,
        bot_username=bot_username,
    )


def _build_key_delivery_caption(
    raw_value: str,
    is_new: bool,
    kind: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
) -> str:
    """Собирает caption выдачи ключа/подписки с учётом лимита Telegram."""
    from bot.utils.message_editor import get_message_data

    delivery_data = get_message_data(KEY_DELIVERY_PAGE, DEFAULT_KEY_DELIVERY_TEXT)
    base_caption = delivery_data.get('text', DEFAULT_KEY_DELIVERY_TEXT)
    context = {}
    if viewer_id:
        context['telegram_id'] = viewer_id
    if bot_username:
        context['bot_username'] = bot_username
    caption = build_key_delivery_text(base_caption, raw_value, context)

    if len(caption) <= 1024:
        return caption

    if kind == 'subscription':
        title = "✅ <b>Ваша подписка!</b>" if is_new else "📋 <b>Ваша подписка</b>"
        return build_compact_delivery_text(
            title=title,
            raw_value=raw_value,
            copy_label="Ваша subscription-ссылка",
            qr_hint="📸 Отсканируйте QR-код, чтобы импортировать подписку в клиент.",
        )

    title = "✅ <b>Ваш новый VPN-ключ!</b>" if is_new else "📋 <b>Ваш VPN-ключ</b>"
    return build_compact_delivery_text(
        title=title,
        raw_value=raw_value,
        copy_label="Ваша ссылка доступа",
        qr_hint="📸 Отсканируйте QR-код для быстрого подключения.",
    )


async def _render_key_delivery_photo(
    target_message: Message,
    raw_value: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    is_new: bool,
    kind: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
) -> Message:
    """Отправляет или редактирует QR-фото страницы выдачи ключа."""
    from bot.utils.text import safe_edit_or_send

    caption = _build_key_delivery_caption(
        raw_value,
        is_new,
        kind,
        viewer_id=viewer_id,
        bot_username=bot_username,
    )
    filename = "subscription_qr.png" if kind == 'subscription' else "qrcode.png"
    photo = BufferedInputFile(generate_qr_code(raw_value), filename=filename)

    return await safe_edit_or_send(
        target_message,
        caption,
        reply_markup=reply_markup,
        photo=photo,
    )


def _remember_key_delivery_context(
    viewer_id: Optional[int],
    rendered_message: Message,
    raw_value: str,
    is_new: bool,
    kind: str,
    attach_markup: bool,
    bot_username: str = '',
) -> None:
    """Запоминает страницу выдачи ключа для контекстной команды /yaa."""
    if not viewer_id:
        return

    try:
        from config import ADMIN_IDS
        from bot.services.page_context import remember_page_context

        if viewer_id not in ADMIN_IDS:
            return

        render_context = {
            'page_key': KEY_DELIVERY_PAGE,
            'telegram_id': viewer_id,
            KEY_DELIVERY_CONTEXT_RAW: raw_value,
            KEY_DELIVERY_CONTEXT_KIND: kind,
            KEY_DELIVERY_CONTEXT_IS_NEW: is_new,
            KEY_DELIVERY_CONTEXT_ATTACH_MARKUP: attach_markup,
        }
        if bot_username:
            render_context['bot_username'] = bot_username

        remember_page_context(
            viewer_id,
            page_key=KEY_DELIVERY_PAGE,
            message=rendered_message,
            context=render_context,
            text_replacements=build_key_delivery_replacements(raw_value),
        )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст страницы выдачи ключа для /yaa: %s", e)


async def render_key_delivery_page(
    messageable,
    raw_value: str,
    key_manage_markup: Optional[InlineKeyboardMarkup] = None,
    is_new: bool = False,
    kind: str = 'key',
    attach_markup: bool = True,
    viewer_id: Optional[int] = None,
) -> Message:
    """Рендерит специальную страницу выдачи ключа с QR и запоминает её для /yaa."""
    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Не удалось определить сообщение для выдачи ключа")

    resolved_viewer_id = viewer_id if viewer_id is not None else _get_viewer_id(messageable)
    bot_username = _get_bot_username(messageable)
    reply_markup = (
        _get_key_delivery_markup(
            key_manage_markup,
            raw_value,
            viewer_id=resolved_viewer_id,
            bot_username=bot_username,
        )
        if attach_markup else None
    )
    rendered_message = await _render_key_delivery_photo(
        target_message=target_message,
        raw_value=raw_value,
        reply_markup=reply_markup,
        is_new=is_new,
        kind=kind,
        viewer_id=resolved_viewer_id,
        bot_username=bot_username,
    )
    _remember_key_delivery_context(
        viewer_id=resolved_viewer_id,
        rendered_message=rendered_message,
        raw_value=raw_value,
        is_new=is_new,
        kind=kind,
        attach_markup=attach_markup,
        bot_username=bot_username,
    )
    return rendered_message


async def rerender_key_delivery_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённую страницу выдачи ключа после изменения через /yaa."""
    context = page_context.context or {}
    raw_value = context.get(KEY_DELIVERY_CONTEXT_RAW)
    if not raw_value:
        return False

    await render_key_delivery_page(
        page_context.message,
        raw_value=raw_value,
        is_new=bool(context.get(KEY_DELIVERY_CONTEXT_IS_NEW)),
        kind=context.get(KEY_DELIVERY_CONTEXT_KIND) or 'key',
        attach_markup=bool(context.get(KEY_DELIVERY_CONTEXT_ATTACH_MARKUP, True)),
        viewer_id=viewer_id,
    )
    return True


async def send_key_with_qr(
    messageable,
    key_data: dict,
    key_manage_markup: InlineKeyboardMarkup = None,
    is_new: bool = False
):
    """
    Отправляет пользователю ключ с QR-кодом и файлом конфигурации.

    Использует единый HTML-контракт для текстов из редактора.

    В режиме subscription (key_data['sub_id'] не пустой И is_subscription_mode):
    выдаёт subscription URL и QR этой ссылки; JSON-файл не отправляется.

    Args:
        messageable: Объект Message или CallbackQuery, куда отвечать
        key_data: Данные ключа из БД (должны содержать server_id, panel_email, client_uuid)
        key_manage_markup: Клавиатура управления ключом
        is_new: Является ли ключ только что созданным
    """
    from bot.services.vpn_api import is_subscription_mode, get_subscription_url_for_key

    try:
        # Проверяем наличие необходимых данных
        if not key_data:
            await _send_error(messageable, "Ключ не найден или не принадлежит пользователю", key_manage_markup)
            return

        if not key_data.get('server_id') or not key_data.get('panel_email'):
            await _send_error(messageable, "Неполные данные ключа", key_manage_markup)
            return

        # === Subscription mode: выдаём subscription URL + QR этой ссылки ===
        if key_data.get('sub_id') and is_subscription_mode():
            sub_url = await get_subscription_url_for_key(key_data)
            if not sub_url:
                await _send_error(messageable,
                    "Не удалось получить subscription URL. "
                    "Проверьте, что на панели 3X-UI включена подписка "
                    "(Settings → Subscription → Enable).",
                    key_manage_markup)
                return

            await render_key_delivery_page(
                messageable,
                raw_value=sub_url,
                key_manage_markup=key_manage_markup,
                is_new=is_new,
                kind='subscription',
                attach_markup=True,
            )
            return

        # === Keys-mode: текущая логика (ссылка + QR + JSON) ===

        # 1. Получаем конфигурацию с сервера
        try:
            client = await get_client(key_data['server_id'])
            config = await client.get_client_config(key_data['panel_email'])
        except Exception as e:
            logger.error(f"Failed to get client config: {e}")
            config = None
            
        if not config:
            # Если не удалось получить конфиг (например, сервер недоступен),
            # показываем UUID через page-backed статус без генерации некорректного QR.
            uuid = key_data.get('client_uuid', 'Unknown')
            await _send_partial_key_config_fallback(messageable, uuid, key_manage_markup)
            return

        # 2. Генерируем данные
        logger.info(f"Generating key for {key_data.get('panel_email')} (protocol: {config.get('protocol', 'vless')})")
        link = generate_link(config)
        viewer_id = _get_viewer_id(messageable)
        bot_username = _get_bot_username(messageable)
        json_document_markup = _get_json_document_markup(
            key_manage_markup,
            link,
            viewer_id=viewer_id,
            bot_username=bot_username,
        )
            
        json_config = generate_json(config)
        # 3. Отправляем страницу выдачи ключа как QR-фото.
        # В keys-mode клавиатура остаётся у JSON-файла, чтобы она была под последним сообщением.
        await render_key_delivery_page(
            messageable,
            raw_value=link,
            key_manage_markup=key_manage_markup,
            is_new=is_new,
            kind='key',
            attach_markup=False,
        )

        # 4. Отправляем JSON конфиг файлом
        config_file = BufferedInputFile(json_config.encode('utf-8'), filename=f"vpn_config_{key_data.get('id', 'new')}.json")

        # Отправляем файл и клавиатуру отдельным сообщением
        if hasattr(messageable, 'message'): # Это CallbackQuery
            answer_func = messageable.message.answer_document
        else: # Это Message
            answer_func = messageable.answer_document

        await answer_func(
            document=config_file,
            caption="📂 <b>Файл конфигурации</b> (для ручного импорта)",
            reply_markup=json_document_markup,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error sending key: {e}")
        await _send_error(messageable, f"Ошибка отправки ключа: {e}", key_manage_markup)


async def _send_error(messageable, text, markup):
    """Отправляет сообщение об ошибке."""
    from bot.utils.key_status_page import render_key_status_page

    append_buttons = getattr(markup, 'inline_keyboard', None) if markup else None
    # Определяем Message для safe_edit_or_send
    if hasattr(messageable, 'text') or hasattr(messageable, 'photo'):
        # Это Message
        await render_key_status_page(
            messageable,
            title_html='❌ <b>Ошибка выдачи ключа</b>',
            body_text=text,
            append_buttons=append_buttons,
        )
    elif hasattr(messageable, 'message'):
        # Это CallbackQuery
        await render_key_status_page(
            messageable.message,
            title_html='❌ <b>Ошибка выдачи ключа</b>',
            body_text=text,
            append_buttons=append_buttons,
        )
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(f"❌ {text}", reply_markup=markup)


async def _send_partial_key_config_fallback(messageable, raw_value: str, markup):
    """Показывает UUID ключа, если полный конфиг временно недоступен."""
    from bot.utils.key_status_page import render_key_status_page

    body_html = (
        f"👇 <b>UUID ключа:</b>\n"
        f"{format_key_copy_value(raw_value)}\n\n"
        "☝️ Нажмите на ключ, чтобы скопировать.\n"
        "⚠️ Не удалось получить полную конфигурацию (сервер недоступен).\n"
        "Попробуйте позже."
    )
    append_buttons = getattr(markup, 'inline_keyboard', None) if markup else None

    if hasattr(messageable, 'text') or hasattr(messageable, 'photo'):
        await render_key_status_page(
            messageable,
            title_html='📋 <b>Ваш VPN-ключ</b>',
            body_html=body_html,
            append_buttons=append_buttons,
        )
    elif hasattr(messageable, 'message'):
        await render_key_status_page(
            messageable.message,
            title_html='📋 <b>Ваш VPN-ключ</b>',
            body_html=body_html,
            append_buttons=append_buttons,
        )
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(
            f"📋 <b>Ваш VPN-ключ</b>\n\n{body_html}",
            reply_markup=markup,
            parse_mode="HTML",
        )
