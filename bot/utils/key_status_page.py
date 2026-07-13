"""Page-backed status of key operations."""
from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot.utils.page_renderer import render_page, render_page_text
from bot.utils.placeholders import apply_page_placeholders

KEY_STATUS_PAGE_KEY = "key_status"


def default_key_status_page_text() -> str:
    """Default text of the key operation status."""
    return "%ключ_статус_заголовок%\n\n%ключ_статус_текст%"


def build_key_status_page_context(title_html: str, body_html: str) -> dict[str, Any]:
    """Collects the runtime context of the status of an operation with a key."""
    return {
        "key_status_title_html": title_html,
        "key_status_body_html": body_html,
    }


def render_key_status_page_text(context: dict[str, Any]) -> str:
    """Renders the key_status text from pages with fallback set to default."""
    text = render_page_text(KEY_STATUS_PAGE_KEY, context=context)
    if text is not None:
        return text

    fallback_context = {"page_key": KEY_STATUS_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_key_status_page_text(),
        context=fallback_context,
    )


async def render_key_status_page(
    target: Message | CallbackQuery,
    *,
    title_html: str,
    body_html: str | None = None,
    body_text: str | None = None,
    append_buttons: list[list[InlineKeyboardButton]] | None = None,
    force_new: bool = False,
) -> Message | None:
    """Renders the page-backed status of the key operation."""
    if body_html is None:
        from bot.utils.text import escape_html

        body_html = escape_html('' if body_text is None else str(body_text))

    return await render_page(
        target,
        page_key=KEY_STATUS_PAGE_KEY,
        context=build_key_status_page_context(title_html, body_html),
        append_buttons=append_buttons,
        force_new=force_new,
        fallback_text=default_key_status_page_text(),
    )
