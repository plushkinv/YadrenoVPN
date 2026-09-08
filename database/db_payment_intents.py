"""Persistent storage for core-owned payment intents and fulfillment effects."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional

from .connection import get_db
from .payment_order_ids import build_payment_order_id


def create_payment_intent_record(
    *,
    user_id: int,
    purpose: str,
    purpose_data: Mapping[str, Any],
    nominal_amount_minor: int,
    base_currency: str = 'RUB',
    description: str,
    success_target: Mapping[str, Any],
    cancel_target: Mapping[str, Any],
    tariff_id: int | None = None,
    vpn_key_id: int | None = None,
    period_days: int | None = None,
    origin_context: Mapping[str, Any] | None = None,
    origin_context_token: str | None = None,
) -> tuple[int, str]:
    """Creates an unquoted core payment intent and returns its id/order_id."""
    payload = _json_object(purpose_data)
    success = _json_object(success_target)
    cancel = _json_object(cancel_target)
    amount = _non_negative_int(nominal_amount_minor, 'nominal_amount_minor')
    currency = str(base_currency or 'RUB').upper()
    if currency not in {'RUB', 'USD'}:
        raise ValueError('base_currency must be RUB or USD')
    if origin_context is not None and origin_context_token is not None:
        raise ValueError('pass origin_context or origin_context_token, not both')

    with get_db() as conn:
        normalized_origin = _normalize_payment_origin_context(origin_context)
        if origin_context_token is not None:
            from .db_action_contexts import consume_semantic_action_context_with_conn

            consumed_origin = consume_semantic_action_context_with_conn(
                conn,
                origin_context_token,
                user_id=int(user_id),
                action='key.purchase.start',
            )
            if consumed_origin is None:
                raise ValueError('origin context token is unavailable or expired')
            normalized_origin = _normalize_payment_origin_context(consumed_origin)
        cursor = conn.execute(
            """
            INSERT INTO payments (
                user_id, tariff_id, order_id, payment_type, vpn_key_id,
                period_days, status, paid_at,
                intent_version, purpose, purpose_data_json,
                base_currency, nominal_amount_minor, payable_amount_minor,
                description, success_target_json, cancel_target_json,
                origin_extension_id, origin_context_version, origin_context_json,
                origin_workflow_id, origin_completion_handler,
                fulfillment_status, created_at
            )
            VALUES (
                ?, ?, 'pending', NULL, ?,
                ?, 'pending', NULL,
                1, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP
            )
            """,
            (
                int(user_id),
                tariff_id,
                vpn_key_id,
                period_days,
                str(purpose),
                payload,
                currency,
                amount,
                amount,
                str(description or ''),
                success,
                cancel,
                (
                    normalized_origin['owner_extension_id']
                    if normalized_origin is not None
                    else None
                ),
                (
                    normalized_origin['schema_version']
                    if normalized_origin is not None
                    else None
                ),
                _json_object(
                    normalized_origin['payload']
                    if normalized_origin is not None
                    else {}
                ),
                (
                    normalized_origin['workflow_id']
                    if normalized_origin is not None
                    else None
                ),
                (
                    normalized_origin['completion_handler']
                    if normalized_origin is not None
                    else None
                ),
            ),
        )
        payment_id = int(cursor.lastrowid)
        order_id = build_payment_order_id(payment_id)
        conn.execute(
            "UPDATE payments SET order_id = ? WHERE id = ?",
            (order_id, payment_id),
        )
        if origin_context_token is not None:
            from .db_action_contexts import attach_semantic_action_context_order_with_conn

            if not attach_semantic_action_context_order_with_conn(
                conn,
                origin_context_token,
                order_id=order_id,
            ):
                raise RuntimeError('consumed origin context could not be linked to its order')
        return payment_id, order_id


def get_payment_intent(order_id: str) -> Optional[dict[str, Any]]:
    """Returns one payment intent with decoded JSON fields."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.*, t.name AS tariff_name, t.duration_days,
                   t.price_minor AS tariff_price_minor
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
    payable_amount_minor: int,
    charge_amount: str,
    charge_currency: str,
    rate_snapshot: Mapping[str, Any],
) -> bool:
    """Persists a provider quote without changing an already settled intent."""
    payable = _non_negative_int(payable_amount_minor, 'payable_amount_minor')
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET payment_type = ?,
                payable_amount_minor = ?,
                charge_amount = ?,
                charge_currency = ?,
                rate_snapshot_json = ?
            WHERE order_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND fulfillment_status IN ('pending', 'failed')
            """,
            (
                str(payment_type),
                payable,
                str(charge_amount),
                str(charge_currency).upper(),
                _json_object(rate_snapshot),
                str(order_id),
            ),
        )
        return cursor.rowcount > 0


def cancel_unconfirmed_payment_for_method_change(
    order_id: str,
    *,
    user_id: int,
) -> bool:
    """Cancel one unconfirmed v1 intent and its active provider tracking."""
    normalized_order_id = str(order_id)
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'canceled'
            WHERE order_id = ?
              AND user_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND provider_confirmed_at IS NULL
            """,
            (normalized_order_id, int(user_id)),
        )
        if cursor.rowcount <= 0:
            return False
        conn.execute(
            """
            UPDATE payment_provider_orders
            SET status = 'canceled',
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ?
              AND status = 'pending'
            """,
            (normalized_order_id,),
        )
        conn.execute(
            """
            UPDATE payment_auto_checks
            SET state = 'canceled',
                next_check_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ?
              AND state IN ('active', 'exhausted', 'completion_failed')
            """,
            (normalized_order_id,),
        )
        conn.execute(
            """
            UPDATE promo_redemptions
            SET status = 'canceled'
            WHERE order_id = ?
              AND status = 'reserved'
            """,
            (normalized_order_id,),
        )
        return True


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


