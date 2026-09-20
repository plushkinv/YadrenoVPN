"""Persistence for automatic coupons sent after a user lets all keys lapse."""

from __future__ import annotations

import datetime
import logging
import secrets
from typing import Any, Dict, List, Optional

from .connection import get_db
from .db_promotions import BASE62_ALPHABET

logger = logging.getLogger(__name__)

COUPON_LAPSED_ENABLED_KEY = "coupon_lapsed_enabled"
COUPON_LAPSED_DISCOUNT_KEY = "coupon_lapsed_discount_percent"
COUPON_LAPSED_LIFETIME_KEY = "coupon_lapsed_lifetime_days"
COUPON_LAPSED_DELAY_KEY = "coupon_lapsed_delay_days"
COUPON_LAPSED_ENABLED_SINCE_KEY = "coupon_lapsed_enabled_since"

_ACTIVE_OR_UNKNOWN_KEY_SQL = """
    candidate.expires_at IS NULL
    OR TRIM(CAST(candidate.expires_at AS TEXT)) = ''
    OR datetime(candidate.expires_at) IS NULL
    OR datetime(candidate.expires_at) > datetime('now')
"""
_ACTIVE_OR_UNKNOWN_GROUP_KEY_SQL = _ACTIVE_OR_UNKNOWN_KEY_SQL.replace(
    "candidate.",
    "vk.",
)

__all__ = [
    "COUPON_LAPSED_ENABLED_KEY",
    "COUPON_LAPSED_DISCOUNT_KEY",
    "COUPON_LAPSED_LIFETIME_KEY",
    "COUPON_LAPSED_DELAY_KEY",
    "COUPON_LAPSED_ENABLED_SINCE_KEY",
    "get_lapsed_coupon_enabled",
    "set_lapsed_coupon_enabled",
    "get_lapsed_coupon_discount_percent",
    "set_lapsed_coupon_discount_percent",
    "get_lapsed_coupon_lifetime_days",
    "set_lapsed_coupon_lifetime_days",
    "get_lapsed_coupon_delay_days",
    "set_lapsed_coupon_delay_days",
    "get_lapsed_coupon_enabled_since",
    "get_purchase_auto_coupon_statistics",
    "get_lapsed_coupon_statistics",
    "discover_lapsed_coupon_episodes",
    "cancel_ineligible_lapsed_coupon_deliveries",
    "list_due_lapsed_coupon_deliveries",
    "ensure_lapsed_coupon_for_delivery",
    "mark_lapsed_coupon_delivery_sent",
    "mark_lapsed_coupon_delivery_retry",
    "mark_lapsed_coupon_delivery_failed",
    "mark_lapsed_coupon_delivery_unavailable",
    "get_lapsed_coupon_delivery",
]


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow().replace(microsecond=0)


