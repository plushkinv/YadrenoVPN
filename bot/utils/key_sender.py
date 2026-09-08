"""
A utility for sending VPN keys to the user.
"""
import logging
from types import SimpleNamespace
from typing import Mapping, Optional

from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
)

from bot.utils.qr import generate_qr_code
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
KEY_DELIVERY_CONTEXT_IS_NEW = 'key_delivery_is_new'
KEY_DELIVERY_CONTEXT_ATTACH_MARKUP = 'key_delivery_attach_markup'
KEY_DELIVERY_CONTEXT_ORDER_ID = 'order_id'
KEY_DELIVERY_CONTEXT_ADMIN_RETURN_KEY_ID = 'key_delivery_admin_return_key_id'


class KeyDeliveryError(RuntimeError):
    """Signals that a configured key could not be delivered to Telegram."""


def _key_delivery_navigation(admin_return_key_id: int | None) -> dict:
    """Keep administrator navigation in the normal page render inputs."""
    if admin_return_key_id is None:
        return {}
    from bot.keyboards.admin_users import key_delivery_admin_kb

    return {
        'context': {KEY_DELIVERY_CONTEXT_ADMIN_RETURN_KEY_ID: admin_return_key_id},
        'visibility': {'btn_my_keys': False, 'btn_back_main': False},
        'append_buttons': key_delivery_admin_kb(admin_return_key_id).inline_keyboard,
    }


def format_key_copy_value(raw_value: str) -> str:
    """Formats the key/subscription as a copyable monospace fragment."""
    return f"<code>{escape_html(raw_value)}</code>"


def format_key_plain_link(raw_value: str) -> str:
    """Return the escaped HTTP subscription link without code/pre markup."""
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


def _add_payment_order(
    context: Mapping[str, object],
    order_id: str | None,
) -> dict[str, object]:
    """Adds a validated payment identifier to a key-delivery render context."""
    result = dict(context)
    normalized = order_id.strip() if isinstance(order_id, str) else ''
    if normalized:
        result[KEY_DELIVERY_CONTEXT_ORDER_ID] = normalized
    return result


def _get_target_message(messageable) -> Optional[Message]:
    """Returns the message to be edited via safe_edit_or_send."""
    if isinstance(messageable, Message):
        return messageable
    nested_message = getattr(messageable, 'message', None)
    if nested_message is not None:
        return nested_message
    # Preserve lightweight message-like adapters used by controlled services
    # and tests without treating arbitrary wrappers as Telegram messages.
    if callable(getattr(messageable, 'answer_photo', None)):
        return messageable
    return None


def _get_key_delivery_page_flow_target(messageable):
    """Use the real Bot in background and preserve interactive flow targets."""
    if getattr(messageable, 'is_background_delivery', False):
        bot = getattr(messageable, 'bot', None)
        if bot is None:
            raise ValueError("Background key delivery target has no bot")
        return bot
    return messageable


def _get_key_delivery_transport_target(messageable):
    """Return only Telegram-native objects to the message transport layer."""
    if isinstance(messageable, (Message, CallbackQuery)):
        return messageable
    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Key delivery target has no message")
    return target_message


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


