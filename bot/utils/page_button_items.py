"""Data-only item builders for repeatable button templates stored in pages."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bot.services.money import format_money_minor
from bot.utils.user_ui_texts import render_ui_text


def build_tariff_button_items(
    tariffs: Iterable[Mapping[str, Any]],
    purpose: str,
    *,
    key_id: int | None = None,
    user_id: int | None = None,
    action_context_token: str | None = None,
) -> list[dict[str, Any]]:
    """Return tariff data/actions; the visible label remains page-owned."""
    items: list[dict[str, Any]] = []
    for tariff in tariffs:
        price_minor = int(tariff.get('price_minor') or 0)
        if price_minor <= 0:
            continue
        tariff_id = int(tariff['id'])
        currency = str(tariff.get('base_currency') or 'RUB')
        price_text = format_money_minor(price_minor, currency)
        if user_id:
            from bot.services.payment_pricing import preview_tariff_price

            preview = preview_tariff_price(
                user_id=user_id, tariff=tariff, purpose=purpose, key_id=key_id,
            )
            discounted_minor = int(preview['payable_amount_minor']) if preview['ok'] else price_minor
            if discounted_minor != price_minor:
                price_text = (
                    f"{price_text} → "
                    f"{format_money_minor(discounted_minor, currency)}"
                )
        callback_data = (
            f"payment_intent_tariff:{purpose}:{tariff_id}:{int(key_id or 0)}"
        )
        if action_context_token is not None:
            callback_data = f'{callback_data}:{action_context_token}'
        items.append({
            'item_id': str(tariff_id),
            'callback_data': callback_data,
            'data': {
                'item_name': str(tariff.get('name') or tariff_id),
                'item_price': price_text,
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
            'item_id': str(int(server['id'])),
            'callback_data': f"{callback_prefix}:{int(server['id'])}",
            'data': {'item_name': str(server.get('name') or server['id'])},
        }
        for server in servers
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


def build_subscription_host_button_items(
    hosts: Iterable[Mapping[str, Any]],
    *,
    component_key_id: int,
) -> list[dict[str, Any]]:
    """Return eligible host-key actions while the page owns their labels."""
    component_id = int(component_key_id)
    items: list[dict[str, Any]] = []
    for host in hosts:
        raw_host_id = host.get('id', host.get('key_id'))
        if raw_host_id is None:
            continue
        host_id = int(raw_host_id)
        display_name = host.get('display_name') or host.get('custom_name')
        if not display_name:
            identity_parts = [
                str(value)
                for value in (
                    host.get('tariff_name'),
                    host.get('server_name'),
                    f'#{host_id}',
                )
                if value
            ]
            display_name = ' · '.join(identity_parts)
        items.append({
            'callback_data': (
                f'subscription_host_select:{component_id}:{host_id}'
            ),
            'data': {'item_name': str(display_name)},
        })
    return items


__all__ = [
    'build_key_button_items',
    'build_server_button_items',
    'build_subscription_host_button_items',
    'build_tariff_button_items',
]