def _format_dt(value: datetime.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _setting_value(conn, key: str, default: str) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"] if row and row["value"] is not None else default)


def _upsert_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _validated_percent(value: Any, default: int = 10) -> int:
    try:
        percent = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return percent if 0 <= percent <= 100 else default


def _validated_positive(value: Any, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _validated_delay(value: Any, default: int = 7) -> int:
    delay = _validated_positive(value, default)
    return delay if 1 <= delay <= 30 else default


def get_lapsed_coupon_enabled() -> bool:
    with get_db() as conn:
        return (
            _setting_value(conn, COUPON_LAPSED_ENABLED_KEY, "0")
            == "1"
        )


def _cancel_pending_deliveries(conn, reason: str) -> int:
    conn.execute(
        """
        UPDATE promo_codes
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE usage_count = 0
          AND id IN (
              SELECT coupon_id
              FROM lapsed_coupon_deliveries
              WHERE status = 'pending'
                AND coupon_id IS NOT NULL
          )
        """
    )
    cursor = conn.execute(
        """
        UPDATE lapsed_coupon_deliveries
        SET status = 'canceled',
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
        """,
        (reason[:1000],),
    )
    return max(0, int(cursor.rowcount or 0))


def set_lapsed_coupon_enabled(enabled: bool) -> None:
    """Toggle the feature and start every enabled window without a backlog."""
    target = bool(enabled)
    with get_db() as conn:
        current = (
            _setting_value(conn, COUPON_LAPSED_ENABLED_KEY, "0")
            == "1"
        )
        if current == target:
            return

        if target:
            _cancel_pending_deliveries(conn, "feature_reenabled")
            _upsert_setting(
                conn,
                COUPON_LAPSED_ENABLED_SINCE_KEY,
                _format_dt(_utcnow()),
            )
            _upsert_setting(conn, COUPON_LAPSED_ENABLED_KEY, "1")
        else:
            _cancel_pending_deliveries(conn, "feature_disabled")
            _upsert_setting(conn, COUPON_LAPSED_ENABLED_KEY, "0")


def get_lapsed_coupon_discount_percent() -> int:
    with get_db() as conn:
        return _validated_percent(
            _setting_value(conn, COUPON_LAPSED_DISCOUNT_KEY, "10"),
            10,
        )


def set_lapsed_coupon_discount_percent(discount_percent: int) -> None:
    percent = int(discount_percent)
    if not 0 <= percent <= 100:
        raise ValueError("Discount must be between 0 and 100 percent")
    with get_db() as conn:
        _upsert_setting(conn, COUPON_LAPSED_DISCOUNT_KEY, str(percent))


def get_lapsed_coupon_lifetime_days() -> int:
    with get_db() as conn:
        return _validated_positive(
            _setting_value(conn, COUPON_LAPSED_LIFETIME_KEY, "90"),
            90,
        )


def set_lapsed_coupon_lifetime_days(days: int) -> None:
    lifetime = int(days)
    if lifetime <= 0:
        raise ValueError("Coupon lifetime must be positive")
    with get_db() as conn:
        _upsert_setting(conn, COUPON_LAPSED_LIFETIME_KEY, str(lifetime))


def get_lapsed_coupon_delay_days() -> int:
    with get_db() as conn:
        return _validated_delay(
            _setting_value(conn, COUPON_LAPSED_DELAY_KEY, "7"),
            7,
        )


def set_lapsed_coupon_delay_days(days: int) -> None:
    delay = int(days)
    if not 1 <= delay <= 30:
        raise ValueError("Delivery delay must be between 1 and 30 days")
    with get_db() as conn:
        _upsert_setting(conn, COUPON_LAPSED_DELAY_KEY, str(delay))


def get_lapsed_coupon_enabled_since() -> Optional[str]:
    with get_db() as conn:
        value = _setting_value(
            conn,
            COUPON_LAPSED_ENABLED_SINCE_KEY,
            "",
        ).strip()
    return value or None


def _statistics(row: Any) -> Dict[str, Any]:
    issued = int(row["issued"] or 0)
    used = int(row["used"] or 0)
    return {
        "issued": issued,
        "used": used,
        "active": int(row["active"] or 0),
        "expired": int(row["expired"] or 0),
        "conversion_percent": round(used * 100 / issued, 1) if issued else 0.0,
    }


def get_purchase_auto_coupon_statistics() -> Dict[str, Any]:
    """Return all-time statistics for payment-triggered automatic coupons."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS issued,
                SUM(CASE WHEN usage_count > 0 THEN 1 ELSE 0 END) AS used,
                SUM(
                    CASE
                        WHEN usage_count = 0
                         AND is_active = 1
                         AND (expires_at IS NULL OR datetime(expires_at) > datetime('now'))
                        THEN 1 ELSE 0
                    END
                ) AS active,
                SUM(
                    CASE
                        WHEN usage_count = 0
                         AND expires_at IS NOT NULL
                         AND datetime(expires_at) <= datetime('now')
                        THEN 1 ELSE 0
                    END
                ) AS expired
            FROM promo_codes
            WHERE type = 'coupon'
              AND (source = 'auto' OR source LIKE 'auto_payment:%')
            """
        ).fetchone()
    return _statistics(row)


def get_lapsed_coupon_statistics() -> Dict[str, Any]:
    """Return delivered coupon statistics plus current delivery states."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS issued,
                SUM(CASE WHEN pc.usage_count > 0 THEN 1 ELSE 0 END) AS used,
                SUM(
                    CASE
                        WHEN pc.usage_count = 0
                         AND pc.is_active = 1
                         AND (
                             pc.expires_at IS NULL
                             OR datetime(pc.expires_at) > datetime('now')
                         )
                        THEN 1 ELSE 0
                    END
                ) AS active,
                SUM(
                    CASE
                        WHEN pc.usage_count = 0
                         AND pc.expires_at IS NOT NULL
                         AND datetime(pc.expires_at) <= datetime('now')
                        THEN 1 ELSE 0
                    END
                ) AS expired
            FROM lapsed_coupon_deliveries delivery
            JOIN promo_codes pc ON pc.id = delivery.coupon_id
            WHERE delivery.status = 'sent'
            """
        ).fetchone()
        states = {
            str(item["status"]): int(item["count"] or 0)
            for item in conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM lapsed_coupon_deliveries
                GROUP BY status
                """
            ).fetchall()
        }

    result = _statistics(row)
    result.update(
        {
            "waiting": states.get("pending", 0),
            "failed": states.get("failed", 0),
            "canceled": states.get("canceled", 0),
        }
    )
    return result


def discover_lapsed_coupon_episodes() -> int:
    """Persist newly lapsed users inside the current enabled window."""
    with get_db() as conn:
        if _setting_value(conn, COUPON_LAPSED_ENABLED_KEY, "0") != "1":
            return 0
        enabled_since = _setting_value(
            conn,
            COUPON_LAPSED_ENABLED_SINCE_KEY,
            "",
        ).strip()
        if not enabled_since:
            return 0

        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO lapsed_coupon_deliveries (
                user_id,
                lapse_token,
                lapsed_at,
                status
            )
            SELECT
                u.id,
                MAX(datetime(vk.expires_at)),
                MAX(datetime(vk.expires_at)),
                'pending'
            FROM users u
            JOIN vpn_keys vk ON vk.user_id = u.id
            WHERE u.is_banned = 0
              AND u.is_bot_blocked = 0
            GROUP BY u.id
            HAVING MAX(datetime(vk.expires_at)) IS NOT NULL
               AND MAX(datetime(vk.expires_at)) >= datetime(?)
               AND SUM(
                    CASE
                        WHEN {_ACTIVE_OR_UNKNOWN_GROUP_KEY_SQL}
                        THEN 1 ELSE 0
                    END
               ) = 0
            """,
            (enabled_since,),
        )
        return max(0, int(cursor.rowcount or 0))


