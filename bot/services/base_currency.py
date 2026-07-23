"""Safe preview and execution workflow for global base-currency switches."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any

from database.requests import (
    create_bot_database_backup,
    execute_base_currency_switch_record,
    get_base_currency,
    preview_base_currency_switch,
)

_switch_lock = asyncio.Lock()


class BaseCurrencySwitchBlocked(RuntimeError):
    """Raised when confirmed payment fulfillment makes a switch unsafe."""


def normalize_transition_input(value: object) -> Decimal:
    """Validates the admin-entered old units per one new base unit."""
    try:
        rate = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Transition rate must be a positive decimal') from error
    if not rate.is_finite() or rate <= 0 or rate > Decimal('1000000'):
        raise ValueError('Transition rate must be a positive decimal')
    return rate


def build_base_currency_switch_preview(
    target_currency: str,
    old_units_per_new: object,
) -> dict[str, Any]:
    """Returns a preview using the admin-facing `1 NEW = X OLD` direction."""
    entered = normalize_transition_input(old_units_per_new)
    conversion = Decimal('1') / entered
    preview = preview_base_currency_switch(target_currency, conversion)
    preview['old_units_per_new'] = _decimal_text(entered)
    return preview


async def switch_base_currency(
    *,
    expected_from_currency: str,
    target_currency: str,
    old_units_per_new: object,
    admin_telegram_id: int,
) -> dict[str, Any]:
    """Creates a verified backup and applies one globally serialized switch."""
    entered = normalize_transition_input(old_units_per_new)
    conversion = Decimal('1') / entered
    async with _switch_lock:
        if get_base_currency() != str(expected_from_currency).upper():
            raise RuntimeError('Base currency changed while confirmation was open')
        preview = preview_base_currency_switch(target_currency, conversion)
        if int(preview.get('blocking_intents') or 0) > 0:
            raise BaseCurrencySwitchBlocked(
                'Confirmed payments must be fulfilled before switching currency'
            )
        backup_path = await asyncio.to_thread(create_bot_database_backup)
        result = await asyncio.to_thread(
            execute_base_currency_switch_record,
            expected_from_currency=str(expected_from_currency).upper(),
            target_currency=str(target_currency).upper(),
            to_units_per_from=conversion,
            from_units_per_to=entered,
            admin_telegram_id=int(admin_telegram_id),
            backup_path=backup_path,
        )
        result['old_units_per_new'] = _decimal_text(entered)
        return result


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


__all__ = [
    'BaseCurrencySwitchBlocked',
    'build_base_currency_switch_preview',
    'normalize_transition_input',
    'switch_base_currency',
]
