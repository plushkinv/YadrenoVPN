"""Database-backed compatibility keyboard helpers for ordinary users."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.page_button_items import (
    build_provider_tariff_button_items,
    build_tariff_button_items,
)
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


def custom_payment_tariff_select_kb(
    tariffs: list,
    provider_id: str,
    *,
    minimum_amount_cents: int = 0,
    minimum_amount_minor: int | None = None,
    payment_type: str = 'cards',
    back_callback: str = 'buy_key',
) -> InlineKeyboardMarkup:
    """Compatibility wrapper using the page-owned tariff-row label."""
    minimum = minimum_amount_cents if minimum_amount_minor is None else minimum_amount_minor
    return _required_page_keyboard(
        'payment_tariff_select',
        context={
            'tariff_back_callback': back_callback,
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                payment_type,
                lambda tariff_id: f'pet:{provider_id}:{tariff_id}',
                minimum_amount=minimum,
            ),
        },
    )


def custom_payment_renew_tariff_select_kb(
    tariffs: list,
    provider_id: str,
    key_id: int,
    *,
    minimum_amount_cents: int = 0,
    minimum_amount_minor: int | None = None,
    payment_type: str = 'cards',
) -> InlineKeyboardMarkup:
    """Compatibility wrapper using the renewal page's tariff-row label."""
    minimum = minimum_amount_cents if minimum_amount_minor is None else minimum_amount_minor
    return _required_page_keyboard(
        'renew_payment',
        context={
            'key_id': key_id,
            'tariff_back_callback': f'key_renew:{key_id}',
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                payment_type,
                lambda tariff_id: f'ret:{provider_id}:{key_id}:{tariff_id}',
                minimum_amount=minimum,
            ),
        },
    )


def qr_payment_kb(
    order_id: str,
    check_prefix: str,
    back_callback: str = 'buy_key',
    qr_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Compatibility wrapper around the page-owned payment link controls."""
    return _required_page_keyboard(
        'qr_payment',
        context={
            'order_id': order_id,
            'payment_url': qr_url or '',
            'payment_check_callback': f'{check_prefix}:{order_id}',
            'payment_methods_callback': back_callback,
            'payment_cancel_callback': back_callback,
            'payment_can_check': True,
        },
    )


def yookassa_qr_kb(
    order_id: str,
    back_callback: str = 'buy_key',
    qr_url: str | None = None,
) -> InlineKeyboardMarkup:
    return qr_payment_kb(order_id, 'check_yookassa_qr', back_callback, qr_url)


def wata_qr_kb(
    order_id: str,
    back_callback: str = 'buy_key',
    qr_url: str | None = None,
) -> InlineKeyboardMarkup:
    return qr_payment_kb(order_id, 'check_wata', back_callback, qr_url)


def platega_qr_kb(
    order_id: str,
    back_callback: str = 'buy_key',
    qr_url: str | None = None,
) -> InlineKeyboardMarkup:
    return qr_payment_kb(order_id, 'check_platega', back_callback, qr_url)


def cardlink_qr_kb(
    order_id: str,
    back_callback: str = 'buy_key',
    qr_url: str | None = None,
) -> InlineKeyboardMarkup:
    return qr_payment_kb(order_id, 'check_cardlink', back_callback, qr_url)


__all__ = [
    'balance_topup_cancel_kb',
    'balance_topup_complete_kb',
    'cancel_kb',
    'cardlink_qr_kb',
    'custom_payment_renew_tariff_select_kb',
    'custom_payment_tariff_select_kb',
    'payment_auto_complete_kb',
    'payment_demo_placeholder_kb',
    'payment_intent_link_kb',
    'payment_intent_tariffs_kb',
    'payment_method_select_kb',
    'platega_qr_kb',
    'qr_payment_kb',
    'wata_qr_kb',
    'yookassa_qr_kb',
]
