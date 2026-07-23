"""Page-backed status of key operations."""
from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot.utils.page_renderer import render_page, render_page_text

KEY_STATUS_PAGE_KEY = "key_status"

def build_key_status_page_context(title_html: str, body_html: str) -> dict[str, Any]:
    """Collects the runtime context of the status of an operation with a key."""
    return {
        "key_status_title_html": title_html,
        "key_status_body_html": body_html,
    }


def render_key_status_page_text(context: dict[str, Any]) -> str:
    """Render the legacy extension status page without a code fallback."""
    text = render_page_text(KEY_STATUS_PAGE_KEY, context=context)
    if text is not None:
        return text
    fallback = render_page_text('screen_unavailable', context=context)
    if fallback is None:
        raise RuntimeError("Required page 'screen_unavailable' is missing")
    return fallback


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
    )
