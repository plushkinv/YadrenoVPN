"""Assembling HTML blocks for editable key pages."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bot.utils.datetime_format import format_date_for_display
from bot.utils.placeholders import KEY_FIELDS_CONTEXT_KEY
from bot.utils.user_ui_texts import render_ui_text


KEY_HISTORY_PLACEHOLDER = '%ключ_история_операций%'


def _default_key_status(key: Mapping[str, Any]) -> str:
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0 and traffic_used >= traffic_limit:
        return render_ui_text("key.status.traffic_exhausted")
    if key.get('is_active'):
        return render_ui_text("key.status.active")
    if key.get('is_active') is not None:
        return render_ui_text("key.status.expired")
    return '—'


def _default_key_traffic(key: Mapping[str, Any]) -> str:
    if not key.get('server_id'):
        return render_ui_text("key.traffic.needs_setup")

    from bot.services.vpn_api import format_traffic

    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0:
        percent = traffic_used / traffic_limit * 100
        return render_ui_text(
            "key.traffic.limited",
            used=format_traffic(traffic_used),
            limit=format_traffic(traffic_limit),
            percent=f"{percent:.1f}",
        )
    if traffic_used > 0:
        return render_ui_text(
            "key.traffic.used_unlimited",
            used=format_traffic(traffic_used),
        )
    return render_ui_text("key.traffic.unlimited")


def build_key_page_context(
    key: Mapping[str, Any],
    *,
    status: str | None = None,
    traffic: str | None = None,
    inbound: str | None = None,
    protocol: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Builds the allowlisted display context for ``%key(field=...)%``."""
    display_name = key.get('display_name') or f"#{key.get('id', '')}"
    server = key.get('server_name') or '—'
    expires = format_date_for_display(key.get('expires_at'))
    tariff = key.get('tariff_name') or '—'
    device_limit = key.get('tariff_max_ips')
    if device_limit is None:
        device_limit = key.get('max_ips')
    if device_limit is None:
        device_limit = '—'

    if inbound is None:
        inbound = render_ui_text("key.inbound.all_protocols") if key.get('sub_id') else '—'
    if protocol is None:
        protocol = 'SUBSCRIPTION' if key.get('sub_id') else '—'

    return {
        KEY_FIELDS_CONTEXT_KEY: {
            'id': key.get('id', ''),
            'name': display_name,
            'status': status if status is not None else _default_key_status(key),
            'traffic': traffic if traffic is not None else _default_key_traffic(key),
            'expires_at': expires,
            'server': server,
            'inbound': inbound,
            'protocol': protocol,
            'tariff': tariff,
            'device_limit': device_limit,
        },
    }


def build_key_history_block(payments: Iterable[Mapping[str, Any]]) -> str:
    """Collects a block of the key's operation history."""
    payment_rows = list(payments or [])
    if not payment_rows:
        return ''

    lines: list[str] = []
    for payment in payment_rows:
        date = format_date_for_display(payment.get('paid_at'))
        if payment.get('history_type') == 'key_operation':
            delta_days = int(payment.get('delta_days') or 0)
            reason = payment.get('reason') or '—'
            if delta_days > 0:
                lines.append(render_ui_text(
                    "key.history.operation_with_days",
                    date=date,
                    operation=reason,
                    days=render_ui_text("format.days_short", days=f"+{delta_days}"),
                ))
            else:
                lines.append(render_ui_text(
                    "key.history.operation",
                    date=date,
                    operation=reason,
                ))
            continue
        tariff = payment.get('tariff_name') or '—'
        ptype = payment.get('payment_type')
        if int(payment.get('intent_version') or 0) == 1:
            from bot.services.money import format_money_minor

            amount = format_money_minor(
                payment.get('payable_amount_minor')
                or payment.get('payable_amount_cents')
                or 0,
                payment.get('base_currency') or 'RUB',
            )
        elif ptype == 'stars':
            stars = payment.get('final_amount_stars') if payment.get('final_amount_stars') is not None else payment.get('amount_stars') or 0
            amount = f"{stars} ⭐"
        elif ptype == 'crypto':
            cents = payment.get('final_amount_cents') if payment.get('final_amount_cents') is not None else payment.get('amount_cents') or 0
            amount_val = cents / 100
            amount_str = f'{amount_val:g}'.replace('.', ',')
            amount = f'${amount_str}'
        elif ptype in ('cards', 'yookassa_qr', 'wata', 'platega', 'cardlink', 'balance', 'promo_free'):
            rub = ((payment.get('final_amount_cents') or 0) / 100) if payment.get('final_amount_cents') is not None else payment.get('price_rub') or 0
            rub_str = f'{rub:g}'.replace('.', ',')
            amount = f'{rub_str} ₽'
        else:
            amount = '—'
        promo = (
            render_ui_text(
                "key.history.promo_suffix",
                promo_code=payment.get('promo_code'),
            )
            if payment.get('promo_code')
            else ""
        )
        lines.append(render_ui_text(
            "key.history.payment",
            date=date,
            payment_type=tariff,
            amount=f"{amount}{promo}",
        ))
    return '\n'.join(lines)
