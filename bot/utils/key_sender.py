"""
A utility for sending VPN keys to the user.
"""
import logging
from types import SimpleNamespace
from typing import Mapping, Optional

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from bot.services.vpn_api import get_client
from bot.utils.key_generator import generate_link, generate_json, generate_qr_code
from bot.utils.placeholders import (
    KEY_FIELDS_CONTEXT_KEY,
    apply_page_placeholders,
)
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


def format_key_copy_value(raw_value: str) -> str:
    """Formats the key/subscription as a copyable monospace fragment."""
    return f"<code>{escape_html(raw_value)}</code>"


def format_key_plain_link(raw_value: str) -> str:
    """
    Returns a clean link without code/pre.

    Telegram shows HTTP/HTTPS subscription links as clickable. For
    custom schemes like vless:// the link remains in plain text if the client
    Telegram does not support such a transition.
    """
    return escape_html(raw_value)


def build_key_delivery_text(
    template: str,
    raw_value: str,
    context: Optional[Mapping[str, object]] = None,
) -> str:
    """Substitutes placeholders for issuing a key into the edited text."""
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
    """Returns secure HTML substitutions for the key issuance page."""
    return {
        KEY_COPY_PLACEHOLDER: format_key_copy_value(raw_value),
        KEY_LINK_PLACEHOLDER: format_key_plain_link(raw_value),
    }


def _add_key_fields(
    context: Mapping[str, object],
    key_fields: Optional[Mapping[str, object]],
) -> dict[str, object]:
    """Adds only allowlisted display fields to a key-delivery render context."""
    result = dict(context)
    if isinstance(key_fields, Mapping) and key_fields:
        result[KEY_FIELDS_CONTEXT_KEY] = dict(key_fields)
    return result


def _get_target_message(messageable) -> Optional[Message]:
    """Returns the message to be edited via safe_edit_or_send."""
    if isinstance(messageable, Message):
        return messageable
    return getattr(messageable, 'message', None)


def build_key_delivery_target(source, message: Optional[Message]):
    """Preserves source context while continuing rendering from the current message."""
    if message is None:
        return source

    source_message = getattr(source, 'message', None)
    bot = (
        getattr(source, 'bot', None)
        or getattr(source_message, 'bot', None)
        or getattr(message, 'bot', None)
    )
    chat = (
        getattr(message, 'chat', None)
        or getattr(source, 'chat', None)
        or getattr(source_message, 'chat', None)
    )
    return SimpleNamespace(
        message=message,
        from_user=getattr(source, 'from_user', None),
        bot=bot,
        chat=chat,
    )


def _get_viewer_id(messageable) -> Optional[int]:
    """Returns the Telegram ID of the user who sees the page."""
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
    """Returns the username of the bot for placeholders of the key issuance page."""
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
    key_fields: Optional[Mapping[str, object]] = None,
) -> Optional[InlineKeyboardMarkup]:
    """Takes the page keyboard from the database if it is available, otherwise uses fallback."""
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
        render_context = _add_key_fields(render_context, key_fields)

        markup = build_page_keyboard(
            KEY_DELIVERY_PAGE,
            context=render_context,
            text_replacements=build_key_delivery_replacements(raw_value),
        )
        return markup
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру страницы выдачи ключа: %s", e)
        return None


def _get_json_document_markup(
    fallback_markup: Optional[InlineKeyboardMarkup],
    raw_value: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
    key_fields: Optional[Mapping[str, object]] = None,
) -> Optional[InlineKeyboardMarkup]:
    """Returns page-backed buttons for issuing a key for a JSON file."""
    return _get_key_delivery_markup(
        fallback_markup,
        raw_value,
        viewer_id=viewer_id,
        bot_username=bot_username,
        key_fields=key_fields,
    )


def _build_key_delivery_caption(
    raw_value: str,
    is_new: bool,
    kind: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
    key_fields: Optional[Mapping[str, object]] = None,
) -> str:
    """Collects caption for issuing a key/subscription taking into account the Telegram limit."""
    from bot.utils.page_renderer import render_page_text

    context = {}
    if viewer_id:
        context['telegram_id'] = viewer_id
    if bot_username:
        context['bot_username'] = bot_username
    context = _add_key_fields(context, key_fields)
    caption = render_page_text(
        KEY_DELIVERY_PAGE,
        context=context,
        text_replacements=build_key_delivery_replacements(raw_value),
    )
    if caption is None:
        raise RuntimeError(f"Missing required page: {KEY_DELIVERY_PAGE}")

    if len(caption) <= 1024:
        return caption
    compact = render_page_text(
        'key_delivery_partial',
        context=context,
        text_replacements=build_key_delivery_replacements(raw_value),
    )
    if compact is None or len(compact) > 1024:
        raise RuntimeError("key_delivery_partial must fit Telegram's caption limit")
    return compact


async def _render_key_delivery_photo(
    target_message: Message,
    raw_value: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    is_new: bool,
    kind: str,
    viewer_id: Optional[int] = None,
    bot_username: str = '',
    key_fields: Optional[Mapping[str, object]] = None,
) -> Message:
    """Sends or edits a QR photo of the key issuance page."""
    from bot.utils.text import safe_edit_or_send

    caption = _build_key_delivery_caption(
        raw_value,
        is_new,
        kind,
        viewer_id=viewer_id,
        bot_username=bot_username,
        key_fields=key_fields,
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
    key_fields: Optional[Mapping[str, object]] = None,
) -> None:
    """Remembers the key issuing page for the /yaa context command."""
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
        render_context = _add_key_fields(render_context, key_fields)

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
    key_fields: Optional[Mapping[str, object]] = None,
) -> Message:
    """Renders a special page for issuing a key with a QR and remembers it for /yaa."""
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
            key_fields=key_fields,
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
        key_fields=key_fields,
    )
    _remember_key_delivery_context(
        viewer_id=resolved_viewer_id,
        rendered_message=rendered_message,
        raw_value=raw_value,
        is_new=is_new,
        kind=kind,
        attach_markup=attach_markup,
        bot_username=bot_username,
        key_fields=key_fields,
    )
    return rendered_message


