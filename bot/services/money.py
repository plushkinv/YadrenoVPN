"""Currency-neutral money formatting and fixed-rate payment conversion."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Mapping

from database.requests import get_base_currency, get_currency_rate, list_currency_rates

BASE_CURRENCIES = frozenset({'RUB', 'USD'})
CURRENCY_EXPONENTS: Mapping[str, int] = {
    'RUB': 2,
    'USD': 2,
    'USDT': 2,
    'XTR': 0,
}
CURRENCY_SYMBOLS: Mapping[str, str] = {
    'RUB': '₽',
    'USD': '$',
    'USDT': 'USDT',
    'XTR': '⭐',
}

_BUILTIN_PAYMENT_CURRENCIES: Mapping[str, str] = {
    'balance': 'BASE',
    'cards': 'RUB',
    'cardlink': 'RUB',
    'crypto': 'USDT',
    'demo': 'RUB',
    'platega': 'RUB',
    'promo_free': 'BASE',
    'stars': 'XTR',
    'wata': 'RUB',
    'yookassa_qr': 'RUB',
}


def normalize_currency(value: object, *, base_only: bool = False) -> str:
    """Validates a supported currency code."""
    currency = str(value or '').strip().upper()
    allowed = BASE_CURRENCIES if base_only else CURRENCY_EXPONENTS
    if currency not in allowed:
        raise ValueError(f'Unsupported currency: {currency or value!r}')
    return currency


def parse_major_to_minor(value: object, currency: str | None = None) -> int:
    """Parses a non-negative major-unit value into integer minor units."""
    code = normalize_currency(currency or get_base_currency(), base_only=False)
    try:
        amount = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Amount must be a decimal number') from error
    if not amount.is_finite() or amount < 0:
        raise ValueError('Amount must be non-negative')
    exponent = CURRENCY_EXPONENTS[code]
    scale = Decimal(10) ** exponent
    scaled = amount * scale
    if scaled != scaled.to_integral_value():
        raise ValueError(f'{code} supports at most {exponent} decimal places')
    return int(scaled)


def minor_to_decimal(amount_minor: object, currency: str) -> Decimal:
    """Returns a Decimal major-unit amount."""
    code = normalize_currency(currency)
    scale = Decimal(10) ** CURRENCY_EXPONENTS[code]
    return Decimal(int(amount_minor or 0)) / scale


def format_money_minor(
    amount_minor: object,
    currency: str | None = None,
    *,
    compact: bool = True,
) -> str:
    """Formats minor units for Russian Telegram UI without redundant zeroes."""
    code = normalize_currency(currency or get_base_currency())
    value = minor_to_decimal(amount_minor, code)
    decimals = CURRENCY_EXPONENTS[code]
    rendered = f'{value:.{decimals}f}'
    if compact and '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    rendered = rendered.replace('.', ',')
    if code == 'USD':
        return f'${rendered}'
    if code == 'RUB':
        return f'{rendered} ₽'
    if code == 'XTR':
        return f'{rendered} ⭐'
    return f'{rendered} {code}'


def payment_type_currency(payment_type: str, *, base_currency: str | None = None) -> str:
    """Returns the native charge currency for a built-in or custom provider."""
    payment = str(payment_type or '').strip().casefold()
    builtin = _BUILTIN_PAYMENT_CURRENCIES.get(payment)
    if builtin:
        return (
            normalize_currency(base_currency or get_base_currency(), base_only=True)
            if builtin == 'BASE'
            else builtin
        )
    if payment.startswith('ext_'):
        try:
            from bot.utils.payment_provider_registry import get_payment_provider_by_type

            provider = get_payment_provider_by_type(payment)
        except (ImportError, ValueError):
            provider = None
        if provider is not None:
            return normalize_currency(provider.currency)
    return normalize_currency(base_currency or get_base_currency(), base_only=True)


def get_payment_rate_snapshot(
    *,
    base_currency: str | None = None,
) -> dict[str, Any]:
    """Returns immutable target-units-per-base inputs for a new quote."""
    base = normalize_currency(base_currency or get_base_currency(), base_only=True)
    rates = list_currency_rates(base_currency=base)
    return {
        'base_currency': base,
        'rate_direction': 'target_units_per_base',
        'rates': {key: _decimal_text(_positive_decimal(value)) for key, value in rates.items()},
    }


def base_minor_to_charge_units(
    amount_minor: object,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> tuple[int, str]:
    """Converts base minor units to provider minor units using ceiling rounding."""
    amount = max(0, int(amount_minor or 0))
    snapshot = dict(rate_snapshot or get_payment_rate_snapshot())
    base = normalize_currency(snapshot.get('base_currency') or get_base_currency(), base_only=True)
    target = payment_type_currency(payment_type, base_currency=base)
    rate = _rate_from_snapshot(snapshot, base, target)
    base_major = minor_to_decimal(amount, base)
    target_scale = Decimal(10) ** CURRENCY_EXPONENTS[target]
    units = (base_major * rate * target_scale).to_integral_value(rounding=ROUND_CEILING)
    return int(units), target


def charge_units_to_base_minor(
    amount_units: object,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> int:
    """Converts provider minor units back into base minor units."""
    units = max(0, int(amount_units or 0))
    snapshot = dict(rate_snapshot or get_payment_rate_snapshot())
    base = normalize_currency(snapshot.get('base_currency') or get_base_currency(), base_only=True)
    target = payment_type_currency(payment_type, base_currency=base)
    rate = _rate_from_snapshot(snapshot, base, target)
    target_major = minor_to_decimal(units, target)
    base_scale = Decimal(10) ** CURRENCY_EXPONENTS[base]
    value = (target_major / rate) * base_scale
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def convert_base_minor(
    amount_minor: object,
    target_currency: str,
    *,
    base_currency: str | None = None,
    rounding: str = 'half_up',
) -> int:
    """Converts base minor units to another supported currency's minor units."""
    base = normalize_currency(base_currency or get_base_currency(), base_only=True)
    target = normalize_currency(target_currency)
    raw_rate = get_currency_rate(target, base_currency=base)
    if raw_rate is None:
        raise ValueError(f'Rate {base} → {target} is not configured')
    base_major = minor_to_decimal(amount_minor, base)
    target_scale = Decimal(10) ** CURRENCY_EXPONENTS[target]
    mode = ROUND_CEILING if rounding == 'ceiling' else ROUND_HALF_UP
    return int((base_major * _positive_decimal(raw_rate) * target_scale).to_integral_value(rounding=mode))