def _ineligible_pending_ids(conn) -> List[int]:
    rows = conn.execute(
        f"""
        SELECT delivery.id
        FROM lapsed_coupon_deliveries delivery
        JOIN users u ON u.id = delivery.user_id
        WHERE delivery.status = 'pending'
          AND (
              u.is_banned = 1
              OR u.is_bot_blocked = 1
              OR EXISTS (
                  SELECT 1
                  FROM vpn_keys candidate
                  WHERE candidate.user_id = delivery.user_id
                    AND ({_ACTIVE_OR_UNKNOWN_KEY_SQL})
              )
          )
        """
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _cancel_delivery_ids(conn, delivery_ids: List[int], reason: str) -> int:
    if not delivery_ids:
        return 0
    placeholders = ",".join("?" for _ in delivery_ids)
    conn.execute(
        f"""
        UPDATE promo_codes
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE usage_count = 0
          AND id IN (
              SELECT coupon_id
              FROM lapsed_coupon_deliveries
              WHERE id IN ({placeholders})
                AND coupon_id IS NOT NULL
          )
        """,
        delivery_ids,
    )
    cursor = conn.execute(
        f"""
        UPDATE lapsed_coupon_deliveries
        SET status = 'canceled',
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
          AND id IN ({placeholders})
        """,
        (reason[:1000], *delivery_ids),
    )
    return max(0, int(cursor.rowcount or 0))


def cancel_ineligible_lapsed_coupon_deliveries() -> int:
    """Cancel pending episodes when the user becomes active or unavailable."""
    with get_db() as conn:
        return _cancel_delivery_ids(
            conn,
            _ineligible_pending_ids(conn),
            "recipient_no_longer_eligible",
        )


def list_due_lapsed_coupon_deliveries(
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return pending episodes whose configured delay has fully elapsed."""
    sql = f"""
        SELECT
            delivery.*,
            u.telegram_id
        FROM lapsed_coupon_deliveries delivery
        JOIN users u ON u.id = delivery.user_id
        WHERE delivery.status = 'pending'
          AND u.is_banned = 0
          AND u.is_bot_blocked = 0
          AND datetime(delivery.lapsed_at) <= datetime(
              'now',
              '-' || ? || ' days'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM vpn_keys candidate
              WHERE candidate.user_id = delivery.user_id
                AND ({_ACTIVE_OR_UNKNOWN_KEY_SQL})
          )
        ORDER BY delivery.lapsed_at ASC, delivery.id ASC
    """
    params: List[Any] = [get_lapsed_coupon_delay_days()]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(limit)))
    with get_db() as conn:
        return [
            dict(row)
            for row in conn.execute(sql, tuple(params)).fetchall()
        ]


def _generate_unique_code(conn, length: int = 10) -> str:
    for _ in range(100):
        code = "".join(
            secrets.choice(BASE62_ALPHABET)
            for _ in range(length)
        )
        exists = conn.execute(
            "SELECT 1 FROM promo_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not exists:
            return code
    raise RuntimeError("Unable to generate a unique lapsed-user coupon")


def _delivery_coupon_row(conn, delivery_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT
            delivery.id AS delivery_id,
            delivery.user_id,
            delivery.lapse_token,
            delivery.lapsed_at,
            delivery.status AS delivery_status,
            delivery.attempts,
            u.telegram_id,
            pc.*
        FROM lapsed_coupon_deliveries delivery
        JOIN users u ON u.id = delivery.user_id
        LEFT JOIN promo_codes pc ON pc.id = delivery.coupon_id
        WHERE delivery.id = ?
        """,
        (delivery_id,),
    ).fetchone()
    return dict(row) if row else None


def ensure_lapsed_coupon_for_delivery(
    delivery_id: int,
) -> Optional[Dict[str, Any]]:
    """Atomically recheck one due episode and create its stable coupon."""
    delivery_id = int(delivery_id)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _setting_value(conn, COUPON_LAPSED_ENABLED_KEY, "0") != "1":
            return None
        enabled_since = _setting_value(
            conn,
            COUPON_LAPSED_ENABLED_SINCE_KEY,
            "",
        ).strip()
        delay_days = _validated_delay(
            _setting_value(conn, COUPON_LAPSED_DELAY_KEY, "7"),
            7,
        )
        row = conn.execute(
            f"""
            SELECT delivery.*, u.telegram_id
            FROM lapsed_coupon_deliveries delivery
            JOIN users u ON u.id = delivery.user_id
            WHERE delivery.id = ?
              AND delivery.status = 'pending'
              AND u.is_banned = 0
              AND u.is_bot_blocked = 0
              AND datetime(delivery.lapsed_at) >= datetime(?)
              AND datetime(delivery.lapsed_at) <= datetime(
                  'now',
                  '-' || ? || ' days'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM vpn_keys candidate
                  WHERE candidate.user_id = delivery.user_id
                    AND ({_ACTIVE_OR_UNKNOWN_KEY_SQL})
              )
            """,
            (delivery_id, enabled_since, delay_days),
        ).fetchone()
        if not row:
            return None

        if row["coupon_id"] is not None:
            coupon = _delivery_coupon_row(conn, delivery_id)
            if not coupon:
                return None
            unavailable = (
                int(coupon.get("usage_count") or 0) > 0
                or not bool(coupon.get("is_active"))
                or (
                    coupon.get("expires_at")
                    and conn.execute(
                        "SELECT datetime(?) <= datetime('now')",
                        (coupon["expires_at"],),
                    ).fetchone()[0]
                )
            )
            if unavailable:
                conn.execute(
                    """
                    UPDATE lapsed_coupon_deliveries
                    SET status = 'failed',
                        last_error = 'coupon_expired_or_unavailable',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (delivery_id,),
                )
                return None
            return coupon

        source = f"auto_lapsed:{delivery_id}"
        existing = conn.execute(
            "SELECT id FROM promo_codes WHERE source = ?",
            (source,),
        ).fetchone()
        if existing:
            coupon_id = int(existing["id"])
        else:
            discount = _validated_percent(
                _setting_value(conn, COUPON_LAPSED_DISCOUNT_KEY, "10"),
                10,
            )
            lifetime = _validated_positive(
                _setting_value(conn, COUPON_LAPSED_LIFETIME_KEY, "90"),
                90,
            )
            generated_at = _utcnow()
            expires_at = generated_at + datetime.timedelta(days=lifetime)
            cursor = conn.execute(
                """
                INSERT INTO promo_codes (
                    type,
                    code,
                    discount_percent,
                    expires_at,
                    is_active,
                    activation_limit,
                    source,
                    issued_to_user_id,
                    snapshot_discount_percent,
                    snapshot_lifetime_days,
                    snapshot_generated_at
                )
                VALUES (
                    'coupon', ?, ?, ?, 1, 1, ?, ?, ?, ?, ?
                )
                """,
                (
                    _generate_unique_code(conn),
                    discount,
                    _format_dt(expires_at),
                    source,
                    int(row["user_id"]),
                    discount,
                    lifetime,
                    _format_dt(generated_at),
                ),
            )
            coupon_id = int(cursor.lastrowid)

        conn.execute(
            """
            UPDATE lapsed_coupon_deliveries
            SET coupon_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (coupon_id, delivery_id),
        )
        return _delivery_coupon_row(conn, delivery_id)


def mark_lapsed_coupon_delivery_sent(
    delivery_id: int,
    *,
    attempts: int = 1,
) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE lapsed_coupon_deliveries
            SET status = 'sent',
                attempts = attempts + ?,
                last_error = NULL,
                sent_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (max(0, int(attempts)), int(delivery_id)),
        )
        return cursor.rowcount > 0


