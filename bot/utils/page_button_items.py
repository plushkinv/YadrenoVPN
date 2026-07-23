"""Data-only item builders for repeatable button templates stored in pages."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable, Mapping

from bot.services.money import format_money_minor
from bot.utils.user_ui_texts import render_ui_text


def build_tariff_button_items(
    tariffs: Iterable[Mapping[str, Any]],
    purpose: str,
    *,
    key_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return tariff data/actions; the visible label remains page-owned."""
    items: list[dict[str, Any]] = []
    for tariff in tariffs:
        price_minor = int(
            tariff.get('price_minor')
            or int(float(tariff.get('price_rub') or 0) * 100)
        )
        if price_minor <= 0:
            continue
        tariff_id = int(tariff['id'])
        items.append({
            'callback_data': (
                f"payment_intent_tariff:{purpose}:{tariff_id}:{int(key_id or 0)}"
            ),
            'data': {
                'item_name': str(tariff.get('name') or tariff_id),
                'item_price': format_money_minor(
                    price_minor,
                    str(tariff.get('base_currency') or 'RUB'),
                ),
            },
        })
    return items


def build_provider_tariff_button_items(
    tariffs: Iterable[Mapping[str, Any]],
    payment_type: str,
    callback_factory: Callable[[int], str],
    *,
    minimum_amount: int = 1,
) -> list[dict[str, Any]]:
    """Return page-owned tariff items for compatibility provider callbacks."""
    from bot.services.exchange_rate import get_payment_rate_snapshot, provider_amount_from_base_minor

    snapshot = get_payment_rate_snapshot()
    items: list[dict[str, Any]] = []
    for tariff in tariffs:
        base_minor = int(
            tariff.get('price_minor')
            or int(float(tariff.get('price_rub') or 0) * 100)
        )
        amount, currency = provider_amount_from_base_minor(
            base_minor,
            payment_type,
            snapshot,
        )
        if amount < int(minimum_amount):
            continue
        tariff_id = int(tariff['id'])
        items.append({
            'callback_data': callback_factory(tariff_id),
            'data': {
                'item_name': str(tariff.get('name') or tariff_id),
                'item_price': format_money_minor(amount, currency),
            },
        })
    return items


def build_server_button_items(
    servers: Iterable[Mapping[str, Any]],
    *,
    callback_prefix: str,
) -> list[dict[str, Any]]:
    """Return server business names and technical callbacks."""
    return [
        {
            'callback_data': f"{callback_prefix}:{int(server['id'])}",
            'data': {'item_name': str(server.get('name') or server['id'])},
        }
        for server in servers
    ]


def build_protocol_button_items(
    inbounds: Iterable[Mapping[str, Any]],
    *,
    callback_prefix: str,
) -> list[dict[str, Any]]:
    """Return protocol business data and technical callbacks."""
    return [
        {
            'callback_data': f"{callback_prefix}:{int(inbound['id'])}",
            'data': {
                'item_name': str(inbound.get('remark') or 'VPN'),
                'item_protocol': str(inbound.get('protocol') or 'vless').upper(),
            },
        }
        for inbound in inbounds
    ]


def build_key_button_items(keys: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return key data/actions with statuses from the cached UI catalog."""
    items: list[dict[str, Any]] = []
    for key in keys:
        traffic_limit = int(key.get('traffic_limit') or 0)
        traffic_used = int(key.get('traffic_used') or 0)
        if traffic_limit > 0 and traffic_used >= traffic_limit:
            status_key = 'key.status.traffic_exhausted'
            status_indicator = '🔴'
        elif bool(key.get('is_active')):
            status_key = 'key.status.active'
            status_indicator = '🟢'
        else:
            status_key = 'key.status.expired'
            status_indicator = '🔴'
        key_id = int(key['id'])
        items.append({
            'callback_data': f'key:{key_id}',
            'data': {
                'item_name': str(key.get('display_name') or f'#{key_id}'),
                'item_status': render_ui_text(status_key),
                'item_status_indicator': status_indicator,
            },
        })
    return items


__all__ = [
    'build_key_button_items',
    'build_protocol_button_items',
    'build_provider_tariff_button_items',
    'build_server_button_items',
    'build_tariff_button_items',
]
