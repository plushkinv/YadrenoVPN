"""Explicit extension wakes of the existing custom-provider payment pipeline."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _result(order_id: str, *, reason: str, **values: Any) -> dict[str, Any]:
    return {
        'ok': False,
        'order_id': order_id,
        'provider_status': None,
        'payment_completed': False,
        'processed_now': False,
        'key_setup_status': None,
        'retryable': False,
        'reason': reason,
        **values,
    }


async def recheck_extension_payment(
    *, extension_id: str, order_id: str, bot: Any = None,
) -> dict[str, Any]:
    """Resolve authority from the live provider and terms from the persisted order."""
    from runtime.readiness import require_active
    from bot.services.payment_api import is_retryable_payment_error
    from bot.services.payment_completion import (
        complete_confirmed_payment, is_payment_completion_active,
    )
    from bot.services.payment_fulfillment import is_payment_fulfillment_running
    from bot.services.payment_intents import load_payment_intent
    from bot.services.payment_provider_adapters import check_provider_invoice
    from bot.services.custom_payments import _wake_payment_completion
    from bot.utils.payment_provider_registry import get_payment_provider
    from database.requests import (
        cancel_pending_order, find_order_by_order_id, get_payment_provider_order,
    )

    require_active()
    order = find_order_by_order_id(order_id)
    if not order:
        return _result(order_id, reason='order_not_found')
    if int(order.get('intent_version') or 0) != 1:
        return _result(order_id, reason='payment_intent_required')
    provider_order = get_payment_provider_order(order_id)
    if not provider_order:
        return _result(order_id, reason='provider_order_not_found')
    try:
        provider = get_payment_provider(provider_order.get('provider_id'))
    except ValueError:
        return _result(order_id, reason='provider_not_owned')
    if provider is None:
        return _result(order_id, reason='provider_unavailable')
    if provider._extension_id != extension_id:
        return _result(order_id, reason='provider_not_owned')
    if order.get('payment_type') != provider.payment_type:
        return _result(order_id, reason='provider_mismatch')

    status = str(provider_order.get('status') or 'pending')
    paid = order.get('status') == 'paid'
    if is_payment_completion_active(order_id) or is_payment_fulfillment_running(order_id):
        return _result(
            order_id, reason='payment_processing', provider_status=status,
            payment_completed=paid, retryable=True,
        )
    if order.get('status') not in {'pending', 'paid'}:
        return _result(order_id, reason='payment_canceled', ok=True, provider_status=status)

    intent = load_payment_intent(order_id)
    if intent is None or intent.purpose not in provider.supported_purposes:
        return _result(order_id, reason='purpose_unavailable')
    if paid:
        status = 'succeeded'
    elif status != 'succeeded':
        try:
            status = await check_provider_invoice(intent)
        except Exception as error:
            logger.warning(
                'Extension payment check failed extension=%s order=%s type=%s',
                extension_id, order_id, type(error).__name__,
            )
            current = get_payment_provider_order(order_id) or {}
            if current.get('status') == 'succeeded':
                status = 'succeeded'
            else:
                return _result(
                    order_id, reason='provider_check_failed', provider_status=status,
                    retryable=is_retryable_payment_error(error),
                )

    if status == 'pending':
        return _result(order_id, reason='payment_pending', ok=True, provider_status=status)
    if status == 'canceled':
        cancel_pending_order(order_id)
        return _result(order_id, reason='payment_canceled', ok=True, provider_status=status)

    _wake_payment_completion(order_id)
    try:
        completed = await complete_confirmed_payment(
            order_id, bot=bot, background=True, notify_user=bot is not None,
        )
    except Exception as error:
        logger.warning(
            'Extension payment completion failed extension=%s order=%s type=%s',
            extension_id, order_id, type(error).__name__,
        )
        current = find_order_by_order_id(order_id) or {}
        return _result(
            order_id, reason='completion_pending', provider_status='succeeded',
            payment_completed=current.get('status') == 'paid', retryable=True,
        )
    reason = (
        ('payment_completed' if completed.processed_now else 'already_completed')
        if completed.ok else
        ('completion_pending' if completed.retryable else 'completion_failed')
    )
    return _result(
        order_id, reason=reason, ok=completed.ok, provider_status='succeeded',
        payment_completed=completed.payment_completed, processed_now=completed.processed_now,
        key_setup_status=(completed.key_setup_status.value if completed.key_setup_status else None),
        retryable=completed.retryable,
    )
