"""Shared transport adapter for database-backed background user pages."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from aiogram.types import Message

logger = logging.getLogger(__name__)


async def send_background_page(
    bot: Any,
    *,
    telegram_id: int,
    page_key: str,
    context: Mapping[str, Any],
) -> Message | None:
    """Prepare and send one stored page without rerunning its guards or hooks."""
    from bot.utils.delivery import is_bot_blocked_error
    from bot.utils.page_renderer import PreparedPageRender, prepare_page_render
    from bot.utils.text import send_media_or_text
    from database.requests import mark_user_bot_blocked

    render_context = dict(context)
    render_context.setdefault("telegram_id", int(telegram_id))
    try:
        prepared = await prepare_page_render(bot, page_key, context=render_context)
        if not isinstance(prepared, PreparedPageRender):
            logger.info(
                "Background page skipped by page flow page=%s order=%s",
                page_key,
                render_context.get("order_id"),
            )
            return None
        return await send_media_or_text(
            bot,
            chat_id=int(telegram_id),
            text=prepared.text,
            media=prepared.media,
            media_type=prepared.media_type,
            reply_markup=prepared.reply_markup,
        )
    except Exception as error:
        if is_bot_blocked_error(error):
            mark_user_bot_blocked(int(telegram_id))
        logger.warning(
            "Failed to deliver background page=%s order=%s: %s",
            page_key,
            render_context.get("order_id"),
            error,
        )
        return None


__all__ = ["send_background_page"]
