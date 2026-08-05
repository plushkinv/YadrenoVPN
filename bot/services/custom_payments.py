"""Core flow for custom payment providers extensions."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bot.utils.payment_provider_registry import (
    check_payment,
    get_payment_provider,
    handle_payment_webhook,
)
from database.requests import (
    cancel_pending_order,
    find_order_by_order_id,
    find_payment_provider_order_by_external_id,
    get_payment_auto_check,
    get_payment_provider_order,
    update_payment_auto_check,
    update_payment_provider_order_status,
)


async def check_custom_payment_order(provider_id: str, order: Mapping[str, Any]) -> dict[str, Any]:
    """Checks the external status of a custom payment and updates the provider-order."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')

    provider_order = get_payment_provider_order(str(order.get('order_id') or ''))
    if not provider_order or provider_order.get('provider_id') != provider.provider_id:
        raise ValueError('payment provider order не найден')

    result = await check_payment(
        provider.provider_id,
        {
            'provider_id': provider.provider_id,
            'payment_type': provider.payment_type,
            'order': dict(order),
            'provider_order': dict(provider_order),
            'order_id': order.get('order_id'),
            'provider_payment_id': provider_order.get('provider_payment_id'),
            'payment_url': provider_order.get('payment_url'),
            'amount_cents': order.get('final_amount_cents') if order.get('final_amount_cents') is not None else order.get('amount_cents'),
            'currency': order.get('charge_currency') or provider.currency,
            'purpose': order.get('purpose') or ('key_renewal' if order.get('vpn_key_id') else 'key_purchase'),
            'base_currency': order.get('base_currency') or 'RUB',
            'nominal_amount_minor': order.get('nominal_amount_minor') or order.get('nominal_amount_cents') or 0,
            'payable_amount_minor': order.get('payable_amount_minor') or order.get('payable_amount_cents') or order.get('final_amount_cents') or 0,
            'nominal_amount_cents': order.get('nominal_amount_minor') or order.get('nominal_amount_cents') or 0,
            'payable_amount_cents': order.get('payable_amount_minor') or order.get('payable_amount_cents') or order.get('final_amount_cents') or 0,
            'charge_amount': order.get('charge_amount'),
            'charge_currency': order.get('charge_currency') or provider.currency,
            'description': order.get('description') or '',
            'rate_snapshot': order.get('rate_snapshot') or {},
        },
    )
    update_payment_provider_order_status(
        str(order.get('order_id') or ''),
        result['status'],
        provider_payment_id=result.get('provider_payment_id'),
        payment_url=result.get('payment_url'),
        metadata=result.get('metadata'),
    )
    return result


async def complete_custom_payment_order(
    order_id: str,
    *,
    bot: Any = None,
    notify_user: bool = False,
) -> dict[str, Any]:
    """Completes custom payment through the shared confirmed-payment service."""
    from bot.services.payment_completion import complete_confirmed_payment

    result = await complete_confirmed_payment(
        order_id,
        bot=bot,
        background=True,
        notify_user=notify_user,
    )
    return result.as_dict()


async def auto_check_custom_payment_orders(
    *,
    bot: Any = None,
    limit: int = 50,
) -> dict[str, int]:
    """Compatibility wrapper for the shared bounded payment polling queue."""
    from bot.services.payment_auto_check import auto_check_payment_orders

    return await auto_check_payment_orders(bot=bot, limit=min(int(limit), 10))


async def process_custom_payment_webhook(
    provider_id: str,
    request_context: Mapping[str, Any],
    *,
    bot: Any = None,
) -> dict[str, Any]:
    """Processes a custom payment provider's webhook through a declarative contract."""
    try:
        provider = get_payment_provider(provider_id)
    except ValueError:
        provider = None
    if provider is None:
        return {'ok': False, 'reason': 'provider_not_found', 'http_status': 404}

    try:
        webhook_result = await handle_payment_webhook(provider.provider_id, request_context)
    except ValueError as e:
        return {'ok': False, 'reason': str(e), 'http_status': 400}

    if webhook_result.get('ignored'):
        return {
            'ok': True,
            'ignored': True,
            'reason': webhook_result.get('reason'),
            'status': 'ignored',
        }

    provider_order = _find_provider_order_for_webhook(provider.provider_id, webhook_result)
    if not provider_order:
        return {'ok': False, 'reason': 'provider_order_not_found', 'http_status': 404}
    if provider_order.get('provider_id') != provider.provider_id:
        return {'ok': False, 'reason': 'provider_order_mismatch', 'http_status': 404}

    order_id = str(provider_order.get('order_id') or '')
    order = find_order_by_order_id(order_id)
    if not order:
        return {'ok': False, 'reason': 'order_not_found', 'http_status': 404}

    status = str(webhook_result['status'])
    update_payment_provider_order_status(
        order_id,
        status,
        provider_payment_id=webhook_result.get('provider_payment_id'),
        payment_url=webhook_result.get('payment_url'),
        metadata=webhook_result.get('metadata'),
    )

    response: dict[str, Any] = {
        'ok': True,
        'order_id': order_id,
        'provider_id': provider.provider_id,
        'status': status,
        'completed': False,
        'processed_now': False,
    }
    if status == 'succeeded':
        auto_check = get_payment_auto_check(order_id)
        auto_check_state = str((auto_check or {}).get('state') or '')
        if auto_check_state and auto_check_state != 'completed':
            update_payment_auto_check(
                order_id,
                state='provider_succeeded',
                next_delay_seconds=0,
                expected_state=auto_check_state,
            )
        completed = await complete_custom_payment_order(order_id, bot=bot, notify_user=True)
        response['completed'] = bool(completed.get('ok'))
        response['processed_now'] = bool(completed.get('processed_now'))
    elif status == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
    return response


def _find_provider_order_for_webhook(
    provider_id: str,
    webhook_result: Mapping[str, Any],
) -> dict[str, Any] | None:
    order_id = webhook_result.get('order_id')
    if order_id:
        provider_order = get_payment_provider_order(str(order_id))
        if provider_order:
            return provider_order

    provider_payment_id = webhook_result.get('provider_payment_id')
    if provider_payment_id:
        return find_payment_provider_order_by_external_id(provider_id, str(provider_payment_id))
    return None


__all__ = [
    'auto_check_custom_payment_orders',
    'check_custom_payment_order',
    'complete_custom_payment_order',
    'process_custom_payment_webhook',
]