def mark_lapsed_coupon_delivery_retry(
    delivery_id: int,
    error: str,
    *,
    attempts: int = 1,
) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE lapsed_coupon_deliveries
            SET attempts = attempts + ?,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (
                max(0, int(attempts)),
                str(error)[:1000],
                int(delivery_id),
            ),
        )
        return cursor.rowcount > 0


def mark_lapsed_coupon_delivery_unavailable(delivery_id: int) -> bool:
    """End an impossible Telegram delivery without revoking the earned coupon."""
    with get_db() as conn:
        return conn.execute(
            "UPDATE lapsed_coupon_deliveries SET status = 'failed', last_error = 'not_sent:no_telegram', "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (int(delivery_id),),
        ).rowcount > 0


def mark_lapsed_coupon_delivery_failed(
    delivery_id: int,
    error: str,
    *,
    attempts: int = 0,
) -> bool:
    delivery_id = int(delivery_id)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE promo_codes
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE usage_count = 0
              AND id = (
                  SELECT coupon_id
                  FROM lapsed_coupon_deliveries
                  WHERE id = ?
              )
            """,
            (delivery_id,),
        )
        cursor = conn.execute(
            """
            UPDATE lapsed_coupon_deliveries
            SET status = 'failed',
                attempts = attempts + ?,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (
                max(0, int(attempts)),
                str(error)[:1000],
                delivery_id,
            ),
        )
        return cursor.rowcount > 0


def get_lapsed_coupon_delivery(
    delivery_id: int,
) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        return _delivery_coupon_row(conn, int(delivery_id))