def _rate_from_snapshot(snapshot: Mapping[str, Any], base: str, target: str) -> Decimal:
    if base == target:
        return Decimal('1')
    rates = snapshot.get('rates')
    if isinstance(rates, Mapping) and target in rates:
        return _positive_decimal(rates[target])

    # Compatibility with v77 RUB-per-provider-unit snapshots.
    if base == 'RUB' and target == 'USDT' and snapshot.get('stablecoin_rub_rate'):
        return Decimal('1') / _positive_decimal(snapshot['stablecoin_rub_rate'])
    if base == 'RUB' and target == 'XTR' and snapshot.get('star_rub_rate'):
        return Decimal('1') / _positive_decimal(snapshot['star_rub_rate'])

    raw = get_currency_rate(target, base_currency=base)
    if raw is None:
        raise ValueError(f'Rate {base} → {target} is not configured')
    return _positive_decimal(raw)


def _positive_decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Rate must be a positive decimal') from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError('Rate must be a positive decimal')
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


__all__ = [
    'BASE_CURRENCIES',
    'CURRENCY_EXPONENTS',
    'base_minor_to_charge_units',
    'charge_units_to_base_minor',
    'convert_base_minor',
    'format_money_minor',
    'get_payment_rate_snapshot',
    'minor_to_decimal',
    'normalize_currency',
    'parse_major_to_minor',
    'payment_type_currency',
]
