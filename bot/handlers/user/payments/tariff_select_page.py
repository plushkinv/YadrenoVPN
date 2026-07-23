"""Page-backed wrapper for tariff selection screens for payment methods."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.page_renderer import render_page

PAYMENT_TARIFF_SELECT_PAGE_KEY = 'payment_tariff_select'


def _runtime_rows(markup: Optional[InlineKeyboardMarkup]) -> Optional[list[list[InlineKeyboardButton]]]:
    return getattr(markup, 'inline_keyboard', None) if markup is not None else None


def build_payment_tariff_select_page_context(
    *,
    provider_title_html: str = '',
    instruction_html: str = '',
    key_name: Optional[str] = None,
    hint_text: str = '',
    telegram_id: Optional[int] = None,
    bot_username: str = '',
) -> dict[str, Any]:
    """Collects data-only context for page-backed tariff selection."""
    context: dict[str, Any] = {}
    if key_name:
        from bot.utils.placeholders import KEY_FIELDS_CONTEXT_KEY

        context[KEY_FIELDS_CONTEXT_KEY] = {'name': key_name}
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


async def show_payment_tariff_select_page(
    callback: CallbackQuery,
    *,
    context: dict[str, Any],
    runtime_markup: Optional[InlineKeyboardMarkup] = None,
    page_key: str = PAYMENT_TARIFF_SELECT_PAGE_KEY,
) -> None:
    """Shows the page-backed tariff selection screen with the transferred runtime keyboard."""
    await render_page(
        callback,
        page_key=page_key,
        context=context,
        append_buttons=_runtime_rows(runtime_markup),
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
    await render_page(callback, 'payment_unavailable')


async def show_provider_tariff_select_page(
    callback: CallbackQuery,
    *,
    tariffs: list[dict[str, Any]],
    payment_type: str,
    callback_factory: Callable[[int], str],
    back_callback: str,
    key: dict[str, Any] | None = None,
    minimum_amount: int = 1,
) -> None:
    """Renders a compatibility provider tariff flow using page-owned labels."""
    if not tariffs:
        await render_page(callback, 'payment_unavailable')
        return

    from bot.utils.page_button_items import build_provider_tariff_button_items

    context: dict[str, Any] = {
        'telegram_id': callback.from_user.id,
        'tariff_back_callback': back_callback,
        'tariff_button_items': build_provider_tariff_button_items(
            tariffs,
            payment_type,
            callback_factory,
            minimum_amount=minimum_amount,
        ),
    }
    page_key = PAYMENT_TARIFF_SELECT_PAGE_KEY
    if key:
        from bot.utils.key_pages import build_key_page_context

        page_key = 'renew_payment'
        context['key_id'] = int(key['id'])
        context.update(build_key_page_context(key))
    await show_payment_tariff_select_page(
        callback,
        page_key=page_key,
        context=context,
    )

