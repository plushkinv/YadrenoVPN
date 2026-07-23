"""Persistent storage for core-owned payment intents and fulfillment effects."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional

from .connection import get_db
from .db_payments import _int_to_base62


def create_payment_intent_record(
    *,
    user_id: int,
    purpose: str,
    purpose_data: Mapping[str, Any],
    nominal_amount_minor: int | None = None,
    nominal_amount_cents: int | None = None,
    base_currency: str = 'RUB',
    description: str,
    success_target: Mapping[str, Any],
    cancel_target: Mapping[str, Any],
    tariff_id: int | None = None,
    vpn_key_id: int | None = None,
    period_days: int | None = None,
) -> tuple[int, str]:
    """Creates an unquoted core payment intent and returns its id/order_id."""
    payload = _json_object(purpose_data)
    success = _json_object(success_target)
    cancel = _json_object(cancel_target)
    raw_amount = nominal_amount_minor if nominal_amount_minor is not None else nominal_amount_cents
    amount = _non_negative_int(raw_amount, 'nominal_amount_minor')
    currency = str(base_currency or 'RUB').upper()
    if currency not in {'RUB', 'USD'}:
        raise ValueError('base_currency must be RUB or USD')

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO payments (
                user_id, tariff_id, order_id, payment_type, vpn_key_id,
                amount_cents, amount_stars, period_days, status, paid_at,
                intent_version, purpose, purpose_data_json,
                nominal_amount_cents, payable_amount_cents,
                base_currency, nominal_amount_minor, payable_amount_minor,
                description, success_target_json, cancel_target_json,
                fulfillment_status, created_at
            )
            VALUES (
                ?, ?, 'pending', NULL, ?,
                ?, 0, ?, 'pending', NULL,
                1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP
            )
            """,
            (
                int(user_id),
                tariff_id,
                vpn_key_id,
                amount,
                period_days,
                str(purpose),
                payload,
                amount,
                amount,
                currency,
                amount,
                amount,
                str(description or ''),
                success,
                cancel,
            ),
        )
        payment_id = int(cursor.lastrowid)
        order_id = '00' + _int_to_base62(payment_id)
        conn.execute(
            "UPDATE payments SET order_id = ? WHERE id = ?",
            (order_id, payment_id),
        )
        return payment_id, order_id


def get_payment_intent(order_id: str) -> Optional[dict[str, Any]]:
    """Returns one payment intent with decoded JSON fields."""
    with get_db() as conn:
        try:
            row = conn.execute(
                """
                SELECT p.*, t.name AS tariff_name, t.duration_days,
                       t.price_rub AS tariff_price_rub,
                       t.price_minor AS tariff_price_minor
                FROM payments p
                LEFT JOIN tariffs t ON t.id = p.tariff_id
                WHERE p.order_id = ?
                """,
                (str(order_id),),
            ).fetchone()
        except sqlite3.OperationalError as error:
            if 'price_minor' not in str(error):
                raise
            row = conn.execute(
                """
                SELECT p.*, t.name AS tariff_name, t.duration_days,
                       t.price_rub AS tariff_price_rub
                FROM payments p
                LEFT JOIN tariffs t ON t.id = p.tariff_id
                WHERE p.order_id = ?
                """,
                (str(order_id),),
            ).fetchone()
    return _decode_intent(row)