async def _prepare_key_delivery_page(
    messageable,
    *,
    raw_value: str,
    is_new: bool,
    attach_markup: bool,
    viewer_id: Optional[int],
    bot_username: str,
    key_fields: Optional[Mapping[str, object]],
    order_id: str | None,
    route_key: str | None = None,
    admin_return_key_id: int | None = None,
):
    """Prepares key-delivery text, media and keyboard through one page flow."""
    from bot.utils.page_renderer import PreparedPageRender, prepare_page_render, render_page_text

    render_context = {
        KEY_DELIVERY_CONTEXT_RAW: raw_value,
        KEY_DELIVERY_CONTEXT_IS_NEW: is_new,
        KEY_DELIVERY_CONTEXT_ATTACH_MARKUP: attach_markup,
    }
    if viewer_id:
        render_context['telegram_id'] = viewer_id
    if bot_username:
        render_context['bot_username'] = bot_username
    render_context = _add_key_fields(render_context, key_fields)
    render_context = _add_payment_order(render_context, order_id)
    navigation = _key_delivery_navigation(admin_return_key_id)
    render_context.update(navigation.pop('context', {}))

    prepared = await prepare_page_render(
        _get_key_delivery_page_flow_target(messageable),
        KEY_DELIVERY_PAGE,
        route_key=route_key,
        context=render_context,
        text_replacements=build_key_delivery_replacements(raw_value),
        **navigation,
    )
    if not isinstance(prepared, PreparedPageRender):
        return prepared
    if prepared.page_key != KEY_DELIVERY_PAGE:
        return prepared

    if len(prepared.text) > 1024:
        compact = render_page_text(
            'key_delivery_partial',
            context=prepared.effective_inputs.context,
            text_replacements=prepared.effective_inputs.text_replacements,
        )
        if compact is None or len(compact) > 1024:
            raise RuntimeError('key_delivery_partial must fit Telegram caption limit')
        prepared.text = compact

    filename = 'subscription_qr.png'
    prepared.media = BufferedInputFile(generate_qr_code(raw_value), filename=filename)
    prepared.media_type = 'photo'
    return prepared


async def _deliver_prepared_key_delivery(
    messageable,
    prepared,
    *,
    attach_markup: bool,
) -> Optional[Message]:
    from bot.utils.page_renderer import deliver_page_prepare_outcome

    delivery = await deliver_page_prepare_outcome(
        _get_key_delivery_transport_target(messageable),
        prepared,
        reply_markup_override=(
            prepared.reply_markup
            if attach_markup and hasattr(prepared, 'reply_markup')
            else None
        ),
    )
    return delivery.message


async def render_key_delivery_page(
    messageable,
    raw_value: str,
    is_new: bool = False,
    attach_markup: bool = True,
    viewer_id: Optional[int] = None,
    key_fields: Optional[Mapping[str, object]] = None,
    order_id: str | None = None,
    route_key: str | None = None,
    *,
    admin_return_key_id: int | None = None,
    notify_new_delivery: bool = False,
) -> Optional[Message]:
    """Renders a special page for issuing a key with a QR and remembers it for /yaa."""
    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Не удалось определить сообщение для выдачи ключа")

    resolved_viewer_id = viewer_id if viewer_id is not None else _get_viewer_id(messageable)
    bot_username = _get_bot_username(messageable)
    prepared = await _prepare_key_delivery_page(
        messageable,
        raw_value=raw_value,
        is_new=is_new,
        attach_markup=attach_markup,
        viewer_id=resolved_viewer_id,
        bot_username=bot_username,
        key_fields=key_fields,
        order_id=order_id,
        route_key=route_key,
        admin_return_key_id=admin_return_key_id,
    )
    rendered_message = await _deliver_prepared_key_delivery(
        messageable,
        prepared,
        attach_markup=attach_markup,
    )
    if (
        notify_new_delivery and is_new and order_id and resolved_viewer_id
        and admin_return_key_id is None and rendered_message is not None
        and getattr(prepared, 'page_key', None) == KEY_DELIVERY_PAGE
    ):
        from bot.services.extension_events import notify_key_delivered

        await notify_key_delivered(
            order_id,
            key_id=(key_fields or {}).get('id'),
            telegram_id=resolved_viewer_id,
            bot=getattr(messageable, 'bot', None) or getattr(target_message, 'bot', None),
        )
    return rendered_message


