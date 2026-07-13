import datetime
import logging
import re
import secrets
from typing import Any, Dict, List, Optional

from .connection import get_db
from .db_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE62_RE = re.compile(r"^[0-9A-Za-z]+$")

COUPON_AUTO_ENABLED_KEY = "coupon_auto_enabled"
COUPON_AUTO_DISCOUNT_KEY = "coupon_auto_discount_percent"
COUPON_AUTO_LIFETIME_KEY = "coupon_auto_lifetime_days"


__all__ = [
    "BASE62_ALPHABET",
    "is_base62_code",
    "generate_unique_promo_code",
    "create_promo_code",
    "create_coupon_batch",
    "create_auto_coupon_for_user",
    "get_promo_code_by_id",
    "get_promo_code_by_code",
    "get_promo_codes",
    "get_promo_code_availability",
    "has_available_promo_codes",
    "update_promo_code",
    "set_promo_code_active",
    "set_user_active_promo_code",
    "clear_user_active_promo_code",
    "get_user_active_promo_code",
    "record_promo_link_visit",
    "save_order_pricing_snapshot",
    "reserve_promo_for_order",
    "cancel_promo_reservation_for_order",
    "apply_promo_for_order",
    "get_coupon_auto_enabled",
    "set_coupon_auto_enabled",
    "get_coupon_auto_discount_percent",
    "set_coupon_auto_discount_percent",
    "get_coupon_auto_lifetime_days",
    "set_coupon_auto_lifetime_days",
]


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow().replace(microsecond=0)