def update_payment_intent_quote(
    order_id: str,
    *,
    payment_type: str,
    payable_amount_minor: int | None = None,
    payable_amount_cents: int | None = None,
    charge_amount: str,
    charge_currency: str,
    rate_snapshot: Mapping[str, Any],
    compatibility_amount_cents: int = 0,
    compatibility_amount_stars: int = 0,
) -> bool:
    """Persists a provider quote without changing an already settled intent."""
    raw_payable = payable_amount_minor if payable_amount_minor is not None else payable_amount_cents
    payable = _non_negative_int(raw_payable, 'payable_amount_minor')
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET payment_type = ?,
                payable_amount_cents = ?,
                payable_amount_minor = ?,
                charge_amount = ?,
                charge_currency = ?,
                rate_snapshot_json = ?,
                amount_cents = ?,
                amount_stars = ?,
                final_amount_cents = ?,
                final_amount_stars = ?
            WHERE order_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND fulfillment_status IN ('pending', 'failed')
            """,
            (
                str(payment_type),
                payable,
                payable,
                str(charge_amount),
                str(charge_currency).upper(),
                _json_object(rate_snapshot),
                _non_negative_int(compatibility_amount_cents, 'compatibility_amount_cents'),
                _non_negative_int(compatibility_amount_stars, 'compatibility_amount_stars'),
                _non_negative_int(compatibility_amount_cents, 'compatibility_amount_cents'),
                _non_negative_int(compatibility_amount_stars, 'compatibility_amount_stars'),
                str(order_id),
            ),
        )
        return cursor.rowcount > 0


def update_payment_intent_purpose_data(
    order_id: str,
    purpose_data: Mapping[str, Any],
    *,
    vpn_key_id: int | None = None,
) -> bool:
    """Stores trusted fulfillment output in the core purpose payload."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET purpose_data_json = ?,
                vpn_key_id = COALESCE(?, vpn_key_id)
            WHERE order_id = ? AND intent_version = 1
            """,
            (_json_object(purpose_data), vpn_key_id, str(order_id)),
        )
        return cursor.rowcount > 0


def mark_payment_provider_confirmed(order_id: str) -> bool:
    """Persists provider settlement while leaving the order pending."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET provider_confirmed_at = COALESCE(provider_confirmed_at, CURRENT_TIMESTAMP),
                fulfillment_status = 'provider_succeeded',
                fulfillment_last_error = NULL
            WHERE order_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND fulfillment_status IN ('pending', 'failed', 'provider_succeeded')
            """,
            (str(order_id),),
        )
        return cursor.rowcount > 0


def begin_payment_fulfillment(order_id: str) -> bool:
    """Atomically claims a provider-confirmed intent for one dispatcher."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET fulfillment_status = 'processing',
                fulfillment_attempts = fulfillment_attempts + 1,
                fulfillment_started_at = CURRENT_TIMESTAMP,
                fulfillment_last_error = NULL
            WHERE order_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND fulfillment_status = 'provider_succeeded'
            """,
            (str(order_id),),
        )
        return cursor.rowcount > 0


def complete_payment_fulfillment(order_id: str) -> bool:
    """Marks the core order paid only after all required fulfillment succeeds."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'paid',
                paid_at = COALESCE(paid_at, CURRENT_TIMESTAMP),
                fulfillment_status = 'completed',
                fulfillment_started_at = NULL,
                fulfilled_at = COALESCE(fulfilled_at, CURRENT_TIMESTAMP),
                fulfillment_last_error = NULL
            WHERE order_id = ?
              AND intent_version = 1
              AND status = 'pending'
              AND fulfillment_status = 'processing'
            """,
            (str(order_id),),
        )
        return cursor.rowcount > 0


def fail_payment_fulfillment(order_id: str, error: str) -> bool:
    """Keeps a settled provider payment retryable after a core failure."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET fulfillment_status = 'failed',
                fulfillment_started_at = NULL,
                fulfillment_last_error = ?
            WHERE order_id = ?
              AND intent_version = 1
              AND status = 'pending'
              AND fulfillment_status = 'processing'
            """,
            (str(error)[:2000], str(order_id)),
        )
        return cursor.rowcount > 0


def prepare_failed_payment_fulfillment_retry(order_id: str) -> bool:
    """Moves a failed settled intent back to the dispatcher-ready state."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET fulfillment_status = 'provider_succeeded',
                fulfillment_started_at = NULL
            WHERE order_id = ?
              AND intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NOT NULL
              AND fulfillment_status = 'failed'
            """,
            (str(order_id),),
        )
        return cursor.rowcount > 0


def recover_interrupted_payment_fulfillment(
    order_id: str,
    *,
    stale_after_seconds: int = 120,
) -> bool:
    """Releases a stale dispatcher claim and its unfinished effect claims."""
    stale_seconds = max(1, int(stale_after_seconds))
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET fulfillment_status = 'provider_succeeded',
                fulfillment_started_at = NULL,
                fulfillment_last_error = 'Recovered interrupted fulfillment'
            WHERE order_id = ?
              AND intent_version = 1
              AND status = 'pending'
              AND provider_confirmed_at IS NOT NULL
              AND fulfillment_status = 'processing'
              AND (
                    fulfillment_started_at IS NULL
                    OR fulfillment_started_at <= datetime('now', '-' || ? || ' seconds')
              )
            """,
            (str(order_id), stale_seconds),
        )
        recovered = cursor.rowcount > 0
        if recovered:
            conn.execute(
                """
                UPDATE payment_effects
                SET status = 'failed',
                    last_error = 'Recovered interrupted fulfillment',
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ? AND status = 'started'
                """,
                (str(order_id),),
            )
        return recovered


