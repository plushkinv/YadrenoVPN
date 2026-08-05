"""Database-backed compatibility keyboard helpers for ordinary users."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.page_button_items import build_tariff_button_items
from bot.utils.page_renderer import build_page_keyboard


def _required_page_keyboard(
    page_key: str,
    *,
    context: dict | None = None,
    append_buttons: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    markup = build_page_keyboard(
        page_key,
        context=context,
        append_buttons=append_buttons,
    )
    if markup is None:
        raise RuntimeError(f'Required user page {page_key!r} is missing')
    return markup


def payment_intent_tariffs_kb(
    tariffs: list,
    purpose: str,
    *,
    key_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Deprecated wrapper around the page-owned tariff collection."""
    return _required_page_keyboard(
        'renew_payment' if key_id else 'payment_tariff_select',
        context={
            'key_id': key_id,
            'tariff_back_callback': f'key:{key_id}' if key_id else 'start',
            'tariff_button_items': build_tariff_button_items(
                tariffs,
                purpose,
                key_id=key_id,
            ),
        },
    )


def payment_method_select_kb(
    providers: list,
    order_id: str,
    *,
    allow_balance: bool = False,
    back_callback: str = 'start',
) -> InlineKeyboardMarkup:
    """Deprecated wrapper around the page-owned provider controls."""
    builtins = [provider.provider_id for provider in providers if not provider.custom]
    custom_rows = [
        [
            InlineKeyboardButton(
                text=str(provider.label),
                callback_data=f'payment_intent_provider:{order_id}:{provider.provider_id}',
            )
        ]
        for provider in providers
        if provider.custom
    ]
    return _required_page_keyboard(
        'payment_method_select',
        context={
            'order_id': order_id,
            'payment_provider_ids': builtins,
            'payment_allow_balance': allow_balance,
            'payment_cancel_callback': back_callback,
        },
        append_buttons=custom_rows or None,
    )


def payment_intent_link_kb(
    order_id: str,
    payment_url: str,
    *,
    can_check: bool = True,
) -> InlineKeyboardMarkup:
    """Deprecated wrapper around the page-owned invoice controls."""
    return _required_page_keyboard(
        'qr_payment',
        context={
            'order_id': order_id,
            'payment_url': payment_url,
            'payment_can_check': can_check,
        },
    )


def payment_demo_placeholder_kb(order_id: str) -> InlineKeyboardMarkup:
    return _required_page_keyboard('demo_payment', context={'order_id': order_id})


def balance_topup_cancel_kb() -> InlineKeyboardMarkup:
    return _required_page_keyboard('balance_topup_amount')


def payment_auto_complete_kb() -> InlineKeyboardMarkup:
    return _required_page_keyboard('payment_auto_completed')


def balance_topup_complete_kb() -> InlineKeyboardMarkup:
    return _required_page_keyboard('balance_topup_result')


def cancel_kb(cancel_callback: str) -> InlineKeyboardMarkup:
    return _required_page_keyboard(
        'key_rename_prompt',
        context={'key_flow_back_callback': cancel_callback},
    )


__all__ = [
    'balance_topup_cancel_kb',
    'balance_topup_complete_kb',
    'cancel_kb',
    'payment_auto_complete_kb',
    'payment_demo_placeholder_kb',
    'payment_intent_link_kb',
    'payment_intent_tariffs_kb',
    'payment_method_select_kb',
]
