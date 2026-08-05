"""Single application flow for every provider-confirmed payment."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from bot.services.new_key_setup import NewKeySetupResult, NewKeySetupStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentCompletionResult:
    """Typed completion outcome shared by handlers, webhooks and schedulers."""

    ok: bool
    order_id: str
    text: str
    order: Mapping[str, Any] | None = None
    purpose: str | None = None
    processed_now: bool = False
    payment_completed: bool = False
    user_notified: bool = False
    key_setup_status: NewKeySetupStatus | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Compatibility view for integrations that serialize completion results."""
        return {
            "ok": self.ok,
            "order_id": self.order_id,
            "text": self.text,
            "order": dict(self.order) if self.order is not None else None,
            "purpose": self.purpose,
            "processed_now": self.processed_now,
            "payment_completed": self.payment_completed,
            "user_notified": self.user_notified,
            "key_setup_status": (
                self.key_setup_status.value if self.key_setup_status else None
            ),
            "retryable": self.retryable,
        }


def _normalized_purpose(order: Mapping[str, Any]) -> str:
    purpose = str(order.get("purpose") or "").strip()
    if purpose in {"key_purchase", "key_renewal", "balance_topup"}:
        return purpose
    action = str(order.get("_payment_action") or "").strip()
    if action in {"key_purchase", "new_key"}:
        return "key_purchase"
    if action in {"key_renewal", "renewal"}:
        return "key_renewal"
    if action == "balance_topup":
        return action
    return "key_renewal" if order.get("vpn_key_id") else "key_purchase"


async def _render_interactive_page(
    target: Any,
    page_key: str,
    *,
    context: Mapping[str, Any],
    force_new: bool = True,
):
    if target is None:
        return None
    from bot.utils.page_renderer import render_page

    return await render_page(
        target,
        page_key,
        context=dict(context),
        force_new=force_new,
    )


async def _render_primary_result(
    *,
    order: Mapping[str, Any],
    purpose: str,
    bot: Any,
    telegram_id: int,
    target: Any,
    background: bool,
):
    """Render only the source-specific presentation around common business state."""
    order_id = str(order.get("order_id") or "")
    if background:
        from bot.utils.background_page_delivery import send_background_page

        page_key = (
            "balance_topup_result"
            if purpose == "balance_topup"
            else "payment_auto_completed"
        )
        context: dict[str, Any] = {
            "telegram_id": telegram_id,
            "order_id": order_id,
        }
        if purpose == "balance_topup":
            from bot.services.payment_intents import format_base_minor

            currency = str(order.get("base_currency") or "RUB")
            context.update({
                "payment_nominal_text": format_base_minor(
                    int(
                        order.get("nominal_amount_minor")
                        or order.get("nominal_amount_cents")
                        or 0
                    ),
                    currency,
                ),
                "payment_amount_text": format_base_minor(
                    int(
                        order.get("payable_amount_minor")
                        or order.get("payable_amount_cents")
                        or 0
                    ),
                    currency,
                ),
            })
        return await send_background_page(
            bot,
            telegram_id=telegram_id,
            page_key=page_key,
            context=context,
        )

    if purpose == "key_purchase":
        return await _render_interactive_page(
            target,
            "payment_completed",
            context={"telegram_id": telegram_id, "order_id": order_id},
            force_new=True,
        )

    if purpose == "key_renewal":
        from bot.utils.key_pages import build_key_page_context
        from bot.utils.user_ui_texts import render_duration_days
        from database.requests import get_key_details_for_user

        key_id = int(order.get("vpn_key_id") or 0)
        key = get_key_details_for_user(key_id, telegram_id) if key_id else None
        if not key:
            return await _render_interactive_page(
                target,
                "payment_failed",
                context={"telegram_id": telegram_id, "order_id": order_id},
                force_new=True,
            )
        period = (
            order.get("period_days")
            if order.get("period_days") is not None
            else order.get("duration_days") or 0
        )
        return await _render_interactive_page(
            target,
            "key_renewed",
            context={
                "telegram_id": telegram_id,
                "order_id": order_id,
                "key_id": key_id,
                "payment_term_text": render_duration_days(period),
                **build_key_page_context(key),
            },
            force_new=True,
        )

    from bot.services.payment_intents import format_base_minor, load_payment_intent
    from bot.utils.extension_rendering import render_extension_page, render_extension_route

    intent = load_payment_intent(order_id)
    success_target = intent.navigation.success_target if intent else None
    currency = str(
        (intent.base_currency if intent else order.get("base_currency")) or "RUB"
    )
    context = {
        "telegram_id": telegram_id,
        "order_id": order_id,
        "payment_purpose": purpose,
        "payment_nominal_text": format_base_minor(
            int(
                order.get("nominal_amount_minor")
                or order.get("nominal_amount_cents")
                or 0
            ),
            currency,
        ),
        "payment_amount_text": format_base_minor(
            int(
                order.get("payable_amount_minor")
                or order.get("payable_amount_cents")
                or 0
            ),
            currency,
        ),
    }
    if success_target and success_target.kind == "page":
        await render_extension_page(
            target,
            success_target.value,
            context,
            force_new_for_message=True,
        )
    else:
        await render_extension_route(
            target,
            success_target.value if success_target else "balance_topup_result",
            context,
            force_new_for_message=True,
        )
    return target


