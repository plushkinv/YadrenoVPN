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
    thread_id: int | None = None,
) -> Message:
    """Send a fresh Rich Markdown final with an HTML compatibility fallback."""
    target = {"chat_id": chat_id}
    if thread_id is not None:
        target["message_thread_id"] = thread_id
    if rich_markdown:
        try:
            result = await bot.send_rich_message(
                **target,
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
        **target,
        text=fallback_html,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def deliver_final_images(bot, chat_id, final, *, api_key=None, thread_id=None):
    """Deliver verified photo previews independently and remember each successful Telegram send."""
    import aiohttp
    from aiogram.exceptions import TelegramAPIError
    from aiogram.types import FSInputFile
    from bot.keyboards.admin_yadreno import original_image_keyboard
    from bot.services.yadreno_admin_images import ImageError
    from database.requests import get_yadreno_admin_api_key
    from localization import text

    request = getattr(final, "image_request", None)
    if request is None:
        return
    failed = final.invalid_attachments
    api_key = api_key or get_yadreno_admin_api_key() or ""
    destination = str(chat_id) if thread_id is None else f"{chat_id}:{thread_id}"
    target = {"chat_id": chat_id}
    if thread_id is not None:
        target["message_thread_id"] = thread_id
    async with request.delivery_lock(), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70)) as session:
        request.mark_pending(True)
        for attachment in final.attachments:
            image = attachment.recommended
            if request.sent(destination, attachment.result_id):
                continue
            try:
                async with request.download(session, api_key, image) as original:
                    await bot.send_photo(**target, photo=FSInputFile(original, filename=image.filename),
                                         reply_markup=original_image_keyboard(attachment.original.download_url))
                request.sent(destination, attachment.result_id, confirm=True)
            except (ImageError, TelegramAPIError, OSError) as error:
                failed += 1
                logger.warning("Satellite image delivery failed for request %s: %s", request.request_id, type(error).__name__)
        if failed:
            await bot.send_message(**target, text=text("yadreno_admin.images_failed", count=failed), parse_mode="HTML")
        if failed == final.invalid_attachments:
            request.mark_pending(False)