def _format_dt(value: Optional[datetime.datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_expires_at(expires_at: Optional[Any]) -> Optional[str]:
    if not expires_at:
        return None
    if isinstance(expires_at, datetime.datetime):
        return _format_dt(expires_at)
    if isinstance(expires_at, datetime.date):
        return _format_dt(datetime.datetime.combine(expires_at, datetime.time(23, 59, 59)))
    return str(expires_at).strip() or None


def _clamp_percent(discount_percent: int) -> int:
    percent = int(discount_percent)
    if percent < 0 or percent > 100:
        raise ValueError("Скидка должна быть от 0 до 100%")
    return percent


def is_base62_code(code: str) -> bool:
    """Checks that the code consists of only base62 characters."""
    return bool(code and BASE62_RE.fullmatch(code))


def _generate_code(length: int = 10) -> str:
    return "".join(secrets.choice(BASE62_ALPHABET) for _ in range(length))


def generate_unique_promo_code(length: int = 10) -> str:
    """Generates a unique base62 code for a promotional code or coupon."""
    for _ in range(100):
        code = _generate_code(length)
        if not get_promo_code_by_code(code):
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный промокод")


def create_promo_code(
    *,
    code: str,
    discount_percent: int,
    expires_at: Optional[Any] = None,
    activation_limit: Optional[int] = None,
    is_active: bool = True,
    created_by_admin_id: Optional[int] = None,
    source: str = "manual",
    code_type: str = "promo",
    issued_to_user_id: Optional[int] = None,
    snapshot_lifetime_days: Optional[int] = None,
) -> int:
    """Generates a promotional code or coupon and returns the post ID."""
    code = (code or "").strip()
    if not is_base62_code(code):
        raise ValueError("Код должен состоять только из символов base62: 0-9, A-Z, a-z")
    if code_type not in ("promo", "coupon"):
        raise ValueError("Тип кода должен быть promo или coupon")

    percent = _clamp_percent(discount_percent)
    limit = activation_limit
    if code_type == "coupon":
        limit = 1
    if limit is not None:
        limit = int(limit)
        if limit <= 0:
            limit = None

    now = _format_dt(_utcnow())
    expires_value = _normalize_expires_at(expires_at)

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO promo_codes (
                type, code, discount_percent, expires_at, is_active,
                activation_limit, source, issued_to_user_id, created_by_admin_id,
                snapshot_discount_percent, snapshot_lifetime_days, snapshot_generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code_type,
                code,
                percent,
                expires_value,
                1 if is_active else 0,
                limit,
                source,
                issued_to_user_id,
                created_by_admin_id,
                percent if code_type == "coupon" else None,
                snapshot_lifetime_days if code_type == "coupon" else None,
                now if code_type == "coupon" else None,
            ),
        )
        promo_id = int(cursor.lastrowid)
        logger.info("Создан %s %s: discount=%s%%", code_type, code, percent)
        return promo_id


def create_coupon_batch(
    *,
    discount_percent: int,
    lifetime_days: int,
    count: int,
    source: str = "admin_generated",
    issued_to_user_id: Optional[int] = None,
    created_by_admin_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Creates a pack of one-time coupons."""
    percent = _clamp_percent(discount_percent)
    days = int(lifetime_days)
    total = int(count)
    if days <= 0:
        raise ValueError("Время жизни купона должно быть больше 0 дней")
    if total <= 0:
        raise ValueError("Количество купонов должно быть больше 0")

    expires_at = _utcnow() + datetime.timedelta(days=days)
    created: List[Dict[str, Any]] = []
    for _ in range(total):
        code = generate_unique_promo_code()
        promo_id = create_promo_code(
            code=code,
            discount_percent=percent,
            expires_at=expires_at,
            activation_limit=1,
            is_active=True,
            created_by_admin_id=created_by_admin_id,
            source=source,
            code_type="coupon",
            issued_to_user_id=issued_to_user_id,
            snapshot_lifetime_days=days,
        )
        created.append(
            {
                "id": promo_id,
                "code": code,
                "discount_percent": percent,
                "expires_at": _format_dt(expires_at),
                "lifetime_days": days,
            }
        )
    return created


def create_auto_coupon_for_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Creates one automatic coupon based on the current auto-issuance settings."""
    if not get_coupon_auto_enabled():
        return None
    coupons = create_coupon_batch(
        discount_percent=get_coupon_auto_discount_percent(),
        lifetime_days=get_coupon_auto_lifetime_days(),
        count=1,
        source="auto",
        issued_to_user_id=user_id,
    )
    return coupons[0] if coupons else None


def get_promo_code_by_id(promo_code_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE id = ?",
            (promo_code_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_promo_code_by_code(code: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            ((code or "").strip(),),
        ).fetchone()
        return _row_to_dict(row)


def get_promo_codes(code_type: Optional[str] = None, *, include_inactive: bool = True) -> List[Dict[str, Any]]:
    sql = """
        SELECT
            pc.*,
            COALESCE(COUNT(DISTINCT CASE WHEN pr.status = 'applied' THEN pr.id END), 0) AS applied_count,
            COALESCE(COUNT(DISTINCT plv.id), 0) AS link_visit_count,
            COALESCE(COUNT(DISTINCT CASE WHEN plv.converted_order_id IS NOT NULL THEN plv.id END), 0) AS link_conversion_count
        FROM promo_codes pc
        LEFT JOIN promo_redemptions pr ON pr.promo_code_id = pc.id
        LEFT JOIN promo_link_visits plv ON plv.promo_code_id = pc.id
    """
    params: List[Any] = []
    where: List[str] = []
    if code_type:
        where.append("pc.type = ?")
        params.append(code_type)
    if not include_inactive:
        where.append("pc.is_active = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY pc.id ORDER BY pc.created_at DESC, pc.id DESC"

    with get_db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _usage_count_for_limit(conn, promo_id: int, exclude_order_id: Optional[str] = None) -> int:
    params: List[Any] = [promo_id]
    exclude_sql = ""
    if exclude_order_id:
        exclude_sql = " AND order_id != ?"
        params.append(exclude_order_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS cnt
        FROM promo_redemptions
        WHERE promo_code_id = ?
          AND status IN ('reserved', 'applied')
          {exclude_sql}
        """,
        params,
    ).fetchone()
    return int(row["cnt"] if row else 0)


def _availability_for_row(conn, promo: Dict[str, Any], order_id: Optional[str] = None) -> Dict[str, Any]:
    if not promo:
        return {"ok": False, "reason": "not_found", "promo": None}
    if not int(promo.get("is_active") or 0):
        return {"ok": False, "reason": "inactive", "promo": promo}

    expires_at = promo.get("expires_at")
    if expires_at:
        row = conn.execute(
            "SELECT datetime(?) < CURRENT_TIMESTAMP AS expired",
            (expires_at,),
        ).fetchone()
        if row and int(row["expired"] or 0):
            return {"ok": False, "reason": "expired", "promo": promo}

    effective_limit = 1 if promo.get("type") == "coupon" else promo.get("activation_limit")
    if effective_limit:
        used = _usage_count_for_limit(conn, int(promo["id"]), exclude_order_id=order_id)
        if used >= int(effective_limit):
            return {"ok": False, "reason": "exhausted", "promo": promo}

    return {"ok": True, "reason": None, "promo": promo}


def get_promo_code_availability(code: str, order_id: Optional[str] = None) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            ((code or "").strip(),),
        ).fetchone()
        return _availability_for_row(conn, dict(row) if row else None, order_id=order_id)


def has_available_promo_codes() -> bool:
    """Checks if there is at least one promotional code or coupon available."""
    with get_db() as conn:
        row = conn.execute(
            """
            WITH usage AS (
                SELECT promo_code_id, COUNT(*) AS used_count
                FROM promo_redemptions
                WHERE status IN ('reserved', 'applied')
                GROUP BY promo_code_id
            ),
            candidates AS (
                SELECT
                    pc.id,
                    COALESCE(usage.used_count, 0) AS used_count,
                    CASE
                        WHEN pc.type = 'coupon' THEN 1
                        WHEN pc.activation_limit IS NULL OR pc.activation_limit <= 0 THEN NULL
                        ELSE pc.activation_limit
                    END AS effective_limit
                FROM promo_codes pc
                LEFT JOIN usage ON usage.promo_code_id = pc.id
                WHERE pc.is_active = 1
                  AND (
                      pc.expires_at IS NULL
                      OR pc.expires_at = ''
                      OR COALESCE(datetime(pc.expires_at) < CURRENT_TIMESTAMP, 0) = 0
                  )
            )
            SELECT 1
            FROM candidates
            WHERE effective_limit IS NULL OR used_count < effective_limit
            LIMIT 1
            """
        ).fetchone()
        return row is not None


def update_promo_code(
    promo_code_id: int,
    *,
    discount_percent: Optional[int] = None,
    expires_at: Any = "__unchanged__",
    activation_limit: Any = "__unchanged__",
    is_active: Optional[bool] = None,
) -> bool:
    fields: List[str] = []
    params: List[Any] = []

    if discount_percent is not None:
        fields.append("discount_percent = ?")
        params.append(_clamp_percent(discount_percent))
    if expires_at != "__unchanged__":
        fields.append("expires_at = ?")
        params.append(_normalize_expires_at(expires_at))
    if activation_limit != "__unchanged__":
        if activation_limit is None or int(activation_limit) <= 0:
            fields.append("activation_limit = NULL")
        else:
            fields.append("activation_limit = ?")
            params.append(int(activation_limit))
    if is_active is not None:
        fields.append("is_active = ?")
        params.append(1 if is_active else 0)

    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(promo_code_id))
    with get_db() as conn:
        cursor = conn.execute(
            f"UPDATE promo_codes SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


def set_promo_code_active(promo_code_id: int, is_active: bool) -> bool:
    return update_promo_code(promo_code_id, is_active=is_active)


def set_user_active_promo_code(user_id: int, promo_code_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET active_promo_code_id = ? WHERE id = ?",
            (promo_code_id, user_id),
        )
        return cursor.rowcount > 0


def clear_user_active_promo_code(user_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET active_promo_code_id = NULL WHERE id = ?",
            (user_id,),
        )
        return cursor.rowcount > 0


def get_user_active_promo_code(user_id: int, order_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT pc.*
            FROM users u
            JOIN promo_codes pc ON pc.id = u.active_promo_code_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        promo = _row_to_dict(row)
        if not promo:
            return None
        availability = _availability_for_row(conn, promo, order_id=order_id)
        if not availability["ok"]:
            conn.execute("UPDATE users SET active_promo_code_id = NULL WHERE id = ?", (user_id,))
            return None
        return promo


def record_promo_link_visit(
    *,
    promo_code_id: int,
    code: str,
    user_id: Optional[int],
    telegram_id: int,
    start_param: str,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO promo_link_visits (promo_code_id, code, user_id, telegram_id, start_param)
            VALUES (?, ?, ?, ?, ?)
            """,
            (promo_code_id, code, user_id, telegram_id, start_param),
        )
        return int(cursor.lastrowid)


def save_order_pricing_snapshot(
    *,
    order_id: str,
    payment_type: str,
    original_amount: int,
    discount_amount: int,
    final_amount: int,
    amount_unit: str,
    promo: Optional[Dict[str, Any]] = None,
) -> bool:
    if amount_unit not in ("cents", "stars"):
        raise ValueError("amount_unit должен быть cents или stars")

    promo_id = promo.get("id") if promo else None
    promo_code = promo.get("code") if promo else None
    discount_percent = int(promo.get("discount_percent") or 0) if promo else 0
    original_cents = original_amount if amount_unit == "cents" else None
    discount_cents = discount_amount if amount_unit == "cents" else 0
    final_cents = final_amount if amount_unit == "cents" else None
    original_stars = original_amount if amount_unit == "stars" else None
    discount_stars = discount_amount if amount_unit == "stars" else 0
    final_stars = final_amount if amount_unit == "stars" else None

    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET payment_type = ?,
                promo_code_id = ?,
                promo_code = ?,
                discount_percent = ?,
                original_amount_cents = ?,
                discount_amount_cents = ?,
                final_amount_cents = ?,
                original_amount_stars = ?,
                discount_amount_stars = ?,
                final_amount_stars = ?,
                amount_cents = CASE WHEN ? = 'cents' THEN ? ELSE amount_cents END,
                amount_stars = CASE WHEN ? = 'stars' THEN ? ELSE amount_stars END,
                is_promo_free = ?
            WHERE order_id = ?
            """,
            (
                payment_type,
                promo_id,
                promo_code,
                discount_percent,
                original_cents,
                discount_cents,
                final_cents,
                original_stars,
                discount_stars,
                final_stars,
                amount_unit,
                final_amount,
                amount_unit,
                final_amount,
                1 if final_amount == 0 and promo else 0,
                order_id,
            ),
        )
        return cursor.rowcount > 0


def reserve_promo_for_order(
    *,
    order_id: str,
    user_id: int,
    promo: Dict[str, Any],
    payment_type: str,
    action: str,
    original_amount: int,
    discount_amount: int,
    final_amount: int,
    amount_unit: str,
) -> Dict[str, Any]:
    with get_db() as conn:
        promo_row = conn.execute(
            "SELECT * FROM promo_codes WHERE id = ?",
            (promo["id"],),
        ).fetchone()
        availability = _availability_for_row(conn, dict(promo_row) if promo_row else None, order_id=order_id)
        if not availability["ok"]:
            return availability

        conn.execute(
            "DELETE FROM promo_redemptions WHERE order_id = ? AND status = 'reserved'",
            (order_id,),
        )
        conn.execute(
            """
            INSERT INTO promo_redemptions (
                promo_code_id, user_id, order_id, code, discount_percent,
                status, payment_type, action, original_amount, discount_amount,
                final_amount, amount_unit
            )
            VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?)
            """,
            (
                promo["id"],
                user_id,
                order_id,
                promo["code"],
                int(promo.get("discount_percent") or 0),
                payment_type,
                action,
                original_amount,
                discount_amount,
                final_amount,
                amount_unit,
            ),
        )
        return {"ok": True, "reason": None, "promo": dict(promo_row)}


def cancel_promo_reservation_for_order(order_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE promo_redemptions SET status = 'canceled' WHERE order_id = ? AND status = 'reserved'",
            (order_id,),
        )
        return cursor.rowcount > 0


def apply_promo_for_order(order_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM promo_redemptions
            WHERE order_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (order_id,),
        ).fetchone()
        redemption = _row_to_dict(row)
        if not redemption:
            return None
        if redemption.get("status") == "applied":
            return redemption
        if redemption.get("status") != "reserved":
            return None

        conn.execute(
            """
            UPDATE promo_redemptions
            SET status = 'applied', applied_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (redemption["id"],),
        )
        conn.execute(
            """
            UPDATE promo_codes
            SET usage_count = usage_count + 1,
                is_active = CASE WHEN type = 'coupon' THEN 0 ELSE is_active END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (redemption["promo_code_id"],),
        )
        conn.execute(
            """
            UPDATE promo_link_visits
            SET converted_order_id = ?, converted_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id
                FROM promo_link_visits
                WHERE promo_code_id = ?
                  AND user_id = ?
                  AND converted_order_id IS NULL
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            )
            """,
            (order_id, redemption["promo_code_id"], redemption["user_id"]),
        )
        redemption["status"] = "applied"
        return redemption


def get_coupon_auto_enabled() -> bool:
    return get_setting(COUPON_AUTO_ENABLED_KEY, "0") == "1"


def set_coupon_auto_enabled(enabled: bool) -> None:
    set_setting(COUPON_AUTO_ENABLED_KEY, "1" if enabled else "0")


def get_coupon_auto_discount_percent() -> int:
    try:
        return _clamp_percent(int(get_setting(COUPON_AUTO_DISCOUNT_KEY, "10") or 10))
    except (TypeError, ValueError):
        return 10


def set_coupon_auto_discount_percent(discount_percent: int) -> None:
    set_setting(COUPON_AUTO_DISCOUNT_KEY, str(_clamp_percent(discount_percent)))


def get_coupon_auto_lifetime_days() -> int:
    try:
        days = int(get_setting(COUPON_AUTO_LIFETIME_KEY, "90") or 90)
    except (TypeError, ValueError):
        return 90
    return max(days, 1)


def set_coupon_auto_lifetime_days(days: int) -> None:
    days = int(days)
    if days <= 0:
        raise ValueError("Время жизни должно быть больше 0 дней")
    set_setting(COUPON_AUTO_LIFETIME_KEY, str(days))
