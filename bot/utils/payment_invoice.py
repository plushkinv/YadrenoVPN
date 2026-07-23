"""Database-backed Telegram Invoice text helpers."""
from __future__ import annotations

from bot.utils.user_ui_texts import render_ui_text


def clamp_invoice_text(value: object, limit: int) -> str:
    """Return a non-empty Telegram invoice field within its character limit."""
    text = str(value or '').strip()
    if not text:
        raise ValueError('Telegram invoice text must not be empty')
    return text[:limit]


def purchase_invoice_description(tariff_name: object, days: object) -> str:
    """Render a purchase description and reusable line-item label."""
    return render_ui_text(
        'payment.invoice.purchase_description',
        tariff_name=tariff_name,
        days=render_ui_text('format.days_short', days=days),
    )


def renewal_invoice_description(key_name: object, tariff_name: object) -> str:
    """Render a renewal description and reusable line-item label."""
    return render_ui_text(
        'payment.invoice.renewal_description',
        key_name=key_name,
        tariff_name=tariff_name,
    )


def topup_invoice_description(amount: object, currency: object) -> str:
    """Render a balance top-up description and reusable line-item label."""
    return render_ui_text(
        'payment.invoice.topup_description',
        amount=amount,
        currency=currency,
    )


def invoice_pay_button(amount: object) -> str:
    return render_ui_text('payment.invoice.pay_button', amount=amount)


def invoice_change_method_button() -> str:
    return render_ui_text('payment.invoice.change_method_button')


__all__ = [
    'clamp_invoice_text',
    'invoice_change_method_button',
    'invoice_pay_button',
    'purchase_invoice_description',
    'renewal_invoice_description',
    'topup_invoice_description',
]