async def rerender_key_delivery_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved key issuance page after changing via /yaa."""
    context = page_context.base_context or page_context.context or {}
    raw_value = context.get(KEY_DELIVERY_CONTEXT_RAW)
    if not raw_value:
        return False

    await render_key_delivery_page(
        page_context.message,
        raw_value=raw_value,
        is_new=bool(context.get(KEY_DELIVERY_CONTEXT_IS_NEW)),
        attach_markup=bool(context.get(KEY_DELIVERY_CONTEXT_ATTACH_MARKUP, True)),
        viewer_id=viewer_id,
        key_fields=context.get(KEY_FIELDS_CONTEXT_KEY),
        order_id=context.get(KEY_DELIVERY_CONTEXT_ORDER_ID),
        route_key=getattr(page_context, 'route_key', None),
        admin_return_key_id=context.get(KEY_DELIVERY_CONTEXT_ADMIN_RETURN_KEY_ID),
    )
    return True


async def send_key_with_qr(
    messageable,
    key_data: dict,
    is_new: bool = False,
    *,
    order_id: str | None = None,
    raise_on_error: bool = False,
    admin_return_key_id: int | None = None,
):
    """
    Sends the user a subscription URL with its QR code.

    Uses a single HTML contract for texts from the editor.

    Args:
        messageable: Message or CallbackQuery object where to respond
        key_data: Key data from the database (server_id, panel_email and sub_id)
        is_new: Whether the key is newly created
        order_id: Payment order that made this delivery available, if any
        raise_on_error: Surface a retryable delivery failure to a shared flow
        admin_return_key_id: Return to this administrator key card, if supplied
    """
    from bot.services.vpn_api import get_subscription_url_for_key
    from bot.utils.key_pages import build_key_page_context

    navigation_kwargs = (
        {'admin_return_key_id': admin_return_key_id}
        if admin_return_key_id is not None else {}
    )
    try:
        # We check the availability of the necessary data
        if not key_data:
            logger.warning('Key delivery requested without key data')
            await _send_error(messageable, order_id=order_id, **navigation_kwargs)
            if raise_on_error:
                raise KeyDeliveryError('missing_key_data')
            return

        if not all(
            (
                key_data.get('server_id'),
                key_data.get('panel_email'),
                key_data.get('sub_id'),
            )
        ):
            logger.warning('Key %s has incomplete delivery data', key_data.get('id'))
            await _send_error(messageable, order_id=order_id, **navigation_kwargs)
            if raise_on_error:
                raise KeyDeliveryError('incomplete_key_data')
            return

        sub_url = await get_subscription_url_for_key(key_data)
        if not sub_url:
            logger.error('Subscription URL is unavailable for key %s', key_data.get('id'))
            await _send_error(messageable, order_id=order_id, **navigation_kwargs)
            if raise_on_error:
                raise KeyDeliveryError('subscription_url_unavailable')
            return

        key_fields = build_key_page_context(key_data)[KEY_FIELDS_CONTEXT_KEY]
        await render_key_delivery_page(
            messageable,
            raw_value=sub_url,
            is_new=is_new,
            attach_markup=True,
            key_fields=key_fields,
            order_id=order_id,
            **({'notify_new_delivery': True} if is_new and order_id else {}),
            **navigation_kwargs,
        )

    except KeyDeliveryError:
        raise
    except Exception as e:
        logger.error(f"Error sending key: {e}")
        try:
            await _send_error(messageable, order_id=order_id, **navigation_kwargs)
        except Exception as render_error:
            logger.warning("Failed to render key delivery error: %s", render_error)
        if raise_on_error:
            raise KeyDeliveryError('key_delivery_failed') from e


async def _send_error(
    messageable,
    *,
    order_id: str | None = None,
    admin_return_key_id: int | None = None,
):
    """Render the database-backed key delivery failure page."""
    from bot.utils.page_renderer import render_page

    target_message = _get_target_message(messageable)
    if target_message is None:
        raise ValueError("Key delivery target has no message")
    render_kwargs = {
        'page_key': 'key_delivery_failed',
        **_key_delivery_navigation(admin_return_key_id),
    }
    if order_id:
        render_kwargs.setdefault('context', {})['order_id'] = order_id
    if admin_return_key_id is not None:
        render_kwargs['context']['telegram_id'] = _get_viewer_id(messageable)
        target_message = _get_key_delivery_transport_target(messageable)
    await render_page(target_message, **render_kwargs)
