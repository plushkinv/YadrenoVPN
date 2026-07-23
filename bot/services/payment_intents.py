"""Core-owned payment intent types, validation, creation and quoting."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from database.requests import (
    cancel_unconfirmed_payment_for_method_change,
    create_payment_intent_record,
    find_order_by_order_id,
    get_base_currency,
    get_page,
    get_page_route,
    get_payment_intent,
    get_tariff_by_id,
    get_vpn_key_by_id,
    save_payment_balance_deduction,
    update_payment_intent_quote,
)

from bot.services.promotions import prepare_order_pricing
from bot.services.exchange_rate import provider_amount_from_base_minor
from bot.services.money import format_money_minor, minor_to_decimal
from bot.utils.user_ui_texts import render_ui_text

PURPOSE_KEY_PURCHASE = 'key_purchase'
PURPOSE_KEY_RENEWAL = 'key_renewal'
PURPOSE_BALANCE_TOPUP = 'balance_topup'


@dataclass(frozen=True)
class PaymentTarget:
    """Declarative post-payment target owned by the page/route registry."""

    kind: str
    value: str

    def as_dict(self) -> dict[str, str]:
        return {'kind': self.kind, 'value': self.value}


@dataclass(frozen=True)
class PaymentNavigation:
    """Success and cancel destinations stored with an intent."""

    success_target: PaymentTarget
    cancel_target: PaymentTarget


@dataclass(frozen=True)
class PaymentIntent:
    """Immutable application view of one persisted payment intent."""

    order_id: str
    user_id: int
    purpose: str
    purpose_data: Mapping[str, Any]
    base_currency: str
    nominal_amount_minor: int
    payable_amount_minor: int
    balance_deduct_minor: int
    description: str
    navigation: PaymentNavigation
    status: str
    fulfillment_status: str
    tariff_id: int | None = None
    vpn_key_id: int | None = None
    payment_type: str | None = None
    charge_amount: Decimal | None = None
    charge_currency: str | None = None
    rate_snapshot: Mapping[str, Any] = field(default_factory=dict)

    @property
    def nominal_amount_cents(self) -> int:
        """Deprecated v77 alias for two-decimal base minor units."""
        return self.nominal_amount_minor

    @property
    def payable_amount_cents(self) -> int:
        """Deprecated v77 alias for two-decimal base minor units."""
        return self.payable_amount_minor

    @property
    def balance_deduct_cents(self) -> int:
        """Deprecated v77 alias for two-decimal base minor units."""
        return self.balance_deduct_minor


@dataclass(frozen=True)
class PaymentQuote:
    """Provider charge and promotion snapshot for one intent."""

    order_id: str
    payment_type: str
    base_currency: str
    nominal_amount_minor: int
    payable_amount_minor: int
    charge_amount: Decimal
    charge_currency: str
    rate_snapshot: Mapping[str, Any]
    discount_percent: int = 0
    promo_code: str | None = None
    is_free: bool = False
    unavailable_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def nominal_amount_cents(self) -> int:
        return self.nominal_amount_minor

    @property
    def payable_amount_cents(self) -> int:
        return self.payable_amount_minor


@dataclass(frozen=True)
class PaymentResult:
    """Idempotent fulfillment outcome returned to provider and UI adapters."""

    order_id: str
    purpose: str
    completed: bool
    already_completed: bool = False
    target: PaymentTarget | None = None
    vpn_key_id: int | None = None
    credited_amount_minor: int = 0
    message: str = ''

    @property
    def credited_amount_cents(self) -> int:
        return self.credited_amount_minor


@dataclass(frozen=True)
class PaymentPurposeDefinition:
    """Trusted purpose metadata; extensions cannot add entries to this registry."""

    purpose: str
    required_payload_fields: tuple[str, ...]
    fulfillment_name: str


PURPOSE_REGISTRY: Mapping[str, PaymentPurposeDefinition] = MappingProxyType({
    PURPOSE_KEY_PURCHASE: PaymentPurposeDefinition(
        PURPOSE_KEY_PURCHASE,
        ('tariff_id',),
        'create_key',
    ),
    PURPOSE_KEY_RENEWAL: PaymentPurposeDefinition(
        PURPOSE_KEY_RENEWAL,
        ('tariff_id', 'key_id'),
        'renew_key',
    ),
    PURPOSE_BALANCE_TOPUP: PaymentPurposeDefinition(
        PURPOSE_BALANCE_TOPUP,
        (),
        'credit_balance',
    ),
})


def create_payment_intent(
    *,
    user_id: int,
    purpose: str,
    purpose_data: Mapping[str, Any] | None = None,
    nominal_amount_minor: int | None = None,
    nominal_amount_cents: int | None = None,
    description: str | None = None,
    navigation: PaymentNavigation | None = None,
) -> PaymentIntent:
    """Validates and creates a core purpose before a provider is selected."""
    definition = _purpose_definition(purpose)
    payload = dict(purpose_data or {})
    _validate_purpose_payload(definition, payload)

    tariff = None
    tariff_id = _optional_positive_int(payload.get('tariff_id'))
    if tariff_id is not None:
        tariff = get_tariff_by_id(tariff_id)
        if not tariff:
            raise ValueError('Tariff does not exist')

    base_currency = get_base_currency()
    if purpose == PURPOSE_BALANCE_TOPUP:
        raw_nominal = (
            nominal_amount_minor
            if nominal_amount_minor is not None
            else nominal_amount_cents
        )
        nominal = _positive_int(raw_nominal, 'nominal_amount_minor')
        nominal_value = format(minor_to_decimal(nominal, base_currency), "f")
        if "." in nominal_value:
            nominal_value = nominal_value.rstrip("0").rstrip(".")
        default_description = render_ui_text(
            "payment.invoice.topup_description",
            amount=nominal_value,
            currency=base_currency,
        )
    else:
        if not tariff:
            raise ValueError('Tariff is required for this payment purpose')
        nominal = _positive_int(
            int(tariff.get('price_minor') or 0),
            'tariff.price_minor',
        )
        tariff_name = str(tariff.get('name') or f'#{tariff_id}')
        if purpose == PURPOSE_KEY_PURCHASE:
            days = render_ui_text(
                "format.days_short",
                days=int(tariff.get('duration_days') or 0),
            )
            default_description = render_ui_text(
                "payment.invoice.purchase_description",
                tariff_name=tariff_name,
                days=days,
            )
        else:
            key_id = _optional_positive_int(payload.get('key_id'))
            key = get_vpn_key_by_id(key_id) if key_id else None
            key_name = str((key or {}).get('display_name') or f'#{key_id or 0}')
            default_description = render_ui_text(
                "payment.invoice.renewal_description",
                key_name=key_name,
                tariff_name=tariff_name,
            )

    resolved_navigation = navigation or default_payment_navigation(purpose)
    validate_payment_target(resolved_navigation.success_target)
    validate_payment_target(resolved_navigation.cancel_target)

    key_id = _optional_positive_int(payload.get('key_id'))
    _, order_id = create_payment_intent_record(
        user_id=_positive_int(user_id, 'user_id'),
        purpose=purpose,
        purpose_data=payload,
        nominal_amount_minor=nominal,
        base_currency=base_currency,
        description=(description or default_description).strip(),
        success_target=resolved_navigation.success_target.as_dict(),
        cancel_target=resolved_navigation.cancel_target.as_dict(),
        tariff_id=tariff_id,
        vpn_key_id=key_id,
        period_days=int(tariff.get('duration_days') or 0) if tariff else None,
    )
    intent = load_payment_intent(order_id)
    if intent is None:
        raise RuntimeError('Created payment intent cannot be loaded')
    return intent


def quote_payment_intent(order_id: str, payment_type: str) -> PaymentQuote:
    """Applies promotion/policy once and stores the provider-rate snapshot."""
    intent = load_payment_intent(order_id)
    if intent is None:
        raise ValueError('Payment intent does not exist')
    if intent.status != 'pending' or intent.fulfillment_status not in {'pending', 'failed'}:
        raise ValueError('Payment intent can no longer be quoted')

    tariff = get_tariff_by_id(intent.tariff_id) if intent.tariff_id else {
        'id': None,
        'name': intent.description,
        'duration_days': 0,
        'price_minor': intent.nominal_amount_minor,
        'base_currency': intent.base_currency,
    }
    if not tariff:
        raise ValueError('Payment intent tariff no longer exists')

    pricing = prepare_order_pricing(
        order_id=intent.order_id,
        user_id=intent.user_id,
        tariff=tariff,
        payment_type=str(payment_type),
        action=intent.purpose,
        purpose=intent.purpose,
        nominal_amount_minor=intent.nominal_amount_minor,
        nominal_amount_cents=intent.nominal_amount_minor,
        rate_snapshot=dict(intent.rate_snapshot) or None,
    )
    if not pricing.get('ok'):
        return _payment_quote(intent, pricing)

    if intent.balance_deduct_minor > 0 and intent.purpose != PURPOSE_BALANCE_TOPUP:
        external_payable = max(
            0,
            int(pricing.get('payable_amount_minor', pricing['payable_amount_cents']))
            - intent.balance_deduct_minor,
        )
        provider_amount, charge_currency = provider_amount_from_base_minor(
            external_payable,
            str(payment_type),
            dict(pricing['rate_snapshot']),
        )
        pricing['payable_amount_minor'] = external_payable
        pricing['payable_amount_cents'] = external_payable
        pricing['final_amount'] = provider_amount
        pricing['charge_currency'] = charge_currency

    charge_amount = _provider_charge_decimal(
        int(pricing['final_amount']),
        str(pricing['charge_currency']),
    )
    compatibility_cents = (
        int(pricing['final_amount'])
        if str(pricing['charge_currency']) != 'XTR'
        else 0
    )
    compatibility_stars = (
        int(pricing['final_amount'])
        if str(pricing['charge_currency']) == 'XTR'
        else 0
    )
    if not update_payment_intent_quote(
        intent.order_id,
        payment_type=str(payment_type),
        payable_amount_minor=int(
            pricing.get('payable_amount_minor', pricing['payable_amount_cents'])
        ),
        charge_amount=_decimal_text(charge_amount),
        charge_currency=str(pricing['charge_currency']),
        rate_snapshot=pricing['rate_snapshot'],
        compatibility_amount_cents=compatibility_cents,
        compatibility_amount_stars=compatibility_stars,
    ):
        raise RuntimeError('Payment intent quote was not persisted')
    return _payment_quote(intent, pricing, charge_amount=charge_amount)


def load_payment_intent(order_id: str) -> PaymentIntent | None:
    """Loads only v1 intents; legacy payments remain compatibility records."""
    row = get_payment_intent(str(order_id))
    if not row or int(row.get('intent_version') or 0) != 1:
        return None
    success = _target_from_mapping(row.get('success_target'), 'success_target')
    cancel = _target_from_mapping(row.get('cancel_target'), 'cancel_target')
    raw_charge = row.get('charge_amount')
    return PaymentIntent(
        order_id=str(row['order_id']),
        user_id=int(row['user_id']),
        purpose=str(row['purpose']),
        purpose_data=MappingProxyType(dict(row.get('purpose_data') or {})),
        base_currency=str(row.get('base_currency') or 'RUB').upper(),
        nominal_amount_minor=int(row.get('nominal_amount_minor') or 0),
        payable_amount_minor=int(row.get('payable_amount_minor') or 0),
        balance_deduct_minor=int(row.get('balance_deduct_minor') or 0),
        description=str(row.get('description') or ''),
        navigation=PaymentNavigation(success, cancel),
        status=str(row.get('status') or 'pending'),
        fulfillment_status=str(row.get('fulfillment_status') or 'pending'),
        tariff_id=_optional_positive_int(row.get('tariff_id')),
        vpn_key_id=_optional_positive_int(row.get('vpn_key_id')),
        payment_type=str(row['payment_type']) if row.get('payment_type') else None,
        charge_amount=Decimal(str(raw_charge)) if raw_charge not in {None, ''} else None,
        charge_currency=str(row['charge_currency']) if row.get('charge_currency') else None,
        rate_snapshot=MappingProxyType(dict(row.get('rate_snapshot') or {})),
    )


def cancel_payment_intent(order_id: str, *, user_id: int) -> bool:
    """Cancel one owned pending intent before leaving its payment flow."""
    return cancel_unconfirmed_payment_for_method_change(
        str(order_id),
        user_id=int(user_id),
    )


def restart_payment_intent_for_method_change(
    order_id: str,
    *,
    user_id: int,
) -> PaymentIntent | None:
    """Replace an unconfirmed provider-bound order with a fresh provider choice."""
    row = find_order_by_order_id(str(order_id))
    if (
        not row
        or int(row.get('user_id') or 0) != int(user_id)
        or str(row.get('status') or '') != 'pending'
        or row.get('provider_confirmed_at')
    ):
        return None

    current = load_payment_intent(str(order_id))
    if current is not None:
        purpose = current.purpose
        purpose_data = dict(current.purpose_data)
        nominal_amount = (
            current.nominal_amount_minor
            if purpose == PURPOSE_BALANCE_TOPUP
            else None
        )
        description = current.description
        navigation = current.navigation
        balance_deduct_minor = current.balance_deduct_minor
    else:
        key_id = _optional_positive_int(row.get('vpn_key_id'))
        raw_purpose = str(row.get('purpose') or '')
        if raw_purpose in PURPOSE_REGISTRY:
            purpose = raw_purpose
        else:
            purpose = PURPOSE_KEY_RENEWAL if key_id else PURPOSE_KEY_PURCHASE
        tariff_id = _optional_positive_int(row.get('tariff_id'))
        if purpose == PURPOSE_BALANCE_TOPUP:
            purpose_data = {}
            nominal_amount = int(
                row.get('nominal_amount_minor')
                or row.get('nominal_amount_cents')
                or row.get('amount_cents')
                or 0
            )
        else:
            if tariff_id is None:
                return None
            purpose_data = {'tariff_id': tariff_id}
            if purpose == PURPOSE_KEY_RENEWAL:
                if key_id is None:
                    return None
                purpose_data['key_id'] = key_id
            nominal_amount = None
        description = None
        navigation = None
        balance_deduct_minor = int(
            row.get('balance_deduct_minor')
            or row.get('balance_deduct_cents')
            or 0
        )

    try:
        replacement = create_payment_intent(
            user_id=int(user_id),
            purpose=purpose,
            purpose_data=purpose_data,
            nominal_amount_minor=nominal_amount,
            description=description,
            navigation=navigation,
        )
    except ValueError:
        return None

    if balance_deduct_minor > 0:
        if not save_payment_balance_deduction(
            replacement.order_id,
            balance_deduct_minor,
        ):
            cancel_payment_intent(
                replacement.order_id,
                user_id=int(user_id),
            )
            raise RuntimeError('Replacement balance deduction could not be persisted')
        refreshed = load_payment_intent(replacement.order_id)
        if refreshed is None:
            cancel_payment_intent(
                replacement.order_id,
                user_id=int(user_id),
            )
            raise RuntimeError('Replacement Payment Intent cannot be loaded')
        replacement = refreshed

    if not cancel_payment_intent(
        str(order_id),
        user_id=int(user_id),
    ):
        cancel_payment_intent(
            replacement.order_id,
            user_id=int(user_id),
        )
        return None
    return replacement


def default_payment_navigation(purpose: str) -> PaymentNavigation:
    """Returns stock declarative targets without Python callbacks."""
    _purpose_definition(purpose)
    if purpose == PURPOSE_BALANCE_TOPUP:
        return PaymentNavigation(
            PaymentTarget('route', 'balance_topup_result'),
            PaymentTarget('page', 'balance_topup_amount'),
        )
    if purpose == PURPOSE_KEY_RENEWAL:
        return PaymentNavigation(
            PaymentTarget('page', 'key_details'),
            PaymentTarget('page', 'renew_payment'),
        )
    return PaymentNavigation(
        PaymentTarget('page', 'key_details'),
        PaymentTarget('page', 'prepayment'),
    )


def validate_payment_target(target: PaymentTarget) -> None:
    """Rejects unknown, disabled or non-declarative navigation targets."""
    if not isinstance(target, PaymentTarget):
        raise ValueError('Payment target must be a PaymentTarget')
    if target.kind == 'page':
        if not get_page(target.value):
            raise ValueError(f'Unknown payment page target: {target.value}')
        return
    if target.kind == 'route':
        route = get_page_route(target.value)
        if not route or not route.get('is_enabled'):
            raise ValueError(f'Unknown or disabled payment route target: {target.value}')
        return
    raise ValueError('Payment target kind must be page or route')


def format_base_minor(amount_minor: int, currency: str | None = None) -> str:
    """Formats current or explicitly snapshotted base money."""
    return format_money_minor(amount_minor, currency or get_base_currency())


def format_rub_cents(amount_cents: int) -> str:
    """Deprecated formatter kept for old RUB-only callers."""
    return format_money_minor(amount_cents, 'RUB')


def _purpose_definition(purpose: str) -> PaymentPurposeDefinition:
    definition = PURPOSE_REGISTRY.get(str(purpose))
    if definition is None:
        raise ValueError(f'Unsupported core payment purpose: {purpose}')
    return definition


def _validate_purpose_payload(
    definition: PaymentPurposeDefinition,
    payload: Mapping[str, Any],
) -> None:
    unknown = set(payload) - {'tariff_id', 'key_id'}
    if unknown:
        raise ValueError(f'Unsupported purpose_data fields: {", ".join(sorted(unknown))}')
    for field_name in definition.required_payload_fields:
        _positive_int(payload.get(field_name), f'purpose_data.{field_name}')
    if definition.purpose == PURPOSE_BALANCE_TOPUP and payload:
        raise ValueError('balance_topup does not accept tariff or key payload')


def _payment_quote(
    intent: PaymentIntent,
    pricing: Mapping[str, Any],
    *,
    charge_amount: Decimal | None = None,
) -> PaymentQuote:
    currency = str(pricing.get('charge_currency') or 'RUB')
    provider_amount = int(pricing.get('final_amount') or 0)
    promo = pricing.get('promo') or {}
    return PaymentQuote(
        order_id=intent.order_id,
        payment_type=str(pricing.get('payment_type') or ''),
        base_currency=intent.base_currency,
        nominal_amount_minor=int(
            pricing.get('nominal_amount_minor')
            or pricing.get('nominal_amount_cents')
            or intent.nominal_amount_minor
        ),
        payable_amount_minor=int(
            pricing.get('payable_amount_minor')
            or pricing.get('payable_amount_cents')
            or 0
        ),
        charge_amount=charge_amount or _provider_charge_decimal(provider_amount, currency),
        charge_currency=currency,
        rate_snapshot=MappingProxyType(dict(pricing.get('rate_snapshot') or {})),
        discount_percent=int(pricing.get('discount_percent') or 0),
        promo_code=str(promo.get('code')) if promo.get('code') else None,
        is_free=bool(pricing.get('is_free')),
        unavailable_reason=str(pricing['unavailable_reason']) if pricing.get('unavailable_reason') else None,
        raw=MappingProxyType(dict(pricing)),
    )


def _provider_charge_decimal(amount: int, currency: str) -> Decimal:
    return minor_to_decimal(amount, currency)


def _target_from_mapping(value: Any, field_name: str) -> PaymentTarget:
    if not isinstance(value, Mapping):
        raise ValueError(f'Invalid {field_name}')
    target = PaymentTarget(str(value.get('kind') or ''), str(value.get('value') or ''))
    validate_payment_target(target)
    return target


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a positive integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be a positive integer') from exc
    if parsed <= 0:
        raise ValueError(f'{field_name} must be a positive integer')
    return parsed


def _optional_positive_int(value: Any) -> int | None:
    if value in {None, ''}:
        return None
    return _positive_int(value, 'identifier')


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


__all__ = [
    'cancel_payment_intent',
    'PURPOSE_BALANCE_TOPUP',
    'PURPOSE_KEY_PURCHASE',
    'PURPOSE_KEY_RENEWAL',
    'PURPOSE_REGISTRY',
    'PaymentIntent',
    'PaymentNavigation',
    'PaymentQuote',
    'PaymentResult',
    'PaymentTarget',
    'create_payment_intent',
    'default_payment_navigation',
    'format_base_minor',
    'format_rub_cents',
    'load_payment_intent',
    'quote_payment_intent',
    'restart_payment_intent_for_method_change',
    'validate_payment_target',
]
