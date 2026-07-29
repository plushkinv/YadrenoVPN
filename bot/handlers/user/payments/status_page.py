"""Page-backed payment verification status screens."""
from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.callbacks import safe_answer_callback
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page, render_page_text
from bot.utils.text import escape_html, html_to_plain_text, safe_edit_or_send

PAYMENT_STATUS_PAGE_KEY = 'payment_status'
CALLBACK_NOTIFICATION_TEXT_LIMIT = 200

logger = logging.getLogger(__name__)


def build_payment_status_page_context(
    *,
    title_html: str,
    body_html: str,
    hint_text: str = '',
    payment_provider_title: str = '',
) -> dict[str, Any]:
    """Collects context for page-backed payment status."""
    context: dict[str, Any] = {
        'payment_provider_title_html': title_html,
        'payment_instruction_html': body_html,
        'payment_hint_text': hint_text,
    }
    if payment_provider_title:
        context['payment_provider_title'] = payment_provider_title
    return context


def _runtime_rows(markup: Optional[InlineKeyboardMarkup]) -> Optional[list[list[InlineKeyboardButton]]]:
    return getattr(markup, 'inline_keyboard', None) if markup else None


async def show_payment_status_page(
    message,
    *,
    context: dict[str, Any],
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    force_new: bool = False,
    send_func=None,
):
    """Shows page-backed payment status."""
    return await render_page(
        message,
        page_key=PAYMENT_STATUS_PAGE_KEY,
        context=context,
        append_buttons=_runtime_rows(reply_markup),
        force_new=force_new,
        send_func=send_func,
    )


async def show_payment_status_message(
    message,
    *,
    title_html: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    hint_text: str = '',
    payment_provider_title: str = '',
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    force_new: bool = False,
    send_func=None,
):
    """Shows typical page-backed payment status by title and text."""
    if body_html is None:
        from bot.utils.text import escape_html

        body_html = escape_html('' if body_text is None else str(body_text))

    return await show_payment_status_page(
        message,
        context=build_payment_status_page_context(
            title_html=title_html,
            body_html=body_html,
            hint_text=hint_text,
            payment_provider_title=payment_provider_title,
        ),
        reply_markup=reply_markup,
        force_new=force_new,
        send_func=send_func,
    )


async def show_payment_unavailable_status(
    message,
    reason: str,
    *,
    payment_provider_title: str = '',
    send_func=None,
):
    """Shows the typical status of an unavailable payment method."""
    return await render_page(message, 'payment_unavailable', send_func=send_func)


async def show_payment_configuration_status(
    message,
    *,
    title_html: str = '',
    body_html: str | None = None,
    body_text: str | None = None,
    payment_provider_title: str = '',
    send_func=None,
):
    """Shows the typical status of a payment method setup error."""
    return await render_page(message, 'payment_unavailable', send_func=send_func)


async def answer_payment_status_notification(
    callback,
    page_key: str,
    **context_values: Any,
) -> bool:
    """
    Shows page-backed payment copy as a callback toast without replacing the invoice.

    If Telegram has already expired the callback query after a slow provider
    response, the same plain text is sent as a new buttonless message.
    """
    context = build_page_flow_context(callback, **context_values)
    rendered = render_page_text(page_key, context=context)
    text = html_to_plain_text(rendered)[:CALLBACK_NOTIFICATION_TEXT_LIMIT]
    answered = await safe_answer_callback(callback, text=text or None)
    if answered or not text:
        return answered

    message = getattr(callback, 'message', None)
    if message is None:
        return False
    try:
        await safe_edit_or_send(
            message,
            escape_html(text),
            force_new=True,
        )
    except Exception:
        logger.warning(
            "Failed to send fallback payment notification page=%s",
            page_key,
            exc_info=True,
        )
    return False

