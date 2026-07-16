"""Telegram delivery helpers for Yadreno Admin final responses."""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message

from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)


async def edit_or_send_yadreno_admin_final(
    message: Message,
    *,
    fallback_html: str,
    rich_markdown: Optional[str],
    reply_markup: Any = None,
) -> Message:
    """Replace an agent status with Rich Markdown or the legacy HTML final."""
    if rich_markdown:
        try:
            result = await message.edit_text(
                text=None,
                parse_mode=None,
                rich_message=InputRichMessage(markdown=rich_markdown),
                reply_markup=reply_markup,
            )
            logger.debug("Yadreno Admin final delivered as Rich Markdown edit")
            return result if isinstance(result, Message) else message
        except (TelegramBadRequest, AttributeError, TypeError) as exc:
            logger.warning(
                "Yadreno Admin Rich Markdown edit rejected; using HTML: %s",
                str(exc)[:200],
            )

    return await safe_edit_or_send(
        message,
        fallback_html,
        reply_markup=reply_markup,
    )


async def send_yadreno_admin_final(
    bot: Any,
    *,
    chat_id: int,
    fallback_html: str,
    rich_markdown: Optional[str],
    reply_markup: Any = None,
) -> Message:
    """Send a fresh Rich Markdown final with an HTML compatibility fallback."""
    if rich_markdown:
        try:
            result = await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=InputRichMessage(markdown=rich_markdown),
                reply_markup=reply_markup,
            )
            logger.debug("Yadreno Admin final delivered as fresh Rich Markdown")
            return result
        except (TelegramBadRequest, AttributeError, TypeError) as exc:
            logger.warning(
                "Yadreno Admin Rich Markdown send rejected; using HTML: %s",
                str(exc)[:200],
            )

    return await bot.send_message(
        chat_id=chat_id,
        text=fallback_html,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
