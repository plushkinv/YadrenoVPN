"""Compatibility facade for fixed administrator-managed payment rates."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Mapping

from database.requests import get_setting

from bot.services.money import (
    base_minor_to_charge_units,
    charge_units_to_base_minor,
    get_payment_rate_snapshot as _generic_rate_snapshot,
)

logger = logging.getLogger(__name__)

DEFAULT_STABLECOIN_RUB_RATE = Decimal('100')
DEFAULT_STAR_RUB_RATE = Decimal('1.3')


def get_positive_decimal_setting(key: str, default: Decimal) -> Decimal:
    """Reads a positive decimal setting without float arithmetic."""
    raw = get_setting(key, _decimal_text(default))
    try:
        value = Decimal(str(raw).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        logger.error("Invalid positive decimal setting %s=%r", key, raw)
        return default
    if not value.is_finite() or value <= 0:
        logger.error("Non-positive decimal setting %s=%r", key, raw)
        return default
    return value


def get_payment_rate_snapshot() -> dict[str, Any]:
    """Returns the generic quote snapshot plus v77 RUB compatibility aliases."""
    snapshot = _generic_rate_snapshot()
    if snapshot['base_currency'] == 'RUB':
        rates = snapshot.get('rates', {})
        try:
            snapshot['stablecoin_rub_rate'] = _decimal_text(
                Decimal('1') / Decimal(str(rates['USDT']))
            )
            snapshot['star_rub_rate'] = _decimal_text(
                Decimal('1') / Decimal(str(rates['XTR']))
            )
        except (KeyError, InvalidOperation, ZeroDivisionError):
            snapshot['stablecoin_rub_rate'] = _decimal_text(
                DEFAULT_STABLECOIN_RUB_RATE
            )
            snapshot['star_rub_rate'] = _decimal_text(DEFAULT_STAR_RUB_RATE)
    return snapshot


def provider_amount_from_base_minor(
    amount_minor: int,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> tuple[int, str]:
    """Converts base minor units into provider minor units."""
    return base_minor_to_charge_units(amount_minor, payment_type, rate_snapshot)


def provider_units_to_base_minor(
    amount: int,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> int:
    """Converts provider minor units back into the snapshotted base currency."""
    return charge_units_to_base_minor(amount, payment_type, rate_snapshot)


def provider_amount_from_rub_cents(
    rub_cents: int,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> tuple[int, str]:
    """Deprecated alias; the integer now represents current base minor units."""
    return provider_amount_from_base_minor(rub_cents, payment_type, rate_snapshot)


def provider_units_to_rub_cents(
    amount: int,
    payment_type: str,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> int:
    """Deprecated alias; the result is current/snapshotted base minor units."""
    return provider_units_to_base_minor(amount, payment_type, rate_snapshot)


async def get_usd_rub_rate() -> int:
    """Returns the configured RUB value of one USDT in kopecks for legacy code."""
    snapshot = get_payment_rate_snapshot()
    rates = snapshot.get('rates', {})
    base = snapshot.get('base_currency')
    try:
        if base == 'RUB':
            rub_per_usdt = Decimal('1') / Decimal(str(rates['USDT']))
        else:
            rub_per_usdt = Decimal(str(rates['RUB'])) / Decimal(str(rates['USDT']))
    except (KeyError, InvalidOperation, ZeroDivisionError):
        rub_per_usdt = DEFAULT_STABLECOIN_RUB_RATE
    return int(
        (rub_per_usdt * Decimal('100')).to_integral_value(rounding=ROUND_CEILING)
    )


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


__all__ = [
    'DEFAULT_STABLECOIN_RUB_RATE',
    'DEFAULT_STAR_RUB_RATE',
    'get_payment_rate_snapshot',
    'get_positive_decimal_setting',
    'get_usd_rub_rate',
    'provider_amount_from_base_minor',
    'provider_amount_from_rub_cents',
    'provider_units_to_base_minor',
    'provider_units_to_rub_cents',
]
