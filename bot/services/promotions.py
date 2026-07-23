import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from bot.services.exchange_rate import (
    get_payment_rate_snapshot,
    provider_amount_from_base_minor,
    provider_units_to_base_minor,
)
from bot.services.money import format_money_minor, payment_type_currency

from database.requests import (
    apply_promo_for_order,
    cancel_promo_reservation_for_order,
    clear_user_active_promo_code,
    create_auto_coupon_for_user,
    get_promo_code_availability,
    get_promo_code_by_source,
    get_user_active_promo_code,
    reserve_promo_for_order,
    save_order_pricing_snapshot,
    set_user_active_promo_code,
)

logger = logging.getLogger(__name__)

RUB_PAYMENT_TYPES = {"cards", "yookassa_qr", "wata", "platega", "cardlink", "balance"}
CENTS_PAYMENT_TYPES = {"crypto"} | RUB_PAYMENT_TYPES

PAYMENT_MINIMUMS = {
    "crypto": 1,
    "stars": 1,
    "cards": 10000,
    "yookassa_qr": 100,
    "wata": 1000,
    "platega": 1000,
    "cardlink": 1000,
    "balance": 0,
}

PAYMENT_MINIMUM_LABELS = {
    "crypto": "$0,01 USDT",
    "stars": "1 ⭐",
    "cards": "100 ₽",
    "yookassa_qr": "1 ₽",
    "wata": "10 ₽",
    "platega": "10 ₽",
    "cardlink": "10 ₽",
    "balance": "0 ₽",
}

def _amount_unit(payment_type: str) -> str:
    return "stars" if payment_type_currency(payment_type) == 'XTR' else "cents"


def _base_amount(tariff: Dict[str, Any], payment_type: str) -> int:
    if payment_type not in {"stars", "crypto"} | RUB_PAYMENT_TYPES and not _is_custom_payment_type(payment_type):
        raise ValueError(f"Неизвестный тип оплаты: {payment_type}")
    if tariff.get('price_minor') is not None:
        return max(0, int(tariff.get('price_minor') or 0))
    try:
        legacy_rubles = Decimal(str(tariff.get('price_rub') or 0))
    except (InvalidOperation, TypeError, ValueError):
        legacy_rubles = Decimal('0')
    return max(0, int((legacy_rubles * Decimal('100')).to_integral_value(rounding=ROUND_HALF_UP)))


def format_amount(amount: int, payment_type: str) -> str:
    return format_money_minor(amount, payment_type_currency(payment_type))