def claim_payment_effect(order_id: str, effect_name: str) -> bool:
    """Claims one idempotent fulfillment effect, retrying only failed effects."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO payment_effects (order_id, effect_name)
            VALUES (?, ?)
            ON CONFLICT(order_id, effect_name) DO UPDATE SET
                status = 'started',
                attempts = payment_effects.attempts + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE payment_effects.status = 'failed'
            """,
            (str(order_id), str(effect_name)),
        )
        return cursor.rowcount > 0


def complete_payment_effect(
    order_id: str,
    effect_name: str,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Marks a claimed fulfillment effect complete."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payment_effects
            SET status = 'completed',
                metadata_json = ?,
                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE order_id = ? AND effect_name = ? AND status = 'started'
            """,
            (_json_object(metadata or {}), str(order_id), str(effect_name)),
        )
        return cursor.rowcount > 0


def fail_payment_effect(order_id: str, effect_name: str, error: str) -> bool:
    """Marks a claimed effect retryable."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payment_effects
            SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ? AND effect_name = ? AND status = 'started'
            """,
            (str(error)[:2000], str(order_id), str(effect_name)),
        )
        return cursor.rowcount > 0


def is_payment_effect_completed(order_id: str, effect_name: str) -> bool:
    """Checks whether an idempotent fulfillment effect already completed."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM payment_effects
            WHERE order_id = ? AND effect_name = ? AND status = 'completed'
            """,
            (str(order_id), str(effect_name)),
        ).fetchone()
        return row is not None


def record_payment_referral_stat_once(
    order_id: str,
    *,
    level: int,
    referrer_id: int,
    payer_id: int,
    reward_cents: int | None = None,
    reward_minor: int | None = None,
    reward_days: int,
    reward_currency: str = 'RUB',
) -> bool:
    """Updates referral aggregates once for one intent and referral level."""
    reward = max(0, int(reward_minor if reward_minor is not None else reward_cents or 0))
    currency = str(reward_currency or 'RUB').upper()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO payment_referral_effects (
                order_id, level, referrer_id, payer_id,
                reward_cents, reward_minor, reward_currency, reward_days
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(order_id),
                int(level),
                int(referrer_id),
                int(payer_id),
                reward,
                reward,
                currency,
                max(0, int(reward_days)),
            ),
        )
        if cursor.rowcount <= 0:
            return False
        conn.execute(
            """
            INSERT INTO referral_stats (
                referrer_id, referral_id, level,
                total_payments_count, total_reward_cents,
                total_reward_minor, reward_currency, total_reward_days
            )
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(referrer_id, referral_id, level) DO UPDATE SET
                total_payments_count = total_payments_count + 1,
                total_reward_cents = total_reward_cents + excluded.total_reward_cents,
                total_reward_minor = total_reward_minor + excluded.total_reward_minor,
                reward_currency = excluded.reward_currency,
                total_reward_days = total_reward_days + excluded.total_reward_days
            """,
            (
                int(referrer_id),
                int(payer_id),
                int(level),
                reward,
                reward,
                currency,
                max(0, int(reward_days)),
            ),
        )
        return True


def fulfill_key_purchase_once(
    order_id: str,
    *,
    user_id: int,
    tariff_id: int,
    days: int,
    traffic_limit_bytes: int,
) -> dict[str, Any]:
    """Creates and links one draft key in the same transaction as its effect marker."""
    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT p.vpn_key_id
            FROM payments p
            JOIN payment_effects e ON e.order_id = p.order_id
            WHERE p.order_id = ? AND e.effect_name = 'purpose'
              AND e.status = 'completed'
            """,
            (str(order_id),),
        ).fetchone()
        if existing and existing['vpn_key_id']:
            return {'ok': True, 'already_applied': True, 'key_id': int(existing['vpn_key_id'])}

        tariff = conn.execute(
            "SELECT id FROM tariffs WHERE id = ?",
            (int(tariff_id),),
        ).fetchone()
        owner = conn.execute("SELECT id FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not tariff or not owner:
            return {'ok': False, 'reason': 'owner_or_tariff_not_found'}

        cursor = conn.execute(
            """
            INSERT INTO vpn_keys
                (user_id, tariff_id, expires_at, created_at, traffic_limit)
            VALUES (?, ?, datetime('now', '+' || ? || ' days'), CURRENT_TIMESTAMP, ?)
            """,
            (int(user_id), int(tariff_id), int(days), int(traffic_limit_bytes)),
        )
        key_id = int(cursor.lastrowid)
        payload = {'tariff_id': int(tariff_id), 'key_id': key_id}
        conn.execute(
            """
            UPDATE payments
            SET vpn_key_id = ?, purpose_data_json = ?
            WHERE order_id = ? AND intent_version = 1
            """,
            (key_id, _json_object(payload), str(order_id)),
        )
        _complete_effect_in_connection(
            conn,
            order_id,
            'purpose',
            {'key_id': key_id, 'operation': 'key_purchase'},
        )
        return {'ok': True, 'already_applied': False, 'key_id': key_id}


def fulfill_key_renewal_once(
    order_id: str,
    *,
    user_id: int,
    key_id: int,
    tariff_id: int,
    days: int,
    traffic_limit_bytes: int,
) -> dict[str, Any]:
    """Extends one owned key exactly once and records the effect atomically."""
    with get_db() as conn:
        effect = conn.execute(
            """
            SELECT status FROM payment_effects
            WHERE order_id = ? AND effect_name = 'purpose'
            """,
            (str(order_id),),
        ).fetchone()
        if effect and effect['status'] == 'completed':
            return {'ok': True, 'already_applied': True, 'key_id': int(key_id)}

        key = conn.execute(
            """
            SELECT id, traffic_limit, traffic_used
            FROM vpn_keys WHERE id = ? AND user_id = ?
            """,
            (int(key_id), int(user_id)),
        ).fetchone()
        tariff = conn.execute("SELECT id FROM tariffs WHERE id = ?", (int(tariff_id),)).fetchone()
        if not key or not tariff:
            return {'ok': False, 'reason': 'owned_key_or_tariff_not_found'}

        modifier = f"{int(days):+} days"
        current_limit = int(key['traffic_limit'] or 0)
        current_used = int(key['traffic_used'] or 0)
        purchased_limit = max(0, int(traffic_limit_bytes or 0))
        if purchased_limit <= 0:
            new_limit = 0
        elif current_limit <= 0:
            new_limit = current_used + purchased_limit
        else:
            new_limit = current_limit + purchased_limit

        cursor = conn.execute(
            """
            UPDATE vpn_keys
            SET expires_at = MAX(
                    datetime('now'),
                    datetime(
                        CASE WHEN expires_at > datetime('now')
                            THEN expires_at ELSE datetime('now') END,
                        ?
                    )
                ),
                tariff_id = ?,
                traffic_limit = ?,
                traffic_notified_pct = 100
            WHERE id = ? AND user_id = ?
            """,
            (modifier, int(tariff_id), new_limit, int(key_id), int(user_id)),
        )
        if cursor.rowcount <= 0:
            return {'ok': False, 'reason': 'key_update_failed'}
        _complete_effect_in_connection(
            conn,
            order_id,
            'purpose',
            {'key_id': int(key_id), 'operation': 'key_renewal', 'days': int(days)},
        )
        return {'ok': True, 'already_applied': False, 'key_id': int(key_id)}


def fulfill_balance_topup_once(
    order_id: str,
    *,
    user_id: int,
    amount_minor: int | None = None,
    amount_cents: int | None = None,
    currency: str = 'RUB',
) -> dict[str, Any]:
    """Credits one nominal top-up atomically with its unique history/effect rows."""
    amount = _non_negative_int(
        amount_minor if amount_minor is not None else amount_cents,
        'amount_minor',
    )
    operation_currency = str(currency or 'RUB').upper()
    if amount <= 0:
        return {'ok': False, 'reason': 'amount_must_be_positive'}

    with get_db() as conn:
        effect = conn.execute(
            """
            SELECT status, metadata_json FROM payment_effects
            WHERE order_id = ? AND effect_name = 'purpose'
            """,
            (str(order_id),),
        ).fetchone()
        if effect and effect['status'] == 'completed':
            return {'ok': True, 'already_applied': True, 'credited_amount_minor': amount, 'credited_amount_cents': amount}

        user = conn.execute(
            "SELECT personal_balance FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
        if not user:
            return {'ok': False, 'reason': 'user_not_found'}
        before = int(user['personal_balance'] or 0)
        after = before + amount

        existing_credit = conn.execute(
            """
            SELECT id FROM balance_operations
            WHERE reference_type = 'payment_topup' AND reference_id = ?
            """,
            (str(order_id),),
        ).fetchone()
        if existing_credit:
            _complete_effect_in_connection(
                conn,
                order_id,
                'purpose',
                {'credited_amount_cents': amount, 'operation_id': int(existing_credit['id'])},
            )
            return {'ok': True, 'already_applied': True, 'credited_amount_minor': amount, 'credited_amount_cents': amount}

        conn.execute(
            "UPDATE users SET personal_balance = ? WHERE id = ?",
            (after, int(user_id)),
        )
        operation = conn.execute(
            """
            INSERT INTO balance_operations (
                user_id, operation_type, delta_cents, delta_minor, currency,
                balance_before, balance_after, source, reason,
                reference_type, reference_id, metadata
            )
            VALUES (?, 'credit', ?, ?, ?, ?, ?, 'payment_topup', ?,
                    'payment_topup', ?, ?)
            """,
            (
                int(user_id),
                amount,
                amount,
                operation_currency,
                before,
                after,
                'Пополнение баланса по оплаченному счёту',
                str(order_id),
                _json_object({
                    'base_currency': operation_currency,
                    'nominal_amount_minor': amount,
                    'nominal_amount_cents': amount,
                }),
            ),
        )
        _complete_effect_in_connection(
            conn,
            order_id,
            'purpose',
            {'credited_amount_minor': amount, 'credited_amount_cents': amount, 'operation_id': int(operation.lastrowid)},
        )
        return {
            'ok': True,
            'already_applied': False,
            'credited_amount_cents': amount,
            'credited_amount_minor': amount,
            'balance_after': after,
        }


def _complete_effect_in_connection(
    conn: sqlite3.Connection,
    order_id: str,
    effect_name: str,
    metadata: Mapping[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO payment_effects (
            order_id, effect_name, status, metadata_json,
            completed_at, updated_at
        )
        VALUES (?, ?, 'completed', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(order_id, effect_name) DO UPDATE SET
            status = 'completed',
            metadata_json = excluded.metadata_json,
            completed_at = COALESCE(payment_effects.completed_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP,
            last_error = NULL
        """,
        (str(order_id), str(effect_name), _json_object(metadata)),
    )


def _decode_intent(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data['base_currency'] = str(data.get('base_currency') or 'RUB').upper()
    data['nominal_amount_minor'] = int(
        data.get('nominal_amount_minor') or data.get('nominal_amount_cents') or 0
    )
    data['payable_amount_minor'] = int(
        data.get('payable_amount_minor') or data.get('payable_amount_cents') or 0
    )
    data['balance_deduct_minor'] = int(
        data.get('balance_deduct_minor') or data.get('balance_deduct_cents') or 0
    )
    for source, target in (
        ('purpose_data_json', 'purpose_data'),
        ('rate_snapshot_json', 'rate_snapshot'),
        ('success_target_json', 'success_target'),
        ('cancel_target_json', 'cancel_target'),
    ):
        raw = data.get(source)
        try:
            decoded = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            decoded = {}
        data[target] = decoded if isinstance(decoded, dict) else {}
    return data


def _json_object(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError('payment intent JSON value must be an object')
    return json.dumps(dict(value), ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a non-negative integer')
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f'{field} must be a non-negative integer')
    return parsed


__all__ = [
    'begin_payment_fulfillment',
    'claim_payment_effect',
    'complete_payment_effect',
    'complete_payment_fulfillment',
    'create_payment_intent_record',
    'fail_payment_effect',
    'fail_payment_fulfillment',
    'fulfill_balance_topup_once',
    'fulfill_key_purchase_once',
    'fulfill_key_renewal_once',
    'get_payment_intent',
    'is_payment_effect_completed',
    'mark_payment_provider_confirmed',
    'prepare_failed_payment_fulfillment_retry',
    'record_payment_referral_stat_once',
    'recover_interrupted_payment_fulfillment',
    'update_payment_intent_purpose_data',
    'update_payment_intent_quote',
]
