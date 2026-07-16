"""Core flow for custom payment providers extensions."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from bot.utils.payment_provider_registry import (
    create_payment,
    check_payment,
    get_payment_provider,
    handle_payment_webhook,
    is_payment_provider_enabled,
)
from database.requests import (
    cancel_pending_order,
    create_pending_order,
    find_order_by_order_id,
    find_payment_provider_order_by_external_id,
    get_payment_provider_order,
    save_payment_provider_order,
    schedule_payment_auto_check,
    update_payment_auto_check,
    update_payment_provider_order_status,
)
from bot.services.promotions import prepare_order_pricing

logger = logging.getLogger(__name__)


async def create_custom_payment_order(
    provider_id: str,
    *,
    user_id: int,
    telegram_id: int,
    tariff: Mapping[str, Any],
    action: str,
    vpn_key_id: int | None = None,
    key: Mapping[str, Any] | None = None,
    bot_username: str | None = None,
) -> dict[str, Any]:
    """Creates a core order, calculates quote and calls create_payment provider."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')
    if not is_payment_provider_enabled(provider.provider_id, {'user_id': user_id, 'telegram_id': telegram_id}):
        return {'ok': False, 'reason': 'Способ оплаты временно недоступен.'}

    (_, order_id) = create_pending_order(
        user_id=user_id,
        tariff_id=int(tariff['id']),
        payment_type=provider.payment_type,
        vpn_key_id=vpn_key_id,
    )
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=dict(tariff),
        payment_type=provider.payment_type,
        action=action,
    )
    if not quote.get('ok'):
        return {'ok': False, 'order_id': order_id, 'quote': quote, 'reason': quote.get('unavailable_reason')}
    if quote.get('is_free'):
        return {'ok': True, 'order_id': order_id, 'quote': quote, 'is_free': True}

    context = {
        'provider_id': provider.provider_id,
        'payment_type': provider.payment_type,
        'order_id': order_id,
        'user_id': user_id,
        'telegram_id': telegram_id,
        'tariff': dict(tariff),
        'action': action,
        'vpn_key_id': vpn_key_id,
        'key': dict(key or {}),
        'amount_cents': int(quote['final_amount']),
        'currency': 'RUB',
        'quote': dict(quote),
        'bot_username': bot_username,
        'description': _payment_description(tariff, action=action, key=key),
    }
    result = await create_payment(provider.provider_id, context)
    saved = save_payment_provider_order(
        order_id=order_id,
        provider_id=provider.provider_id,
        payment_type=provider.payment_type,
        provider_payment_id=result.get('provider_payment_id'),
        payment_url=result.get('payment_url'),
        status=result.get('status') or 'pending',
        metadata=result.get('metadata') or {},
    )
    if saved is False:
        raise RuntimeError('Не удалось сохранить custom provider order')
    if provider.auto_check_interval_seconds:
        try:
            schedule_payment_auto_check(
                order_id,
                provider.provider_id,
                first_delay_seconds=min(
                    1800,
                    max(120, int(provider.auto_check_interval_seconds)),
                ),
            )
        except Exception as error:
            logger.error(
                "Не удалось поставить custom payment в очередь provider=%s order=%s: %s",
                provider.provider_id,
                order_id,
                error,
                exc_info=True,
            )
    return {
        'ok': True,
        'order_id': order_id,
        'provider': provider,
        'provider_order': get_payment_provider_order(order_id),
        'payment_url': result['payment_url'],
        'quote': quote,
        'is_free': False,
    }


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
            'currency': 'RUB',
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
    """Completes custom payment via core billing without UI/FSM."""
    from bot.services.billing import complete_payment_order_background

    return await complete_payment_order_background(
        order_id,
        bot=bot,
        notify_user=notify_user,
    )


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
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )
        completed = await complete_custom_payment_order(order_id, bot=bot, notify_user=True)
        response['completed'] = bool(completed.get('ok'))
        response['processed_now'] = bool(completed.get('processed_now'))
        if completed.get('ok'):
            update_payment_auto_check(order_id, state='completed')
    elif status == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
    return response


def _payment_description(
    tariff: Mapping[str, Any],
    *,
    action: str,
    key: Mapping[str, Any] | None = None,
) -> str:
    tariff_name = str(tariff.get('name') or 'VPN')
    days = int(tariff.get('duration_days') or 0)
    if action == 'renewal' and key:
        key_name = str(key.get('display_name') or 'VPN-ключ')
        return f"Продление ключа «{key_name}»: «{tariff_name}» ({days} дн.)"
    return f"Покупка «{tariff_name}» — {days} дней"


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
    'create_custom_payment_order',
    'process_custom_payment_webhook',
]
