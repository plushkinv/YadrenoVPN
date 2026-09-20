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
    if int(order.get('intent_version') or 0) != 1:
        raise ValueError('custom payment checks require Payment Intent v1')
    purpose = str(order.get('purpose') or '')
    if purpose not in {'key_purchase', 'key_renewal', 'balance_topup'}:
        raise ValueError('custom payment purpose is invalid')
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')

    provider_order = get_payment_provider_order(str(order.get('order_id') or ''))
    if not provider_order or provider_order.get('provider_id') != provider.provider_id:
        raise ValueError('payment provider order не найден')

    charge_currency = str(order.get('charge_currency') or provider.currency)
    from bot.services.money import parse_major_to_minor

    try:
        provider_amount_minor = parse_major_to_minor(
            order.get('charge_amount') or '0',
            charge_currency,
        )
    except (TypeError, ValueError):
        provider_amount_minor = 0

    result = await check_payment(
        provider.provider_id,
        {
            'provider_id': provider.provider_id,
            'payment_type': provider.payment_type,
            'order': dict(order),
            'provider_order': dict(provider_order),
            'order_id': order.get('order_id'),
            'user_id': order.get('user_id'),
            'provider_payment_id': provider_order.get('provider_payment_id'),
            'payment_url': provider_order.get('payment_url'),
            'amount_cents': provider_amount_minor,
            'currency': charge_currency,
            'purpose': purpose,
            'base_currency': order.get('base_currency') or 'RUB',
            'nominal_amount_minor': order.get('nominal_amount_minor') or 0,
            'payable_amount_minor': order.get('payable_amount_minor') or 0,
            'nominal_amount_cents': order.get('nominal_amount_minor') or 0,
            'payable_amount_cents': order.get('payable_amount_minor') or 0,
            'charge_amount': order.get('charge_amount'),
            'charge_currency': charge_currency,
            'description': order.get('description') or '',
            'rate_snapshot': order.get('rate_snapshot') or {},
        },
    )
    return _save_custom_payment_status(str(order.get('order_id') or ''), result)


def _save_custom_payment_status(order_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """A late pending/canceled check must not erase a committed confirmation."""
    current = get_payment_provider_order(order_id)
    if (current or {}).get('status') == 'succeeded' and result['status'] != 'succeeded':
        return {**result, 'status': 'succeeded'}
    update_payment_provider_order_status(
        order_id, result['status'],
        provider_payment_id=result.get('provider_payment_id'),
        payment_url=result.get('payment_url'),
        metadata=result.get('metadata'),
    )
    return dict(result)


def _wake_payment_completion(order_id: str) -> None:
    """Wake an existing polling row; confirmation recovery also works without one."""
    auto_check = get_payment_auto_check(order_id)
    auto_check_state = str((auto_check or {}).get('state') or '')
    if auto_check_state and auto_check_state != 'completed':
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
            expected_state=auto_check_state,
        )


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


async def process_custom_payment_webhook(
    provider_id: str,
    request_context: Mapping[str, Any],
    *,
    bot: Any = None,
) -> dict[str, Any]:
    """Processes a custom payment provider's webhook through a declarative contract."""
    from runtime.readiness import require_active

    require_active()
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
    if not order or int(order.get('intent_version') or 0) != 1:
        return {'ok': False, 'reason': 'order_not_found', 'http_status': 404}

    webhook_result = _save_custom_payment_status(order_id, webhook_result)
    status = str(webhook_result['status'])

    response: dict[str, Any] = {
        'ok': True,
        'order_id': order_id,
        'provider_id': provider.provider_id,
        'status': status,
        'completed': False,
        'processed_now': False,
    }
    if status == 'succeeded':
        _wake_payment_completion(order_id)
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
    'check_custom_payment_order',
    'complete_custom_payment_order',
    'process_custom_payment_webhook',
]