async def _deliver_optional_coupon(
    *,
    bot: Any,
    target: Any,
    telegram_id: int,
    order_id: str,
    background: bool,
) -> bool:
    if background:
        from bot.services.payment_coupon_delivery import (
            send_optional_payment_coupon_message,
        )

        return await send_optional_payment_coupon_message(
            bot,
            telegram_id=telegram_id,
            order_id=order_id,
        )
    from bot.services.payment_coupon_delivery import (
        render_optional_payment_coupon_message,
    )

    rendered = await render_optional_payment_coupon_message(
        target,
        order_id=order_id,
        telegram_id=telegram_id,
    )
    return rendered is not None


async def _run_key_setup_without_delivery(
    order_id: str,
    *,
    telegram_id: int,
) -> NewKeySetupResult:
    from bot.services.new_key_setup import provision_new_key, resolve_new_key_setup

    result = await resolve_new_key_setup(
        order_id,
        expected_telegram_id=telegram_id,
    )
    if result.status is NewKeySetupStatus.PROVISIONING:
        result = await provision_new_key(
            result,
            expected_telegram_id=telegram_id,
        )
    return result


def _finish_payment_auto_check(order_id: str) -> None:
    """Stop any polling row after the shared continuation has been handled."""
    from database.requests import get_payment_auto_check, update_payment_auto_check

    row = get_payment_auto_check(order_id)
    current_state = str((row or {}).get("state") or "")
    if current_state in {"active", "provider_succeeded"}:
        update_payment_auto_check(
            order_id,
            state="completed",
            expected_state=current_state,
        )


