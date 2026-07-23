"""Database access for base-currency settings, rates and atomic switches."""
from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .connection import get_db

SUPPORTED_BASE_CURRENCIES = frozenset({'RUB', 'USD'})
SUPPORTED_PAYMENT_CURRENCIES = frozenset({'RUB', 'USD', 'USDT', 'XTR'})

__all__ = [
    'SUPPORTED_BASE_CURRENCIES',
    'SUPPORTED_PAYMENT_CURRENCIES',
    'execute_base_currency_switch_record',
    'get_base_currency',
    'get_currency_rate',
    'list_currency_rates',
    'preview_base_currency_switch',
    'set_currency_rate',
]


def _currency(value: object, *, base_only: bool = False) -> str:
    currency = str(value or '').strip().upper()
    allowed = SUPPORTED_BASE_CURRENCIES if base_only else SUPPORTED_PAYMENT_CURRENCIES
    if currency not in allowed:
        raise ValueError(f'Unsupported currency: {currency or value!r}')
    return currency


def _positive_decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Rate must be a positive decimal') from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError('Rate must be a positive decimal')
    return parsed


def _decimal_text(value: Decimal) -> str:
    quantum = Decimal('0.000000000000000001')
    if abs(value) >= quantum:
        try:
            value = value.quantize(quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            pass
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


def _convert_minor(amount: object, rate: Decimal) -> int:
    value = Decimal(max(0, int(amount or 0))) * rate
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def get_base_currency() -> str:
    """Returns the current global base currency, defaulting safely to RUB."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'base_currency'"
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    value = str(row['value'] if row else 'RUB').upper()
    return value if value in SUPPORTED_BASE_CURRENCIES else 'RUB'


def get_currency_rate(
    target_currency: str,
    *,
    base_currency: str | None = None,
) -> str | None:
    """Returns target units per one base unit as a normalized Decimal string."""
    base = _currency(base_currency or get_base_currency(), base_only=True)
    target = _currency(target_currency)
    if base == target:
        return '1'
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT units_per_base
                FROM currency_rates
                WHERE base_currency = ? AND target_currency = ?
                """,
                (base, target),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    return str(row['units_per_base']) if row else None


def list_currency_rates(*, base_currency: str | None = None) -> dict[str, str]:
    """Returns all configured target-per-base rates for one base currency."""
    base = _currency(base_currency or get_base_currency(), base_only=True)
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT target_currency, units_per_base
                FROM currency_rates
                WHERE base_currency = ?
                ORDER BY target_currency
                """,
                (base,),
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    rates = {str(row['target_currency']): str(row['units_per_base']) for row in rows}
    rates[base] = '1'
    return rates


def set_currency_rate(
    target_currency: str,
    units_per_base: object,
    *,
    base_currency: str | None = None,
) -> str:
    """Stores one fixed target-per-base rate and returns its normalized value."""
    base = _currency(base_currency or get_base_currency(), base_only=True)
    target = _currency(target_currency)
    rate = Decimal('1') if base == target else _positive_decimal(units_per_base)
    normalized = _decimal_text(rate)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO currency_rates (
                base_currency, target_currency, units_per_base, updated_at
            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(base_currency, target_currency) DO UPDATE SET
                units_per_base = excluded.units_per_base,
                updated_at = CURRENT_TIMESTAMP
            """,
            (base, target, normalized),
        )
        if base == 'RUB' and target in {'USDT', 'XTR'}:
            legacy_key = (
                'stablecoin_rub_rate' if target == 'USDT' else 'star_rub_rate'
            )
            legacy_value = _decimal_text(Decimal('1') / rate)
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (legacy_key, legacy_value),
            )
        if target == 'RUB':
            _refresh_legacy_tariff_rub_prices(conn, base, rate)
    return normalized


