"""Provider-aware cancellation for unconfirmed Payment Intent v1 orders."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from database.requests import (
    get_payment_provider_order,
    list_pending_payment_provider_orders,
    set_setting,
    update_payment_provider_order_status,
)

from bot.services.payment_intents import (
    PaymentIntent,
    cancel_payment_intent,
    load_payment_intent,
    restart_payment_intent_for_method_change,
)


logger = logging.getLogger(__name__)


class CryptoBotCancellationUncertain(RuntimeError):
    """Raised when a local cancellation cannot safely follow the API state."""


class CryptoBotPaymentConfirmed(RuntimeError):
    """Raised when cancellation discovers a payment that must be fulfilled."""


@dataclass(frozen=True)
class SafePaymentCancellation:
    """Result returned to interactive cancel and method-change handlers."""

    outcome: str
    intent: PaymentIntent | None
    replacement: PaymentIntent | None = None


@dataclass(frozen=True)
class CryptoBotTokenReplacementReadiness:
    """Reconciliation summary before replacing the provider credential."""

    active: int = 0
    uncertain: int = 0
    paid: int = 0
    canceled: int = 0

    @property
    def ready(self) -> bool:
        return self.active == 0 and self.uncertain == 0


def _cryptobot_snapshot(
    provider_order: dict,
) -> tuple[str, Decimal, str] | None:
    external_id = str(provider_order.get("provider_payment_id") or "").strip()
    currency = str(provider_order.get("charge_currency") or "").strip().upper()
    try:
        amount = Decimal(str(provider_order.get("charge_amount")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (
        not external_id
        or currency not in {"RUB", "USD"}
        or not amount.is_finite()
        or amount <= 0
    ):
        return None
    return external_id, amount, currency


async def _prepare_external_cancellation(intent: PaymentIntent) -> str:
    provider_order = get_payment_provider_order(intent.order_id)
    if not provider_order:
        return "local"
    status = str(provider_order.get("status") or "pending").casefold()
    if status == "succeeded" or intent.provider_confirmed_at is not None:
        return "succeeded"
    if (provider_order.get('provider_id') == 'wata' and status == 'pending'
            and intent.status == 'pending'
            and (provider_order.get('provider_payment_id') or provider_order.get('payment_url'))):
        return "retained"
    if str(provider_order.get("provider_id") or "") != "cryptobot":
        return "local"
    if status == "canceled":
        return "canceled"

    snapshot = _cryptobot_snapshot(provider_order)
    if snapshot is None:
        return "uncertain"
    external_id, amount, currency = snapshot

    from bot.services.cryptobot import cancel_cryptobot_invoice

    result = await cancel_cryptobot_invoice(
        invoice_id=external_id,
        order_id=intent.order_id,
        amount=amount,
        fiat=currency,
    )
    metadata = dict(provider_order.get("metadata") or {})
    metadata.update(dict(result.metadata))
    if result.outcome == "succeeded":
        update_payment_provider_order_status(
            intent.order_id,
            "succeeded",
            metadata=metadata,
        )
        return "succeeded"
    if result.outcome == "canceled":
        update_payment_provider_order_status(
            intent.order_id,
            "canceled",
            metadata=metadata,
        )
        return "canceled"
    return "uncertain"


async def cancel_payment_intent_safely(
    order_id: str,
    *,
    user_id: int,
) -> SafePaymentCancellation:
    """Cancel locally only after any Crypto Pay invoice is final or deleted."""
    intent = load_payment_intent(str(order_id))
    if intent is None or intent.user_id != int(user_id):
        return SafePaymentCancellation("unavailable", intent)

    provider_order = get_payment_provider_order(intent.order_id)
    is_cryptobot = bool(
        provider_order
        and str(provider_order.get("provider_id") or "") == "cryptobot"
    )
    if is_cryptobot:
        from bot.services.cryptobot import cryptobot_lifecycle_lock

        async with cryptobot_lifecycle_lock():
            return await _cancel_locked(intent)
    return await _cancel_locked(intent)


async def _cancel_locked(intent: PaymentIntent) -> SafePaymentCancellation:
    outcome = await _prepare_external_cancellation(intent)
    if outcome in {"succeeded", "uncertain", "retained"}:
        return SafePaymentCancellation(outcome, intent)
    canceled = cancel_payment_intent(intent.order_id, user_id=intent.user_id)
    return SafePaymentCancellation(
        "canceled" if canceled else "unavailable",
        intent,
    )


async def restart_payment_intent_safely(
    order_id: str,
    *,
    user_id: int,
) -> SafePaymentCancellation:
    """Replace an intent only after its Crypto Pay invoice cannot be paid."""
    intent = load_payment_intent(str(order_id))
    if intent is None or intent.user_id != int(user_id):
        return SafePaymentCancellation("unavailable", intent)

    provider_order = get_payment_provider_order(intent.order_id)
    is_cryptobot = bool(
        provider_order
        and str(provider_order.get("provider_id") or "") == "cryptobot"
    )
    if is_cryptobot:
        from bot.services.cryptobot import cryptobot_lifecycle_lock

        async with cryptobot_lifecycle_lock():
            return await _restart_locked(intent)
    return await _restart_locked(intent)


async def _restart_locked(intent: PaymentIntent) -> SafePaymentCancellation:
    outcome = await _prepare_external_cancellation(intent)
    if outcome in {"succeeded", "uncertain"}:
        return SafePaymentCancellation(outcome, intent)
    replacement = restart_payment_intent_for_method_change(
        intent.order_id,
        user_id=intent.user_id,
    )
    if replacement is None:
        return SafePaymentCancellation("unavailable", intent)
    return SafePaymentCancellation("restarted", intent, replacement)


async def cancel_pending_cryptobot_for_base_switch() -> int:
    """Close every active Crypto Pay invoice before a base-currency switch."""
    canceled = 0
    for provider_order in list_pending_payment_provider_orders("cryptobot"):
        order_id = str(provider_order.get("order_id") or "")
        intent = load_payment_intent(order_id)
        if intent is None:
            raise CryptoBotCancellationUncertain(
                f"Crypto Pay intent is missing for order {order_id}"
            )
        outcome = await _prepare_external_cancellation(intent)
        if outcome == "succeeded":
            from bot.services.payment_completion import complete_confirmed_payment

            completed = await complete_confirmed_payment(
                order_id,
                bot=None,
                notify_user=False,
                show_primary_result=False,
            )
            if not completed.ok or not completed.payment_completed:
                raise CryptoBotPaymentConfirmed(
                    f"Crypto Pay order {order_id} was paid but could not be fulfilled"
                )
            continue
        if outcome == "uncertain":
            raise CryptoBotCancellationUncertain(
                f"Crypto Pay order {order_id} could not be canceled safely"
            )
        if not cancel_payment_intent(order_id, user_id=intent.user_id):
            raise CryptoBotCancellationUncertain(
                f"Crypto Pay order {order_id} could not be canceled locally"
            )
        canceled += 1
    if canceled:
        logger.info(
            "Canceled Crypto Pay invoices before base-currency switch: count=%s",
            canceled,
        )
    return canceled


async def reconcile_cryptobot_before_token_replacement(
    *,
    bot: Any = None,
) -> CryptoBotTokenReplacementReadiness:
    """Resolve expired/paid invoices and block while old-token work remains."""
    from bot.services.cryptobot import cryptobot_lifecycle_lock

    async with cryptobot_lifecycle_lock():
        return await _reconcile_cryptobot_before_token_replacement_locked(bot=bot)


async def replace_cryptobot_token_safely(
    token: str,
    *,
    bot: Any = None,
) -> CryptoBotTokenReplacementReadiness:
    """Reconcile old-token invoices and persist the replacement atomically."""
    normalized_token = str(token or '').strip()
    if not normalized_token or any(
        ord(character) < 32 for character in normalized_token
    ):
        raise ValueError('Crypto Pay API token is invalid')

    from bot.services.cryptobot import cryptobot_lifecycle_lock

    async with cryptobot_lifecycle_lock():
        readiness = await _reconcile_cryptobot_before_token_replacement_locked(
            bot=bot,
        )
        if readiness.ready:
            set_setting('cryptobot_api_token', normalized_token)
        return readiness


async def _reconcile_cryptobot_before_token_replacement_locked(
    *,
    bot: Any = None,
) -> CryptoBotTokenReplacementReadiness:
    """Reconcile old-token invoices while the caller owns the lifecycle lock."""
    from bot.services.cryptobot import check_cryptobot_invoice

    active = 0
    uncertain = 0
    paid = 0
    canceled = 0
    for provider_order in list_pending_payment_provider_orders("cryptobot"):
        order_id = str(provider_order.get("order_id") or "")
        intent = load_payment_intent(order_id)
        snapshot = _cryptobot_snapshot(provider_order)
        if intent is None or snapshot is None:
            uncertain += 1
            continue
        external_id, amount, currency = snapshot
        try:
            checked = await check_cryptobot_invoice(
                invoice_id=external_id,
                order_id=order_id,
                amount=amount,
                fiat=currency,
            )
        except Exception as error:
            logger.warning(
                "Crypto Pay token replacement reconciliation failed "
                "order=%s error=%s",
                order_id,
                error,
            )
            uncertain += 1
            continue

        metadata = dict(provider_order.get("metadata") or {})
        metadata.update(dict(checked.metadata))
        if checked.status == "pending":
            active += 1
            continue
        update_payment_provider_order_status(
            order_id,
            checked.status,
            metadata=metadata,
        )
        if checked.status == "canceled":
            if cancel_payment_intent(order_id, user_id=intent.user_id):
                canceled += 1
            else:
                uncertain += 1
            continue

        from bot.services.payment_completion import complete_confirmed_payment

        result = await complete_confirmed_payment(
            order_id,
            bot=bot,
            background=bot is not None,
            notify_user=bot is not None,
            show_primary_result=bot is not None,
        )
        paid += 1
        if not result.payment_completed:
            logger.warning(
                "Crypto Pay paid invoice needs fulfillment retry before token "
                "replacement: order=%s result=%s",
                order_id,
                result.text,
            )

    return CryptoBotTokenReplacementReadiness(
        active=active,
        uncertain=uncertain,
        paid=paid,
        canceled=canceled,
    )


__all__ = [
    "CryptoBotCancellationUncertain",
    "CryptoBotPaymentConfirmed",
    "CryptoBotTokenReplacementReadiness",
    "SafePaymentCancellation",
    "cancel_payment_intent_safely",
    "cancel_pending_cryptobot_for_base_switch",
    "reconcile_cryptobot_before_token_replacement",
    "replace_cryptobot_token_safely",
    "restart_payment_intent_safely",
]
