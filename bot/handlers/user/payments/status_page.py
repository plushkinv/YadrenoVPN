"""Page-backed payment verification status screens."""
from __future__ import annotations

import logging
from typing import Any

from bot.utils.callbacks import safe_answer_callback
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page_text
from bot.utils.text import escape_html, html_to_plain_text, safe_edit_or_send

CALLBACK_NOTIFICATION_TEXT_LIMIT = 200

logger = logging.getLogger(__name__)


async def send_payment_status_notification(message, page_key: str, **context_values: Any) -> None:
    """Send a buttonless return-link notice before restoring the saved invoice."""
    text = render_page_text(page_key, context=build_page_flow_context(message, **context_values))
    if text:
        await safe_edit_or_send(message, text, force_new=True)


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