def preview_base_currency_switch(
    target_currency: str,
    to_units_per_from: object,
) -> dict[str, Any]:
    """Builds a read-only conversion preview for a base-currency switch."""
    target = _currency(target_currency, base_only=True)
    source = get_base_currency()
    if target == source:
        raise ValueError('Target currency is already active')
    rate = _positive_decimal(to_units_per_from)
    with get_db() as conn:
        tariffs = [
            {
                'id': int(row['id']),
                'name': str(row['name']),
                'before_minor': int(row['price_minor'] or 0),
                'after_minor': _convert_minor(row['price_minor'], rate),
            }
            for row in conn.execute(
                "SELECT id, name, price_minor FROM tariffs ORDER BY display_order, id"
            ).fetchall()
        ]
        balance_rows = conn.execute(
            "SELECT personal_balance FROM users WHERE personal_balance != 0"
        ).fetchall()
        referral_rows = conn.execute(
            "SELECT total_reward_minor FROM referral_stats WHERE total_reward_minor != 0"
        ).fetchall()
        blocking = conn.execute(
            """
            SELECT COUNT(*) AS row_count
            FROM payments
            WHERE intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NOT NULL
            """
        ).fetchone()
        cancelable = conn.execute(
            """
            SELECT COUNT(*) AS row_count
            FROM payments
            WHERE intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NULL
            """
        ).fetchone()
    balance_total = sum(int(row['personal_balance'] or 0) for row in balance_rows)
    referral_total = sum(int(row['total_reward_minor'] or 0) for row in referral_rows)
    return {
        'from_currency': source,
        'to_currency': target,
        'to_units_per_from': _decimal_text(rate),
        'tariffs': tariffs,
        'tariff_count': len(tariffs),
        'balance_count': len(balance_rows),
        'balance_before_minor': balance_total,
        'balance_after_minor': sum(
            _convert_minor(row['personal_balance'], rate) for row in balance_rows
        ),
        'referral_count': len(referral_rows),
        'referral_before_minor': referral_total,
        'referral_after_minor': sum(
            _convert_minor(row['total_reward_minor'], rate) for row in referral_rows
        ),
        'cancelable_intents': int(cancelable['row_count'] or 0),
        'blocking_intents': int(blocking['row_count'] or 0),
    }