def _discount_amount(original_amount: int, discount_percent: int) -> int:
    return max(0, min(original_amount, original_amount * int(discount_percent) // 100))


def discounted_amount_minor(original_amount: int, discount_percent: int) -> int:
    """Return a provider-neutral payable preview in base minor units."""
    nominal = max(0, int(original_amount))
    return nominal - _discount_amount(nominal, discount_percent)


def get_active_promo_discount_percent(user_id: int) -> int:
    """Return the currently usable promo discount for a pre-intent tariff list."""
    promo = get_user_active_promo_code(int(user_id))
    if not promo:
        return 0
    return max(0, min(100, int(promo.get("discount_percent") or 0)))


def _unavailable_code(payment_type: str, original_amount: int, final_amount: int) -> Optional[str]:
    if original_amount <= 0:
        return "price_unset"
    minimum = _payment_minimum(payment_type)
    if final_amount > 0 and final_amount < minimum:
        return "below_minimum"
    return None


def _is_custom_payment_type(payment_type: str | None) -> bool:
    try:
        from bot.utils.payment_provider_registry import is_custom_payment_type

        return is_custom_payment_type(payment_type)
    except Exception:
        return False


def _payment_minimum(payment_type: str) -> int:
    try:
        from bot.utils.payment_provider_registry import get_payment_provider_by_type

        provider = get_payment_provider_by_type(payment_type)
    except Exception:
        provider = None
    if provider is not None:
        return int(provider.minimum_amount_minor or 0)
    return PAYMENT_MINIMUMS.get(payment_type, 0)


def _payment_minimum_label(payment_type: str, minimum: int) -> str:
    if _is_custom_payment_type(payment_type):
        return format_amount(minimum, payment_type)
    return PAYMENT_MINIMUM_LABELS.get(payment_type, str(minimum))


def build_quote(
    *,
    user_id: int,
    tariff: Dict[str, Any],
    payment_type: str,
    order_id: Optional[str] = None,
    explicit_code: Optional[str] = None,
    purpose: str = 'key_purchase',
    nominal_amount_minor: int | None = None,
    nominal_amount_cents: int | None = None,
    rate_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Returns the price calculation taking into account the active promotional code or coupon."""
    raw_nominal = nominal_amount_minor if nominal_amount_minor is not None else nominal_amount_cents
    nominal_minor = (
        _base_amount(tariff, payment_type)
        if raw_nominal is None
        else max(0, int(raw_nominal))
    )
    snapshot = dict(rate_snapshot or get_payment_rate_snapshot())
    amount_unit = _amount_unit(payment_type)
    promo = None
    promo_error = None

    if explicit_code:
        availability = get_promo_code_availability(
            explicit_code,
            order_id=order_id,
            user_id=user_id,
        )
        if availability.get("ok"):
            promo = availability.get("promo")
        else:
            promo_error = str(availability.get("reason") or "unavailable")
    else:
        promo = get_user_active_promo_code(user_id, order_id=order_id)
        if promo:
            availability = get_promo_code_availability(
                promo["code"],
                order_id=order_id,
                user_id=user_id,
            )
            if not availability.get("ok"):
                promo = None

    discount_percent = int(promo.get("discount_percent") or 0) if promo else 0
    discount_minor = _discount_amount(nominal_minor, discount_percent) if promo else 0
    payable_minor = max(0, nominal_minor - discount_minor)
    original_amount, charge_currency = provider_amount_from_base_minor(
        nominal_minor,
        payment_type,
        snapshot,
    )
    final_amount, _ = provider_amount_from_base_minor(
        payable_minor,
        payment_type,
        snapshot,
    )
    discount_amount = max(0, original_amount - final_amount)

    quote = {
        "ok": promo_error is None,
        "promo": promo,
        "promo_error": promo_error,
        "payment_type": payment_type,
        "amount_unit": amount_unit,
        "original_amount": original_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "final_amount": final_amount,
        "base_currency": snapshot.get('base_currency', 'RUB'),
        "nominal_amount_minor": nominal_minor,
        "payable_amount_minor": payable_minor,
        "discount_amount_minor": discount_minor,
        "nominal_amount_cents": nominal_minor,
        "payable_amount_cents": payable_minor,
        "discount_rub_cents": discount_minor,
        "charge_currency": charge_currency,
        "rate_snapshot": snapshot,
        "purpose": purpose,
        "is_free": final_amount == 0 and promo is not None,
        "unavailable_reason": promo_error,
        "unavailable_code": promo_error,
        "minimum_amount_label": _payment_minimum_label(
            payment_type,
            _payment_minimum(payment_type),
        ),
    }

    if promo_error is None:
        from bot.utils.policy_registry import apply_pricing_policies

        quote = apply_pricing_policies(
            quote,
            {
                "user_id": user_id,
                "tariff": dict(tariff or {}),
                "payment_type": payment_type,
                "order_id": order_id,
                "explicit_code": explicit_code,
                "purpose": purpose,
                "base_currency": snapshot.get('base_currency', 'RUB'),
                "nominal_amount_minor": nominal_minor,
                "payable_amount_minor": payable_minor,
                "nominal_amount_cents": nominal_minor,
                "payable_amount_cents": payable_minor,
                "rate_snapshot": dict(snapshot),
            },
        )

        amount_policy_applied = any(
            'final_amount' in policy or 'discount_amount' in policy
            for policy in quote.get('pricing_policies') or []
        )
        if quote.get('ok') is not False and amount_policy_applied:
            payable_minor = provider_units_to_base_minor(
                int(quote.get('final_amount') or 0),
                payment_type,
                snapshot,
            )
            quote['payable_amount_minor'] = payable_minor
            quote['payable_amount_cents'] = payable_minor
            quote['discount_amount_minor'] = max(0, nominal_minor - payable_minor)
            quote['discount_rub_cents'] = quote['discount_amount_minor']

    if quote.get("ok") is False:
        if promo_error is None:
            extension_reason = quote.get("unavailable_reason")
            if extension_reason:
                quote["extension_unavailable_reason"] = extension_reason
            quote["unavailable_reason"] = "policy_rejected"
            quote["unavailable_code"] = "policy_rejected"
        return quote

    unavailable_reason = _unavailable_code(
        payment_type,
        int(quote["original_amount"]),
        int(quote["final_amount"]),
    )
    quote["ok"] = unavailable_reason is None
    quote["is_free"] = (
        int(quote["final_amount"]) == 0
        and (quote.get("promo") is not None or bool(quote.get("pricing_policies")))
    )
    quote["unavailable_reason"] = unavailable_reason
    quote["unavailable_code"] = unavailable_reason
    return quote


def prepare_order_pricing(
    *,
    order_id: str,
    user_id: int,
    tariff: Dict[str, Any],
    payment_type: str,
    action: str,
    purpose: str | None = None,
    nominal_amount_minor: int | None = None,
    nominal_amount_cents: int | None = None,
    rate_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Calculates the price, saves the snapshot in payments and reserves a promotional code for the order.
    """
    quote = build_quote(
        user_id=user_id,
        tariff=tariff,
        payment_type=payment_type,
        order_id=order_id,
        purpose=purpose or action,
        nominal_amount_minor=nominal_amount_minor,
        nominal_amount_cents=nominal_amount_cents,
        rate_snapshot=rate_snapshot,
    )

    if not quote["ok"]:
        cancel_promo_reservation_for_order(order_id)
        return quote

    save_order_pricing_snapshot(
        order_id=order_id,
        payment_type=payment_type,
        original_amount=quote["original_amount"],
        discount_amount=quote["discount_amount"],
        final_amount=quote["final_amount"],
        amount_unit=quote["amount_unit"],
        promo=quote["promo"],
    )

    if quote["promo"]:
        reservation = reserve_promo_for_order(
            order_id=order_id,
            user_id=user_id,
            promo=quote["promo"],
            payment_type=payment_type,
            action=action,
            original_amount=quote["original_amount"],
            discount_amount=quote["discount_amount"],
            final_amount=quote["final_amount"],
            amount_unit=quote["amount_unit"],
        )
        if not reservation.get("ok"):
            quote["ok"] = False
            quote["unavailable_reason"] = str(reservation.get("reason") or "unavailable")
            quote["unavailable_code"] = quote["unavailable_reason"]
            return quote
    else:
        cancel_promo_reservation_for_order(order_id)

    return quote


def activate_promo_code_for_user(user_id: int, code: str, *, allow_coupons: bool = True) -> Dict[str, Any]:
    """Checks the code and saves it to the user as active for the next payment."""
    availability = get_promo_code_availability(
        (code or "").strip(),
        user_id=user_id,
    )
    if not availability.get("ok"):
        return {
            "ok": False,
            "reason": str(availability.get("reason") or "unavailable"),
            "promo": availability.get("promo"),
        }
    promo = availability["promo"]
    if promo.get("type") == "coupon" and not allow_coupons:
        return {
            "ok": False,
            "reason": "coupon_link_disallowed",
            "promo": promo,
        }
    set_user_active_promo_code(user_id, promo["id"])
    return {"ok": True, "reason": "applied", "promo": promo}


def describe_quote_lines(quote: Dict[str, Any]) -> str:
    """Generates a short HTML block about the applied discount."""
    promo = quote.get("promo")
    pricing_policies = quote.get("pricing_policies") or []
    if not promo and not pricing_policies:
        return ""
    from bot.utils.text import escape_html
    from bot.utils.user_ui_texts import render_ui_text

    original = format_amount(quote["original_amount"], quote["payment_type"])
    final = format_amount(quote["final_amount"], quote["payment_type"])
    lines = []
    if promo:
        discount = quote["discount_percent"]
        lines.append(
            "\n" + render_ui_text(
                "payment.quote.promo_line",
                promo_code=str(promo["code"]),
                discount=discount,
            )
        )
    for policy in pricing_policies:
        label = policy.get("label") or policy.get("name")
        if label:
            lines.append(f"\n<b>{escape_html(str(label))}</b>")
    lines.append(
        "\n" + render_ui_text(
            "payment.quote.price_line",
            old_price=original,
            new_price=final,
        )
    )
    return "".join(lines)


def apply_order_promotion_after_payment(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Records the successful use of a promotional code/coupon after payment."""
    if not order or not order.get("promo_code_id"):
        return None

    redemption = apply_promo_for_order(order["order_id"])
    if redemption:
        clear_user_active_promo_code(
            order["user_id"],
            promo_code_id=redemption["promo_code_id"],
        )
    return redemption


def _order_final_amount(order: Dict[str, Any]) -> int:
    payment_type = order.get("payment_type")
    if payment_type == "stars":
        return int(order.get("final_amount_stars") if order.get("final_amount_stars") is not None else order.get("amount_stars") or 0)
    if payment_type in CENTS_PAYMENT_TYPES or _is_custom_payment_type(payment_type):
        return int(order.get("final_amount_cents") if order.get("final_amount_cents") is not None else order.get("amount_cents") or 0)
    return 0


def maybe_issue_auto_coupon_after_payment(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Issues a one-time coupon after a paid purchase or renewal."""
    return _issue_auto_coupon_after_payment(order)


async def maybe_issue_auto_coupon_after_payment_async(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Issues a coupon and applies promo reward policies through domain services."""
    coupon = _issue_auto_coupon_after_payment(order)
    policy_rewards = await _apply_promo_reward_policies_after_payment(order)
    if policy_rewards:
        return {
            "type": "promo_rewards",
            "coupon": coupon,
            "rewards": policy_rewards,
        }
    return coupon


def _issue_auto_coupon_after_payment(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Creates only a coupon, without changing the core state with rewards."""
    if not order:
        return None
    if order.get("payment_type") in {"promo_free", "trial", "demo"}:
        return None
    if _order_final_amount(order) <= 0:
        return None
    order_id = str(order.get("order_id") or "").strip()
    source = f"auto_payment:{order_id}" if order_id else "auto"
    coupon = get_promo_code_by_source(
        source,
        issued_to_user_id=int(order["user_id"]),
    )
    if coupon:
        return coupon
    try:
        coupon = create_auto_coupon_for_user(order["user_id"], source=source)
    except Exception as e:
        logger.warning("Не удалось автоматически выдать купон по заказу %s: %s", order.get("order_id"), e)
        coupon = get_promo_code_by_source(
            source,
            issued_to_user_id=int(order["user_id"]),
        )

    return coupon


async def _apply_promo_reward_policies_after_payment(order: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Applies declarative post-payment promo rewards via core API."""
    try:
        from bot.utils.policy_registry import apply_promo_reward_policies

        rewards = apply_promo_reward_policies(
            {
                "order": dict(order or {}),
                "user_id": order.get("user_id"),
                "payment_type": order.get("payment_type"),
                "tariff_id": order.get("tariff_id"),
                "period_days": order.get("period_days") or order.get("duration_days"),
                "amount_cents": _order_final_amount(order),
            }
        )
    except Exception as e:
        logger.warning("Не удалось применить promo reward policies по заказу %s: %s", order.get("order_id"), e)
        return []

    applied: list[Dict[str, Any]] = []
    for reward_index, reward in enumerate(rewards):
        if reward.get("type") != "days":
            continue
        reward_days = int(reward.get("reward_days") or 0)
        if reward_days <= 0:
            continue
        reward_entry = dict(reward)
        try:
            from bot.services.rewards import grant_days_to_first_active_key

            result = await grant_days_to_first_active_key(
                int(order["user_id"]),
                reward_days,
                source='promo_reward',
                reason=str(reward.get('label') or reward.get('name') or 'Промо-награда'),
                reference_type='payment_promo_reward',
                reference_id=f"{str(order.get('order_id') or '')}:{reward_index}",
                metadata={
                    'reward_name': reward.get('name'),
                    'payment_type': order.get('payment_type'),
                    'tariff_id': order.get('tariff_id'),
                },
            )
            reward_entry["applied"] = bool(result.get("ok"))
            reward_entry["result"] = result
        except Exception as e:
            reward_entry["applied"] = False
            reward_entry["reason"] = str(e)
            logger.warning(
                "Не удалось применить promo reward '%s' по заказу %s: %s",
                reward.get("name"),
                order.get("order_id"),
                e,
            )
        if reward_entry["applied"]:
            applied.append(reward_entry)

    return applied


def format_auto_coupon_text(coupon: Optional[Dict[str, Any]]) -> str:
    if not coupon:
        return ""
    if coupon.get("type") == "promo_rewards":
        parts = []
        if coupon.get("coupon"):
            parts.append(format_auto_coupon_text(coupon["coupon"]))
        for reward in coupon.get("rewards") or []:
            reward_days = int(reward.get("reward_days") or 0)
            if reward_days <= 0:
                continue
            label = reward.get("label") or reward.get("name")
            if not label:
                continue
            from bot.utils.text import escape_html
            from bot.utils.user_ui_texts import render_ui_text

            parts.append(
                f"\n\n{escape_html(str(label))}: "
                f"<b>{escape_html(render_ui_text('format.days_short', days=reward_days))}</b>"
            )
        return "".join(parts)
    from bot.utils.user_ui_texts import render_ui_text

    return "\n\n" + render_ui_text("promo.auto_coupon", promo_code=coupon["code"])
