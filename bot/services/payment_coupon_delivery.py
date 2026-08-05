"""Optional database-backed delivery of a payment coupon as a separate message."""
from __future__ import annotations

import logging
from typing import Any

from bot.utils.page_dynamic_data import build_payment_coupon_context_values
from bot.utils.page_renderer import (
    PreparedPageRender,
    get_page_data,
    prepare_page_render,
    render_page,
)
from bot.utils.text import send_media_or_text

logger = logging.getLogger(__name__)

PAYMENT_COUPON_MESSAGE_PAGE_KEY = 'payment_coupon_message'


def _build_delivery_context(
    order_id: str | None,
    telegram_id: int | None,
) -> dict[str, Any] | None:
    """Returns owned order context only when that order has an issued coupon."""
    normalized_order_id = order_id.strip() if isinstance(order_id, str) else ''
    viewer_id = int(telegram_id or 0)
    coupon_context = build_payment_coupon_context_values(
        normalized_order_id,
        viewer_id,
    )
    if not coupon_context.get('payment_coupon_html'):
        return None
    return {
        'telegram_id': viewer_id,
        'order_id': normalized_order_id,
        **coupon_context,
    }


def _configured_page_data() -> dict[str, Any] | None:
    """Returns the optional page only after an administrator gives it content."""
    page_data = get_page_data(PAYMENT_COUPON_MESSAGE_PAGE_KEY)
    if page_data is None:
        raise RuntimeError(
            f"Required user page is missing: {PAYMENT_COUPON_MESSAGE_PAGE_KEY}"
        )
    if not str(page_data.get('text') or '').strip() and not page_data.get('image'):
        return None
    return page_data


async def render_optional_payment_coupon_message(
    target,
    *,
    order_id: str | None,
    telegram_id: int | None,
) -> Any | None:
    """Renders the opt-in separate coupon page in an interactive payment flow."""
    try:
        context = _build_delivery_context(order_id, telegram_id)
        if context is None or _configured_page_data() is None:
            return None
        return await render_page(
            target,
            PAYMENT_COUPON_MESSAGE_PAGE_KEY,
            context=context,
            force_new=True,
        )
    except Exception as error:
        logger.warning(
            "Failed to render optional payment coupon message order=%s: %s",
            order_id,
            error,
        )
        return None


async def send_optional_payment_coupon_message(
    bot,
    *,
    telegram_id: int | None,
    order_id: str | None,
) -> bool:
    """Sends the opt-in separate coupon page from a background payment flow."""
    try:
        context = _build_delivery_context(order_id, telegram_id)
        page_data = _configured_page_data() if context is not None else None
        if context is None or page_data is None:
            return False

        prepared = await prepare_page_render(
            bot,
            PAYMENT_COUPON_MESSAGE_PAGE_KEY,
            context=context,
        )
        if not isinstance(prepared, PreparedPageRender):
            logger.info(
                "Optional payment coupon delivery skipped by page flow: order=%s",
                order_id,
            )
            return False
        await send_media_or_text(
            bot,
            chat_id=int(telegram_id or 0),
            text=prepared.text,
            media=prepared.media,
            media_type=prepared.media_type,
            reply_markup=prepared.reply_markup,
        )
        return True
    except Exception as error:
        logger.warning(
            "Failed to send optional payment coupon message order=%s: %s",
            order_id,
            error,
        )
        return False


__all__ = [
    'PAYMENT_COUPON_MESSAGE_PAGE_KEY',
    'render_optional_payment_coupon_message',
    'send_optional_payment_coupon_message',
]