def execute_base_currency_switch_record(
    *,
    expected_from_currency: str,
    target_currency: str,
    to_units_per_from: object,
    from_units_per_to: object | None = None,
    admin_telegram_id: int,
    backup_path: str,
) -> dict[str, Any]:
    """Atomically converts current state, rates and pending unconfirmed intents."""
    source = _currency(expected_from_currency, base_only=True)
    target = _currency(target_currency, base_only=True)
    if source == target:
        raise ValueError('Target currency is already active')
    rate = _positive_decimal(to_units_per_from)
    inverse_rate = (
        _positive_decimal(from_units_per_to)
        if from_units_per_to is not None
        else Decimal('1') / rate
    )
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'base_currency'"
        ).fetchone()
        actual = str(row['value'] if row else 'RUB').upper()
        if actual != source:
            raise RuntimeError('Base currency changed while confirmation was open')
        blocking = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM payments
            WHERE intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NOT NULL
            """
        ).fetchone()[0])
        if blocking:
            raise RuntimeError('Confirmed payments must be fulfilled before switching currency')

        cancel_rows = conn.execute(
            """
            SELECT order_id
            FROM payments
            WHERE intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NULL
            """
        ).fetchall()
        canceled_order_ids = [str(row['order_id']) for row in cancel_rows]
        if canceled_order_ids:
            placeholders = ','.join('?' for _ in canceled_order_ids)
            conn.execute(
                f"UPDATE payments SET status = 'canceled' WHERE order_id IN ({placeholders})",
                canceled_order_ids,
            )
            conn.execute(
                f"UPDATE payment_provider_orders SET status = 'canceled', "
                f"updated_at = CURRENT_TIMESTAMP WHERE order_id IN ({placeholders})",
                canceled_order_ids,
            )
            conn.execute(
                f"UPDATE promo_redemptions SET status = 'canceled' "
                f"WHERE status = 'reserved' AND order_id IN ({placeholders})",
                canceled_order_ids,
            )

        tariff_rows = conn.execute("SELECT id, price_minor FROM tariffs").fetchall()
        for row in tariff_rows:
            conn.execute(
                "UPDATE tariffs SET price_minor = ? WHERE id = ?",
                (_convert_minor(row['price_minor'], rate), int(row['id'])),
            )
        balance_rows = conn.execute(
            "SELECT id, personal_balance FROM users WHERE personal_balance != 0"
        ).fetchall()
        for row in balance_rows:
            conn.execute(
                "UPDATE users SET personal_balance = ? WHERE id = ?",
                (_convert_minor(row['personal_balance'], rate), int(row['id'])),
            )
        referral_rows = conn.execute(
            "SELECT id, total_reward_minor FROM referral_stats"
        ).fetchall()
        for row in referral_rows:
            converted_reward = _convert_minor(row['total_reward_minor'], rate)
            conn.execute(
                """
                UPDATE referral_stats
                SET total_reward_minor = ?, total_reward_cents = ?, reward_currency = ?
                WHERE id = ?
                """,
                (
                    converted_reward,
                    converted_reward,
                    target,
                    int(row['id']),
                ),
            )

        old_rates = {
            str(row['target_currency']): _positive_decimal(row['units_per_base'])
            for row in conn.execute(
                """
                SELECT target_currency, units_per_base
                FROM currency_rates WHERE base_currency = ?
                """,
                (source,),
            ).fetchall()
        }
        old_rates[source] = Decimal('1')
        new_rates: dict[str, Decimal] = {}
        for quote_currency in SUPPORTED_PAYMENT_CURRENCIES:
            if quote_currency == target:
                continue
            old_rate = old_rates.get(quote_currency)
            if old_rate is not None:
                # Use the explicitly entered inverse transition rate when it is
                # available. Apart from avoiding recurring Decimal tails, this
                # preserves the exact relationship shown in the confirmation UI.
                new_rates[quote_currency] = old_rate * inverse_rate
        new_rates[source] = inverse_rate
        for quote_currency, converted_rate in new_rates.items():
            conn.execute(
                """
                INSERT INTO currency_rates (
                    base_currency, target_currency, units_per_base, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(base_currency, target_currency) DO UPDATE SET
                    units_per_base = excluded.units_per_base,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (target, quote_currency, _decimal_text(converted_rate)),
            )
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('base_currency', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (target,),
        )
        rub_rate = Decimal('1') if target == 'RUB' else new_rates.get('RUB')
        if rub_rate is not None:
            _refresh_legacy_tariff_rub_prices(conn, target, rub_rate)
        if target == 'RUB':
            for quote_currency, legacy_key in (
                ('USDT', 'stablecoin_rub_rate'),
                ('XTR', 'star_rub_rate'),
            ):
                quote_rate = new_rates.get(quote_currency)
                if quote_rate is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (legacy_key, _decimal_text(Decimal('1') / quote_rate)),
                )
        cursor = conn.execute(
            """
            INSERT INTO base_currency_switches (
                from_currency, to_currency, to_units_per_from,
                admin_telegram_id, backup_path, converted_tariffs,
                converted_balances, converted_referral_rows, canceled_intents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                target,
                _decimal_text(rate),
                int(admin_telegram_id),
                str(backup_path),
                len(tariff_rows),
                len(balance_rows),
                len(referral_rows),
                len(canceled_order_ids),
            ),
        )
    return {
        'switch_id': int(cursor.lastrowid),
        'from_currency': source,
        'to_currency': target,
        'to_units_per_from': _decimal_text(rate),
        'converted_tariffs': len(tariff_rows),
        'converted_balances': len(balance_rows),
        'converted_referral_rows': len(referral_rows),
        'canceled_intents': len(canceled_order_ids),
        'backup_path': str(backup_path),
    }


def _refresh_legacy_tariff_rub_prices(
    conn: Any,
    base_currency: str,
    rub_units_per_base: Decimal,
) -> None:
    """Keeps the deprecated price_rub column usable by legacy provider handlers."""
    rows = conn.execute("SELECT id, price_minor FROM tariffs").fetchall()
    for row in rows:
        base_major = Decimal(int(row['price_minor'] or 0)) / Decimal('100')
        rub_major = base_major if base_currency == 'RUB' else base_major * rub_units_per_base
        conn.execute(
            "UPDATE tariffs SET price_rub = ? WHERE id = ?",
            (_decimal_text(rub_major), int(row['id'])),
        )