async def rerender_key_delivery_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved key issuance page after changing via /yaa."""
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
        key_fields=context.get(KEY_FIELDS_CONTEXT_KEY),
    )
    return True


async def send_key_with_qr(
    messageable,
    key_data: dict,
    key_manage_markup: InlineKeyboardMarkup = None,
    is_new: bool = False
):
    """
    Sends the user a key with a QR code and a configuration file.

    Uses a single HTML contract for texts from the editor.

    In subscription mode (key_data['sub_id'] is not empty AND is_subscription_mode):
    returns the subscription URL and QR of this link; The JSON file is not sent.

    Args:
        messageable: Message or CallbackQuery object where to respond
        key_data: Key data from the database (must contain server_id, panel_email, client_uuid)
        key_manage_markup: Key management keyboard
        is_new: Whether the key is newly created
    """
    from bot.services.vpn_api import is_subscription_mode, get_subscription_url_for_key
    from bot.utils.key_pages import build_key_page_context

    try:
        # We check the availability of the necessary data
        if not key_data:
            logger.warning('Key delivery requested without key data')
            await _send_error(messageable)
            return

        if not key_data.get('server_id') or not key_data.get('panel_email'):
            logger.warning('Key %s has incomplete delivery data', key_data.get('id'))
            await _send_error(messageable)
            return

        # === Subscription mode: issue subscription URL + QR of this link ===
        if key_data.get('sub_id') and is_subscription_mode():
            sub_url = await get_subscription_url_for_key(key_data)
            if not sub_url:
                logger.error('Subscription URL is unavailable for key %s', key_data.get('id'))
                await _send_error(messageable)
                return

            from bot.utils.user_ui_texts import render_ui_text

            key_fields = build_key_page_context(
                key_data,
                inbound=render_ui_text('key.inbound.all_protocols'),
                protocol='SUBSCRIPTION',
            )[KEY_FIELDS_CONTEXT_KEY]
            await render_key_delivery_page(
                messageable,
                raw_value=sub_url,
                key_manage_markup=key_manage_markup,
                is_new=is_new,
                kind='subscription',
                attach_markup=True,
                key_fields=key_fields,
            )
            return

        # === Keys-mode: current logic (link + QR + JSON) ===

        # 1. Receive the configuration from the server
        try:
            client = await get_client(key_data['server_id'])
            config = await client.get_client_config(key_data['panel_email'])
        except Exception as e:
            logger.error(f"Failed to get client config: {e}")
            config = None
            
        if not config:
            # If it was not possible to obtain the config (for example, the server is unavailable),
            # We show the UUID through the page-backed status without generating an incorrect QR.
            uuid = key_data.get('client_uuid', 'Unknown')
            await _send_partial_key_config_fallback(messageable, uuid, key_manage_markup)
            return

        # 2. Generate data
        logger.info(f"Generating key for {key_data.get('panel_email')} (protocol: {config.get('protocol', 'vless')})")
        link = generate_link(config)
        key_fields = build_key_page_context(
            key_data,
            inbound=config.get('remark', 'VPN') or 'VPN',
            protocol=str(config.get('protocol') or 'vless').upper(),
        )[KEY_FIELDS_CONTEXT_KEY]
        viewer_id = _get_viewer_id(messageable)
        bot_username = _get_bot_username(messageable)
        json_document_markup = _get_json_document_markup(
            key_manage_markup,
            link,
            viewer_id=viewer_id,
            bot_username=bot_username,
            key_fields=key_fields,
        )
            
        json_config = generate_json(config)
        # 3. Send the key issuance page as a QR photo.
        # In keys-mode, the keyboard remains in the JSON file so that it is under the last message.
        await render_key_delivery_page(
            messageable,
            raw_value=link,
            key_manage_markup=key_manage_markup,
            is_new=is_new,
            kind='key',
            attach_markup=False,
            key_fields=key_fields,
        )

        # 4. Send JSON config file
        config_file = BufferedInputFile(json_config.encode('utf-8'), filename=f"vpn_config_{key_data.get('id', 'new')}.json")

        # Send the file and keyboard as a separate message
        if hasattr(messageable, 'message'): # This is CallbackQuery
            answer_func = messageable.message.answer_document
        else: # This is Message
            answer_func = messageable.answer_document

        await answer_func(
            document=config_file,
            reply_markup=json_document_markup,
        )

    except Exception as e:
        logger.error(f"Error sending key: {e}")
        await _send_error(messageable)


async def _send_error(messageable):
    """Render the database-backed key delivery failure page."""
    from bot.utils.page_renderer import render_page

    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Key delivery target has no message")
    await render_page(target_message, page_key='key_delivery_failed')


async def _send_partial_key_config_fallback(messageable, raw_value: str, markup):
    """Shows the UUID of the key if the full config is temporarily unavailable."""
    from bot.utils.page_renderer import render_page

    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Key delivery target has no message")
    await render_page(
        target_message,
        page_key='key_delivery_partial',
        text_replacements=build_key_delivery_replacements(raw_value),
    )
