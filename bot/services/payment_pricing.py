"""Shared base-currency pricing before provider selection and conversion."""
from __future__ import annotations

from typing import Any


def calculate_base_price(
    *, user_id: int, tariff: dict[str, Any], nominal_minor: int,
    snapshot: dict[str, Any], purpose: str, order_id: str | None = None,
    explicit_code: str | None = None, use_active_promo: bool = True,
    phase: str = 'quote', key_id: int | None = None,
) -> dict[str, Any]:
    """Apply the native promo, then read-only base policies in registry order."""
    from database.requests import (
        find_order_by_order_id, get_promo_code_availability,
        get_user_active_promo_code, get_user_active_promo_snapshot,
    )
    from bot.services.promotions import _discount_amount
    from bot.utils.policy_registry import apply_pricing_policies

    promo, promo_error, skipped = None, None, None
    if explicit_code:
        availability = get_promo_code_availability(explicit_code, order_id=order_id, user_id=user_id)
        if availability.get('ok'):
            promo = availability.get('promo')
        else:
            promo_error = str(availability.get('reason') or 'unavailable')
    elif use_active_promo:
        promo = (
            get_user_active_promo_snapshot(user_id) if phase == 'preview'
            else get_user_active_promo_code(user_id, order_id=order_id)
        )
        if promo:
            availability = get_promo_code_availability(
                promo['code'], order_id=order_id, user_id=user_id, block_user_reservations=True,
            )
            if not availability.get('ok'):
                skipped = str(availability.get('reason') or 'unavailable')
                promo = None
            else:
                promo = availability['promo']
    discount_percent = int(promo.get('discount_percent') or 0) if promo else 0
    discount_minor = _discount_amount(nominal_minor, discount_percent) if promo else 0
    payable_minor = max(0, nominal_minor - discount_minor)
    quote = {
        'ok': promo_error is None, 'promo': promo, 'promo_error': promo_error,
        'promo_skipped_reason': skipped, 'base_currency': snapshot.get('base_currency', 'RUB'),
        'original_amount': nominal_minor, 'final_amount': payable_minor,
        'discount_amount': discount_minor, 'discount_percent': discount_percent,
        'nominal_amount_minor': nominal_minor, 'payable_amount_minor': payable_minor,
        'discount_amount_minor': discount_minor, 'pricing_policies': [],
        'unavailable_reason': promo_error,
    }
    if promo_error is None:
        if key_id is None and order_id and purpose == 'key_renewal':
            order = find_order_by_order_id(order_id) or {}
            key_id = order.get('vpn_key_id')
        quote = apply_pricing_policies(quote, {
            'user_id': user_id, 'tariff': dict(tariff), 'purpose': purpose,
            'order_id': order_id, 'key_id': key_id, 'phase': phase, 'mode': 'base',
            'base_currency': quote['base_currency'], 'nominal_amount_minor': nominal_minor,
            'payable_amount_minor': payable_minor,
        }, mode='base')
    quote['payable_amount_minor'] = int(quote['final_amount'])
    quote['discount_amount_minor'] = max(0, nominal_minor - quote['payable_amount_minor'])
    quote['is_free'] = (
        quote['payable_amount_minor'] == 0
        and (promo is not None or bool(quote['pricing_policies']))
    )
    return quote


def preview_tariff_price(*, user_id: int, tariff, purpose: str, key_id=None) -> dict[str, Any]:
    """Build display data for an already selected, core-owned tariff catalog."""
    from bot.services.exchange_rate import get_payment_rate_snapshot
    from bot.utils.action_policy import _action_policy_context

    with _action_policy_context('price_preview'):
        return calculate_base_price(
            user_id=user_id, tariff=dict(tariff), nominal_minor=int(tariff.get('price_minor') or 0),
            snapshot=get_payment_rate_snapshot(), purpose=purpose, phase='preview', key_id=key_id,
        )


def preview_payment_price(
    *, user_id: int, purpose: str, tariff_id: int | None = None,
    key_id: int | None = None, nominal_amount_minor: int | None = None,
) -> dict[str, Any]:
    """Validate a core purchase target and return a non-binding base-price preview."""
    from database.requests import get_tariff_by_id, is_tariff_payment_target_allowed
    from database.db_extension_payments import positive_int
    from bot.services.exchange_rate import get_payment_rate_snapshot
    from bot.utils.action_policy import _action_policy_context

    user_id = positive_int(user_id, 'user_id')
    if purpose not in {'key_purchase', 'key_renewal', 'balance_topup'}:
        raise ValueError('unsupported payment purpose')
    if purpose == 'balance_topup':
        if tariff_id is not None or key_id is not None:
            raise ValueError('balance topup does not accept a tariff or key')
        nominal = positive_int(nominal_amount_minor, 'nominal_amount_minor')
        tariff = {'id': None, 'price_minor': nominal}
    else:
        if nominal_amount_minor is not None:
            raise ValueError('tariff pricing does not accept an amount override')
        tariff_id = positive_int(tariff_id, 'tariff_id')
        if purpose == 'key_renewal':
            key_id = positive_int(key_id, 'key_id')
        elif key_id is not None:
            raise ValueError('purchase does not accept key_id')
        tariff = get_tariff_by_id(tariff_id)
        if not tariff or not is_tariff_payment_target_allowed(
            user_id=user_id, tariff_id=tariff_id, vpn_key_id=key_id,
        ):
            raise ValueError('tariff is not available for this user and purpose')
        nominal = positive_int(int(tariff.get('price_minor') or 0), 'tariff.price_minor')
    with _action_policy_context('price_preview'):
        quote = calculate_base_price(
            user_id=user_id, tariff=tariff, nominal_minor=nominal,
            snapshot=get_payment_rate_snapshot(), purpose=purpose, key_id=key_id, phase='preview',
        )
    return {
        'contract_version': 1, 'purpose': purpose, 'tariff_id': tariff_id, 'key_id': key_id,
        **{field: quote[field] for field in (
            'ok', 'base_currency', 'nominal_amount_minor', 'payable_amount_minor',
            'discount_amount_minor', 'is_free', 'unavailable_reason',
        )},
        'promo_code': (quote.get('promo') or {}).get('code'),
    }