def confirm_internal_payment_intent_settlement(
    order_id: str,
    *,
    payment_type: str,
    balance_deduct_minor: int | None = None,
    rate_snapshot: Mapping[str, Any] | None = None,
) -> bool:
    """Durably settle a validated zero-payable free or full-balance intent."""
    normalized_type = str(payment_type or '').strip().casefold()
    if normalized_type not in {'promo_free', 'balance'}:
        raise ValueError('payment_type must be promo_free or balance')
    balance_deduct = _non_negative_int(
        balance_deduct_minor or 0,
        'balance_deduct_minor',
    )
    rate_snapshot_json = _json_object(rate_snapshot or {})
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET payment_type = ?,
                balance_deduct_minor = CASE
                    WHEN ? = 'balance' THEN ? ELSE balance_deduct_minor
                END,
                payable_amount_minor = CASE
                    WHEN ? = 'balance' THEN 0 ELSE payable_amount_minor
                END,
                charge_amount = CASE
                    WHEN ? = 'balance' THEN '0' ELSE charge_amount
                END,
                charge_currency = CASE
                    WHEN ? = 'balance' THEN base_currency ELSE charge_currency
                END,
                rate_snapshot_json = CASE
                    WHEN ? = 'balance' THEN ? ELSE rate_snapshot_json
                END,
                provider_confirmed_at = COALESCE(
                    provider_confirmed_at,
                    CURRENT_TIMESTAMP
                ),
                fulfillment_status = 'provider_succeeded',
                fulfillment_last_error = NULL
            WHERE order_id = ?
              AND status = 'pending'
              AND intent_version = 1
              AND (
                    provider_confirmed_at IS NULL
                    OR payment_type = ?
              )
              AND fulfillment_status IN ('pending', 'failed', 'provider_succeeded')
              AND (
                    ? <> 'balance'
                    OR EXISTS (
                        SELECT 1
                        FROM users u
                        WHERE u.id = payments.user_id
                          AND COALESCE(u.personal_balance, 0) >= ? + COALESCE((
                                SELECT SUM(COALESCE(reserved.balance_deduct_minor, 0))
                                FROM payments reserved
                                WHERE reserved.user_id = payments.user_id
                                  AND reserved.order_id <> payments.order_id
                                  AND reserved.status = 'pending'
                                  AND reserved.provider_confirmed_at IS NOT NULL
                                  AND reserved.fulfillment_status IN (
                                        'provider_succeeded', 'failed', 'processing'
                                  )
                                  AND COALESCE(reserved.balance_deduct_minor, 0) > 0
                                  AND NOT EXISTS (
                                        SELECT 1
                                        FROM payment_effects effect
                                        WHERE effect.order_id = reserved.order_id
                                          AND effect.effect_name = 'balance_debit'
                                          AND effect.status = 'completed'
                                  )
                          ), 0)
                    )
              )
              AND (
                    (
                        ? = 'promo_free'
                        AND COALESCE(payable_amount_minor, 0) = 0
                    )
                    OR (
                        ? = 'balance'
                        AND purpose <> 'balance_topup'
                        AND ? > 0
                        AND COALESCE(payable_amount_minor, 0) > 0
                        AND ? >= COALESCE(payable_amount_minor, 0)
                    )
              )
            """,
            (
                normalized_type,
                normalized_type,
                balance_deduct,
                normalized_type,
                normalized_type,
                normalized_type,
                normalized_type,
                rate_snapshot_json,
                str(order_id),
                normalized_type,
                normalized_type,
                balance_deduct,
                normalized_type,
                normalized_type,
                balance_deduct,
                balance_deduct,
            ),
        )
        if cursor.rowcount > 0:
            return True
        row = conn.execute(
            """
            SELECT status, fulfillment_status, payment_type,
                   provider_confirmed_at,
                   payable_amount_minor, balance_deduct_minor
            FROM payments
            WHERE order_id = ? AND intent_version = 1
            """,
            (str(order_id),),
        ).fetchone()
        if row is None or str(row['payment_type'] or '') != normalized_type:
            return False
        stored_payable = int(row['payable_amount_minor'] or 0)
        stored_balance_deduct = int(row['balance_deduct_minor'] or 0)
        same_snapshot = bool(
            stored_payable == 0
            and (
                normalized_type == 'promo_free'
                or (
                    balance_deduct > 0
                    and stored_balance_deduct == balance_deduct
                )
            )
        )
        if not same_snapshot:
            return False
        return bool(
            (
                str(row['status'] or '') == 'paid'
                and str(row['fulfillment_status'] or '') == 'completed'
            )
            or (
                str(row['status'] or '') == 'pending'
                and str(row['fulfillment_status'] or '') == 'provider_succeeded'
                and row['provider_confirmed_at'] is not None
            )
        )


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


def complete_payment_fulfillment(order_id: str, *, event_subscribers=()) -> bool:
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
        if cursor.rowcount > 0:
            from .db_core_events import record_core_event_with_conn

            record_core_event_with_conn(
                conn, event_name='payment.completed', source_id=str(order_id),
                order_id=str(order_id), subscribers=event_subscribers,
            )
            return True
        return False


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


def get_retryable_confirmed_payment_intents(
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return settled v1 intents whose durable core fulfillment is incomplete."""
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 10
    normalized_limit = max(1, min(normalized_limit, 100))

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.order_id, p.payment_type, p.fulfillment_status,
                   p.fulfillment_started_at, p.fulfillment_attempts,
                   p.provider_confirmed_at, ppo.provider_id
            FROM payments p
            LEFT JOIN payment_provider_orders ppo ON ppo.order_id = p.order_id
            WHERE p.intent_version = 1
              AND p.status = 'pending'
              AND p.provider_confirmed_at IS NOT NULL
              AND p.fulfillment_status IN (
                    'provider_succeeded', 'failed', 'processing'
              )
              AND (
                    ppo.status = 'succeeded'
                    OR (
                        ppo.id IS NULL
                        AND p.payment_type IN ('promo_free', 'balance')
                    )
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM payment_auto_checks pac
                    WHERE pac.order_id = p.order_id
                      AND pac.state IN ('active', 'provider_succeeded')
              )
            ORDER BY p.provider_confirmed_at ASC, p.id ASC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()
        return [dict(row) for row in rows]


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
            "SELECT id, system_type FROM tariffs WHERE id = ?",
            (int(tariff_id),),
        ).fetchone()
        owner = conn.execute("SELECT id FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if (
            not tariff
            or not owner
            or tariff['system_type'] is not None
        ):
            return {'ok': False, 'reason': 'owner_or_tariff_not_found'}

        from .db_keys import _create_initial_vpn_key_with_conn

        key_id = _create_initial_vpn_key_with_conn(
            conn,
            int(user_id),
            int(tariff_id),
            int(days),
            int(traffic_limit_bytes),
        )
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
            SELECT vk.id, vk.traffic_limit, vk.traffic_used,
                   t.group_id AS tariff_group_id
            FROM vpn_keys vk
            JOIN tariffs t ON t.id = vk.tariff_id
            WHERE vk.id = ? AND vk.user_id = ?
            """,
            (int(key_id), int(user_id)),
        ).fetchone()
        tariff = conn.execute(
            """
            SELECT id, group_id, system_type
            FROM tariffs WHERE id = ?
            """,
            (int(tariff_id),),
        ).fetchone()
        if (
            not key
            or not tariff
            or tariff['system_type'] is not None
            or int(key['tariff_group_id']) != int(tariff['group_id'])
        ):
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
            SET expires_at = CASE
                    WHEN ? = 0 THEN NULL
                    ELSE MAX(
                        datetime('now'),
                        datetime(
                            CASE WHEN expires_at > datetime('now')
                                THEN expires_at ELSE datetime('now') END,
                            ?
                        )
                    )
                END,
                tariff_id = ?,
                traffic_limit = ?,
                traffic_limit_override = NULL,
                max_ips_override = NULL,
                traffic_notified_pct = 100
            WHERE id = ? AND user_id = ?
            """,
            (
                int(days),
                modifier,
                int(tariff_id),
                new_limit,
                int(key_id),
                int(user_id),
            ),
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
    amount_minor: int,
    currency: str = 'RUB',
) -> dict[str, Any]:
    """Credits one nominal top-up atomically with its unique history/effect rows."""
    amount = _non_negative_int(amount_minor, 'amount_minor')
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
    data['nominal_amount_minor'] = int(data.get('nominal_amount_minor') or 0)
    data['payable_amount_minor'] = int(data.get('payable_amount_minor') or 0)
    data['balance_deduct_minor'] = int(data.get('balance_deduct_minor') or 0)
    for source, target in (
        ('purpose_data_json', 'purpose_data'),
        ('rate_snapshot_json', 'rate_snapshot'),
        ('success_target_json', 'success_target'),
        ('cancel_target_json', 'cancel_target'),
        ('origin_context_json', 'origin_context_payload'),
    ):
        raw = data.get(source)
        try:
            decoded = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            decoded = {}
        data[target] = decoded if isinstance(decoded, dict) else {}
    if data.get('origin_extension_id'):
        data['origin_context'] = {
            'owner_extension_id': str(data['origin_extension_id']),
            'schema_version': int(data.get('origin_context_version') or 0),
            'payload': data.get('origin_context_payload') or {},
            'workflow_id': str(data.get('origin_workflow_id') or ''),
            'completion_handler': (
                str(data['origin_completion_handler'])
                if data.get('origin_completion_handler')
                else None
            ),
        }
    else:
        data['origin_context'] = None
    return data


def _normalize_payment_origin_context(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    from bot.utils.action_origin_context import normalize_stored_origin_context

    normalized = normalize_stored_origin_context(value)
    return normalized.as_storage_dict() if normalized is not None else None


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
    'cancel_unconfirmed_payment_for_method_change',
    'claim_payment_effect',
    'complete_payment_effect',
    'complete_payment_fulfillment',
    'confirm_internal_payment_intent_settlement',
    'create_payment_intent_record',
    'fail_payment_effect',
    'fail_payment_fulfillment',
    'fulfill_balance_topup_once',
    'fulfill_key_purchase_once',
    'fulfill_key_renewal_once',
    'get_payment_intent',
    'get_retryable_confirmed_payment_intents',
    'is_payment_effect_completed',
    'mark_payment_provider_confirmed',
    'prepare_failed_payment_fulfillment_retry',
    'record_payment_referral_stat_once',
    'recover_interrupted_payment_fulfillment',
    'update_payment_intent_purpose_data',
    'update_payment_intent_quote',
]