async def complete_confirmed_payment(
    order_id: str,
    *,
    bot: Any,
    target: Any = None,
    state: Any = None,
    telegram_id: int | None = None,
    background: bool = False,
    notify_user: bool = True,
    show_primary_result: bool = True,
    payment_type: str | None = None,
    referral_amount: int | None = None,
    retry_post_actions: bool = False,
) -> PaymentCompletionResult:
    """Complete and continue one provider-confirmed order through one code path."""
    from bot.services import billing
    from database.requests import find_order_by_order_id, get_user_by_id

    normalized_order_id = str(order_id or "").strip()
    initial_order = find_order_by_order_id(normalized_order_id)
    if not initial_order:
        return PaymentCompletionResult(
            ok=False,
            order_id=normalized_order_id,
            text="order_not_found",
        )

    initial_payment_completed = str(initial_order.get("status") or "") == "paid"
    initial_purpose = _normalized_purpose(initial_order)

    owner = get_user_by_id(int(initial_order.get("user_id") or 0))
    owner_telegram_id = int((owner or {}).get("telegram_id") or 0)
    if not owner_telegram_id:
        return PaymentCompletionResult(
            ok=False,
            order_id=normalized_order_id,
            text="owner_not_found",
            order=initial_order,
            purpose=initial_purpose,
            payment_completed=initial_payment_completed,
        )
    if telegram_id is not None and int(telegram_id) != owner_telegram_id:
        logger.warning(
            "Rejected foreign payment completion order=%s viewer=%s owner=%s",
            normalized_order_id,
            telegram_id,
            owner_telegram_id,
        )
        if target is not None:
            await _render_interactive_page(
                target,
                "payment_order_unavailable",
                context={"telegram_id": telegram_id},
            )
        return PaymentCompletionResult(
            ok=False,
            order_id=normalized_order_id,
            text="owner_mismatch",
            order=initial_order,
            purpose=initial_purpose,
            payment_completed=initial_payment_completed,
        )

    balance_override = 0
    if state is not None:
        try:
            state_data = await state.get_data()
            balance_override = int(state_data.get("balance_to_deduct") or 0)
        except Exception:
            logger.warning(
                "Failed to load payment FSM balance data order=%s",
                normalized_order_id,
            )

    completion_order: Mapping[str, Any] = initial_order
    purpose = initial_purpose
    processed_now = False
    payment_completed = initial_payment_completed
    user_notified = False
    key_setup_status: NewKeySetupStatus | None = None

    try:
        success, text, order = await billing.process_payment_order(
            normalized_order_id,
            bot=bot,
            process_referrals=False,
        )
        if not success or not order:
            failed_order = order or initial_order
            failed_payment_completed = (
                str(failed_order.get("status") or "") == "paid"
            )
            if target is not None and not background:
                await _render_interactive_page(
                    target,
                    "payment_failed",
                    context={
                        "telegram_id": owner_telegram_id,
                        "order_id": normalized_order_id,
                    },
                )
            return PaymentCompletionResult(
                ok=False,
                order_id=normalized_order_id,
                text=text,
                order=failed_order,
                purpose=_normalized_purpose(failed_order),
                payment_completed=failed_payment_completed,
                retryable=True,
            )

        completion_order = order
        processed_now = bool(order.get("_payment_processed_now", True))
        payment_completed = True
        if processed_now or retry_post_actions:
            await billing._run_payment_post_actions(
                order,
                bot=bot,
                payment_type=str(payment_type or order.get("payment_type") or ""),
                referral_amount=(
                    int(referral_amount)
                    if referral_amount is not None
                    else billing._payment_order_referral_amount(order)
                ),
                balance_override_cents=balance_override,
                force=retry_post_actions,
            )

        if state is not None:
            await state.update_data(balance_to_deduct=0, remaining_cents=0)

        fresh_order = find_order_by_order_id(normalized_order_id) or order
        fresh_order.update({
            key: value
            for key, value in order.items()
            if str(key).startswith("_")
        })
        completion_order = fresh_order
        purpose = _normalized_purpose(fresh_order)
        primary_message = None
        coupon_delivered = False
        # Background delivery is emitted only by the processor that actually
        # closed the order. Retries, recovery and a concurrent manual check still
        # continue key setup, but cannot duplicate the automatic main notice.
        should_show_primary = bool(
            notify_user
            and show_primary_result
            and (not background or processed_now)
        )
        if should_show_primary:
            primary_message = await _render_primary_result(
                order=fresh_order,
                purpose=purpose,
                bot=bot,
                telegram_id=owner_telegram_id,
                target=target,
                background=background,
            )
            user_notified = primary_message is not None
            coupon_delivered = await _deliver_optional_coupon(
                bot=bot,
                target=target,
                telegram_id=owner_telegram_id,
                order_id=normalized_order_id,
                background=background,
            )

        key_setup: NewKeySetupResult | None = None
        if purpose == "key_purchase":
            if notify_user and background:
                from bot.handlers.user.payments.keys_config import (
                    start_new_key_config_background,
                )

                key_setup = await start_new_key_config_background(
                    bot,
                    telegram_id=owner_telegram_id,
                    username=(owner or {}).get("username"),
                    order_id=normalized_order_id,
                    anchor_message=primary_message,
                )
            elif notify_user and target is not None:
                from bot.handlers.user.payments.keys_config import run_new_key_setup_flow

                key_setup = await run_new_key_setup_flow(
                    primary_message or target,
                    normalized_order_id,
                    state=state,
                    owner_telegram_id=owner_telegram_id,
                    owner_username=(owner or {}).get("username"),
                    force_new=True,
                )
            else:
                key_setup = await _run_key_setup_without_delivery(
                    normalized_order_id,
                    telegram_id=owner_telegram_id,
                )
            key_setup_status = key_setup.status

        if purpose != "key_purchase" and state is not None:
            await state.clear()

        retryable = bool(key_setup and key_setup.retryable)
        completion_text = (
            key_setup.error_code
            if retryable and key_setup and key_setup.error_code
            else str(text or "payment_completed")
        )
        result = PaymentCompletionResult(
            ok=not retryable,
            order_id=normalized_order_id,
            text=completion_text,
            order=fresh_order,
            purpose=purpose,
            processed_now=processed_now,
            payment_completed=True,
            user_notified=bool(user_notified or coupon_delivered),
            key_setup_status=key_setup_status,
            retryable=retryable,
        )
        if result.ok:
            _finish_payment_auto_check(normalized_order_id)
        logger.info(
            "Payment completion order=%s source=%s purpose=%s processed_now=%s key_setup=%s retryable=%s",
            normalized_order_id,
            "background" if background else "interactive",
            purpose,
            processed_now,
            result.key_setup_status.value if result.key_setup_status else "none",
            result.retryable,
        )
        return result
    except Exception as error:
        from bot.errors import TariffNotFoundError

        page_key = (
            "payment_order_unavailable"
            if isinstance(error, TariffNotFoundError)
            else "payment_failed"
        )
        logger.exception(
            "Unified payment completion failed order=%s: %s",
            normalized_order_id,
            error,
        )
        if target is not None and not background:
            await _render_interactive_page(
                target,
                page_key,
                context={
                    "telegram_id": owner_telegram_id,
                    "order_id": normalized_order_id,
                },
            )
        return PaymentCompletionResult(
            ok=False,
            order_id=normalized_order_id,
            text=page_key,
            order=completion_order,
            purpose=purpose,
            processed_now=processed_now,
            payment_completed=payment_completed,
            user_notified=user_notified,
            key_setup_status=key_setup_status,
            retryable=True,
        )


__all__ = ["PaymentCompletionResult", "complete_confirmed_payment"]
