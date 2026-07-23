"""Closed, retryable fulfillment dispatcher for provider-confirmed intents."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from database.requests import (
    begin_payment_fulfillment,
    claim_payment_effect,
    complete_payment_effect,
    complete_payment_fulfillment,
    fail_payment_effect,
    fail_payment_fulfillment,
    find_order_by_order_id,
    fulfill_balance_topup_once,
    fulfill_key_purchase_once,
    fulfill_key_renewal_once,
    get_tariff_by_id,
    is_payment_effect_completed,
    mark_payment_provider_confirmed,
    prepare_failed_payment_fulfillment_retry,
    recover_interrupted_payment_fulfillment,
)

from bot.services.payment_intents import (
    PURPOSE_BALANCE_TOPUP,
    PURPOSE_KEY_PURCHASE,
    PURPOSE_KEY_RENEWAL,
    PaymentResult,
    load_payment_intent,
)

logger = logging.getLogger(__name__)
_fulfillment_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def fulfill_payment_intent(
    order_id: str,
    *,
    bot: Any = None,
    process_referrals: bool = True,
) -> PaymentResult:
    """Confirms settlement and applies the trusted purpose exactly once."""
    async with _fulfillment_locks[str(order_id)]:
        return await _fulfill_payment_intent_unlocked(
            order_id,
            bot=bot,
            process_referrals=process_referrals,
        )


async def _fulfill_payment_intent_unlocked(
    order_id: str,
    *,
    bot: Any = None,
    process_referrals: bool = True,
) -> PaymentResult:
    """Runs one locally serialized fulfillment attempt."""
    intent = load_payment_intent(order_id)
    if intent is None:
        raise ValueError('Payment intent does not exist')
    if intent.status == 'paid' and intent.fulfillment_status == 'completed':
        return _completed_result(intent, already_completed=True)
    if intent.status != 'pending':
        return PaymentResult(
            order_id=intent.order_id,
            purpose=intent.purpose,
            completed=False,
            message='payment_not_pending',
        )

    if intent.fulfillment_status == 'processing':
        recover_interrupted_payment_fulfillment(intent.order_id)
        intent = load_payment_intent(intent.order_id) or intent
    if intent.fulfillment_status == 'failed':
        prepare_failed_payment_fulfillment_retry(intent.order_id)
    mark_payment_provider_confirmed(intent.order_id)
    if not begin_payment_fulfillment(intent.order_id):
        current = load_payment_intent(intent.order_id)
        if current and current.status == 'paid' and current.fulfillment_status == 'completed':
            return _completed_result(current, already_completed=True)
        return PaymentResult(
            order_id=intent.order_id,
            purpose=intent.purpose,
            completed=False,
            message='payment_processing',
        )

    try:
        initial_order = find_order_by_order_id(intent.order_id)
        if not initial_order:
            raise RuntimeError('Payment order disappeared during fulfillment')
        await _debit_internal_balance_once(initial_order)
        purpose_result = await _apply_purpose(intent)
        if not purpose_result.get('ok'):
            raise RuntimeError(str(purpose_result.get('reason') or 'purpose fulfillment failed'))

        order = find_order_by_order_id(intent.order_id)
        if not order:
            raise RuntimeError('Payment order disappeared during fulfillment')
        await _apply_promotion_once(order)

        paid_amount = int(intent.payable_amount_minor or 0)
        if paid_amount > 0:
            if process_referrals:
                await _apply_referrals_once(order, bot=bot)
            await _issue_coupon_once(order)

        if not complete_payment_fulfillment(intent.order_id):
            current = load_payment_intent(intent.order_id)
            if not current or current.fulfillment_status != 'completed':
                raise RuntimeError('Payment fulfillment could not be finalized')

        completed = load_payment_intent(intent.order_id)
        if completed is None:
            raise RuntimeError('Completed payment intent cannot be loaded')
        await _notify_admins_once(intent.order_id, bot=bot)
        result = _completed_result(completed, already_completed=False)
        return PaymentResult(
            **{
                **result.__dict__,
                'vpn_key_id': int(purpose_result.get('key_id') or 0) or result.vpn_key_id,
                'credited_amount_minor': int(
                    purpose_result.get('credited_amount_minor')
                    or purpose_result.get('credited_amount_cents')
                    or 0
                ),
            }
        )
    except Exception as error:
        fail_payment_fulfillment(intent.order_id, str(error))
        logger.exception('Payment fulfillment failed order=%s', intent.order_id)
        return PaymentResult(
            order_id=intent.order_id,
            purpose=intent.purpose,
            completed=False,
            message='payment_retry_scheduled',
        )


async def _apply_purpose(intent) -> dict[str, Any]:
    payload = dict(intent.purpose_data)
    if intent.purpose == PURPOSE_BALANCE_TOPUP:
        return fulfill_balance_topup_once(
            intent.order_id,
            user_id=intent.user_id,
            amount_minor=intent.nominal_amount_minor,
            currency=intent.base_currency,
        )

    tariff_id = int(payload.get('tariff_id') or intent.tariff_id or 0)
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        return {'ok': False, 'reason': 'tariff_not_found'}
    days = int(tariff.get('duration_days') or 0)
    traffic_limit = int(tariff.get('traffic_limit_gb') or 0) * (1024 ** 3)

    if intent.purpose == PURPOSE_KEY_PURCHASE:
        result = fulfill_key_purchase_once(
            intent.order_id,
            user_id=intent.user_id,
            tariff_id=tariff_id,
            days=days,
            traffic_limit_bytes=traffic_limit,
        )
        if result.get('ok') and not result.get('already_applied'):
            await _emit_key_event(
                'key_created',
                intent,
                result,
                {'tariff_id': tariff_id, 'days': days, 'traffic_limit': traffic_limit},
            )
        return result

    if intent.purpose == PURPOSE_KEY_RENEWAL:
        key_id = int(payload.get('key_id') or intent.vpn_key_id or 0)
        result = fulfill_key_renewal_once(
            intent.order_id,
            user_id=intent.user_id,
            key_id=key_id,
            tariff_id=tariff_id,
            days=days,
            traffic_limit_bytes=traffic_limit,
        )
        if result.get('ok') and not result.get('already_applied'):
            await _sync_renewed_key(key_id)
            await _emit_key_event(
                'key_renewed',
                intent,
                result,
                {'tariff_id': tariff_id, 'days': days, 'reset_traffic': False},
            )
        return result

    return {'ok': False, 'reason': 'unsupported_purpose'}


async def _apply_promotion_once(order: dict[str, Any]) -> None:
    await _run_effect(
        order['order_id'],
        'promotion',
        lambda: _apply_promotion(order),
    )


async def _debit_internal_balance_once(order: dict[str, Any]) -> None:
    amount = int(order.get('balance_deduct_cents') or 0)
    if amount <= 0:
        return

    async def apply() -> dict[str, Any]:
        from bot.services.balance import debit_user_balance
        from database.requests import has_balance_operation_reference

        reference = str(order['order_id'])
        if has_balance_operation_reference(
            user_id=int(order['user_id']),
            operation_type='debit',
            source='payment_balance',
            reference_type='payment_order',
            reference_id=reference,
        ):
            return {'amount_cents': amount, 'already_applied': True}
        result = await debit_user_balance(
            int(order['user_id']),
            amount,
            source='payment_balance',
            reason='Списание баланса при оплате',
            reference_type='payment_order',
            reference_id=reference,
            metadata={'payment_type': order.get('payment_type')},
        )
        if not result.get('ok'):
            raise RuntimeError(f"Balance debit failed: {result.get('status')}")
        return {'amount_cents': amount, 'operation_id': result.get('operation_id')}

    await _run_effect(order['order_id'], 'balance_debit', apply)


async def _apply_promotion(order: dict[str, Any]) -> dict[str, Any]:
    from bot.services.promotions import apply_order_promotion_after_payment

    redemption = apply_order_promotion_after_payment(order)
    return {'redemption_id': (redemption or {}).get('id')}


async def _apply_referrals_once(order: dict[str, Any], *, bot: Any) -> None:
    async def apply() -> dict[str, Any]:
        from bot.services.billing import process_referral_reward

        events = await process_referral_reward(
            int(order['user_id']),
            int(order.get('period_days') or order.get('duration_days') or 0),
            int(order.get('payable_amount_cents') or 0),
            str(order.get('payment_type') or ''),
            bot=bot,
            order=order,
        )
        return {'events': events}

    await _run_effect(order['order_id'], 'referrals', apply)


async def _issue_coupon_once(order: dict[str, Any]) -> None:
    async def apply() -> dict[str, Any]:
        from bot.services.promotions import maybe_issue_auto_coupon_after_payment_async

        coupon = await maybe_issue_auto_coupon_after_payment_async(order)
        return {'coupon': coupon}

    await _run_effect(order['order_id'], 'coupon', apply)


async def _notify_admins_once(order_id: str, *, bot: Any) -> None:
    if bot is None:
        return

    async def apply() -> dict[str, Any]:
        from bot.services.notifications import notify_admins_payment

        order = find_order_by_order_id(order_id)
        if order:
            await notify_admins_payment(bot, order)
        return {'notified': bool(order)}

    try:
        await _run_effect(order_id, 'admin_notification', apply)
    except Exception as error:
        logger.warning('Admin payment notification failed order=%s: %s', order_id, error)


async def _run_effect(order_id: str, effect_name: str, operation) -> None:
    if is_payment_effect_completed(order_id, effect_name):
        return
    if not claim_payment_effect(order_id, effect_name):
        if is_payment_effect_completed(order_id, effect_name):
            return
        raise RuntimeError(f'Payment effect is already running: {effect_name}')
    try:
        metadata = await operation()
        if not complete_payment_effect(order_id, effect_name, metadata or {}):
            raise RuntimeError(f'Payment effect completion failed: {effect_name}')
    except Exception as error:
        fail_payment_effect(order_id, effect_name, str(error))
        raise


async def _sync_renewed_key(key_id: int) -> None:
    try:
        from bot.services.vpn_api import sync_key_to_panel_state

        await sync_key_to_panel_state(key_id, reset_traffic=False)
    except Exception as error:
        logger.warning('Renewed key panel sync deferred key=%s: %s', key_id, error)


async def _emit_key_event(
    event_name: str,
    intent,
    result: dict[str, Any],
    context: dict[str, Any],
) -> None:
    try:
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

        await emit_key_lifecycle_event_safe(
            event_name,
            {
                'key_id': int(result.get('key_id') or 0),
                'user_id': intent.user_id,
                'order_id': intent.order_id,
                'payment_type': intent.payment_type,
                'source': 'payment_intent',
                **context,
            },
        )
    except Exception as error:
        logger.warning('Payment key lifecycle event failed order=%s: %s', intent.order_id, error)


def _completed_result(intent, *, already_completed: bool) -> PaymentResult:
    if intent.purpose == PURPOSE_BALANCE_TOPUP:
        message = 'balance_topup_completed'
    elif intent.purpose == PURPOSE_KEY_RENEWAL:
        message = 'key_renewed'
    else:
        message = 'key_purchase_completed'
    return PaymentResult(
        order_id=intent.order_id,
        purpose=intent.purpose,
        completed=True,
        already_completed=already_completed,
        target=intent.navigation.success_target,
        vpn_key_id=intent.vpn_key_id,
        credited_amount_minor=(
            intent.nominal_amount_minor
            if intent.purpose == PURPOSE_BALANCE_TOPUP
            else 0
        ),
        message=message,
    )


__all__ = ['fulfill_payment_intent']
