"""Page-backed payment verification status screens."""
from __future__ import annotations

from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.page_renderer import render_page

PAYMENT_STATUS_PAGE_KEY = 'payment_status'


def default_payment_status_page_text() -> str:
    """Default text of the payment status screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


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
        fallback_text=default_payment_status_page_text(),
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
    from bot.keyboards.admin import home_only_kb

    return await show_payment_status_message(
        message,
        title_html='⚠️ <b>Способ оплаты недоступен</b>',
        body_text=reason,
        payment_provider_title=payment_provider_title,
        reply_markup=home_only_kb(),
        send_func=send_func,
    )


async def show_payment_configuration_status(
    message,
    *,
    title_html: str = '❌ <b>Ошибка настройки платежей</b>',
    body_html: str | None = None,
    body_text: str | None = None,
    payment_provider_title: str = '',
    send_func=None,
):
    """Shows the typical status of a payment method setup error."""
    from bot.keyboards.admin import home_only_kb

    return await show_payment_status_message(
        message,
        title_html=title_html,
        body_html=body_html,
        body_text=body_text,
        payment_provider_title=payment_provider_title,
        reply_markup=home_only_kb(),
        send_func=send_func,
    )

