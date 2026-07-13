"""Page-backed wrapper for tariff selection screens for payment methods."""
from __future__ import annotations

from typing import Any, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.page_renderer import render_page
from bot.utils.text import escape_html

PAYMENT_TARIFF_SELECT_PAGE_KEY = 'payment_tariff_select'


def default_payment_tariff_select_page_text() -> str:
    """Default text of the payment tariff selection screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


def _runtime_rows(markup: Optional[InlineKeyboardMarkup]) -> Optional[list[list[InlineKeyboardButton]]]:
    return getattr(markup, 'inline_keyboard', None) if markup is not None else None


def build_payment_tariff_select_page_context(
    *,
    provider_title_html: str,
    instruction_html: str = 'Выберите тариф:',
    key_name: Optional[str] = None,
    hint_text: str = '',
    telegram_id: Optional[int] = None,
    bot_username: str = '',
) -> dict[str, Any]:
    """Collects the general context for page-backed tariff selection."""
    context: dict[str, Any] = {
        'payment_provider_title_html': provider_title_html,
        'payment_key_line_html': (
            f"🔑 Ключ: <b>{escape_html(str(key_name))}</b>\n\n" if key_name else ''
        ),
        'payment_instruction_html': instruction_html,
        'payment_hint_text': hint_text,
    }
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


async def show_payment_tariff_select_page(
    callback: CallbackQuery,
    *,
    context: dict[str, Any],
    runtime_markup: Optional[InlineKeyboardMarkup],
) -> None:
    """Shows the page-backed tariff selection screen with the transferred runtime keyboard."""
    await render_page(
        callback,
        page_key=PAYMENT_TARIFF_SELECT_PAGE_KEY,
        context=context,
        append_buttons=_runtime_rows(runtime_markup),
        fallback_text=default_payment_tariff_select_page_text(),
    )


async def show_payment_no_tariffs_page(
    callback: CallbackQuery,
    *,
    provider_title_html: str,
    instruction_html: str,
    key_name: Optional[str] = None,
    back_callback: Optional[str] = None,
) -> None:
    """Shows a page-backed tariff selection screen with no available tariffs."""
    from bot.keyboards.admin import back_and_home_kb, home_only_kb

    runtime_markup = back_and_home_kb(back_callback) if back_callback else home_only_kb()
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=provider_title_html,
            instruction_html=instruction_html,
            key_name=key_name,
        ),
        runtime_markup=runtime_markup,
    )

