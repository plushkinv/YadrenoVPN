"""Assembling HTML blocks for editable key pages."""
from __future__ import annotations

from datetime import tzinfo
from typing import Any, Iterable, Mapping

from bot.utils.datetime_format import format_date_for_display, get_remaining_minutes
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
    display_tz: tzinfo | None = None,
) -> dict[str, dict[str, Any]]:
    """Builds the allowlisted display context for ``%key(field=...)%``."""
    display_name = key.get('display_name') or f"#{key.get('id', '')}"
    server = key.get('server_name') or '—'
    expires = (
        render_ui_text('format.duration_unlimited')
        if key.get('expires_at') is None
        else format_date_for_display(key.get('expires_at'), display_tz=display_tz)
    )
    tariff = (
        render_ui_text('key.tariff.custom')
        if key.get('tariff_system_type') == 'admin_custom'
        else key.get('tariff_name') or '—'
    )
    device_limit = key.get('max_ips_override')
    if device_limit is None:
        device_limit = key.get('tariff_max_ips')
    if device_limit is None:
        device_limit = key.get('max_ips')
    if device_limit is None:
        device_limit = '—'

    days_left: int | str = ''
    time_left = ''
    remaining_minutes = get_remaining_minutes(key.get('expires_at'))
    if remaining_minutes is not None:
        days, remainder = divmod(remaining_minutes, 24 * 60)
        hours, minutes = divmod(remainder, 60)
        # Expiry notifications retain the whole-day value from their selection.
        days_left = key.get('days_left', days)
        time_left = render_ui_text('format.time_left', days=days, hours=hours, minutes=minutes)
    elif 'expires_at' in key and key['expires_at'] is None:
        time_left = render_ui_text('format.duration_unlimited')

    return {
        KEY_FIELDS_CONTEXT_KEY: {
            'id': key.get('id', ''),
            'name': display_name,
            'status': status if status is not None else _default_key_status(key),
            'traffic': traffic if traffic is not None else _default_key_traffic(key),
            'expires_at': expires,
            'server': server,
            'tariff': tariff,
            'device_limit': device_limit,
            'days_left': days_left,
            'time_left': time_left,
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
        from bot.services.money import format_money_minor

        amount = format_money_minor(
            payment.get('payable_amount_minor') or 0,
            payment.get('base_currency') or 'RUB',
        )
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
