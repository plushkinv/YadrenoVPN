import sqlite3
import logging
import secrets
import string
import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_all_tariffs',
    'get_tariff_by_id',
    'add_tariff',
    'update_tariff',
    'update_tariff_field',
    'toggle_tariff_active',
    'get_tariffs_count',
    'get_admin_tariff',
    'get_admin_custom_tariff',
    'ensure_admin_custom_tariff',
    'is_admin_custom_tariff',
    'normalize_tariff_money',
]


ADMIN_CUSTOM_SYSTEM_TYPE = 'admin_custom'


def _base_currency_and_rub_rate(conn) -> tuple[str, Decimal]:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'base_currency'"
    ).fetchone()
    base = str(row['value'] if row else 'RUB').upper()
    if base == 'RUB':
        return 'RUB', Decimal('1')
    rate_row = conn.execute(
        """
        SELECT units_per_base FROM currency_rates
        WHERE base_currency = ? AND target_currency = 'RUB'
        """,
        (base,),
    ).fetchone()
    return base, Decimal(str(rate_row['units_per_base'])) if rate_row else Decimal('1')


def normalize_tariff_money(row: Dict[str, Any], *, base_currency: str, rub_rate: Decimal) -> Dict[str, Any]:
    """Adds generic money fields and a derived legacy RUB compatibility value."""
    data = dict(row)
    minor = int(data.get('price_minor') or 0)
    if minor == 0 and base_currency == 'RUB' and data.get('price_rub'):
        minor = int(
            (Decimal(str(data.get('price_rub'))) * Decimal('100')).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
    data['price_minor'] = minor
    data['base_currency'] = base_currency
    rub_major = Decimal(minor) / Decimal('100')
    if base_currency != 'RUB':
        rub_major *= rub_rate
    data['price_rub'] = float(rub_major) if rub_major % 1 else int(rub_major)
    return data

def get_all_tariffs(
    include_hidden: bool = False,
    *,
    include_system: bool = False,
) -> List[Dict[str, Any]]:
    """
    Gets a list of all tariffs.
    
    Args:
        include_hidden: Include hidden rates (is_active = 0)
        
    Returns:
        List of dictionaries with tariff data
    """
    with get_db() as conn:
        conditions = []
        if not include_hidden:
            conditions.append("is_active = 1")
        if not include_system:
            conditions.append("system_type IS NULL")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = conn.execute(f"""
            SELECT id, name, duration_days, price_rub, price_minor,
                   display_order, is_active, traffic_limit_gb, group_id, max_ips,
                   system_type
            FROM tariffs
            {where_clause}
            ORDER BY display_order, id
        """)
        base, rub_rate = _base_currency_and_rub_rate(conn)
        return [normalize_tariff_money(dict(row), base_currency=base, rub_rate=rub_rate) for row in cursor.fetchall()]

def get_tariff_by_id(tariff_id: int) -> Optional[Dict[str, Any]]:
    """
    Receives tariff by ID.
    
    Args:
        tariff_id: Tariff ID
        
    Returns:
        Dictionary with tariff data or None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, duration_days, price_rub, price_minor,
                   display_order, is_active, traffic_limit_gb, group_id, max_ips,
                   system_type
            FROM tariffs
            WHERE id = ?
        """, (tariff_id,))
        row = cursor.fetchone()
        if not row:
            return None
        base, rub_rate = _base_currency_and_rub_rate(conn)
        return normalize_tariff_money(dict(row), base_currency=base, rub_rate=rub_rate)

def add_tariff(
    name: str,
    duration_days: int,
    price_rub: int | float | None = None,
    display_order: int = 0,
    traffic_limit_gb: int = 0,
    group_id: int = 1,
    max_ips: int = 1,
    price_minor: int | None = None,
) -> int:
    """
    Adds a new tariff.
    
    Args:
        name: Tariff name
        duration_days: Duration in days
        price_rub: Deprecated RUB-major compatibility price
        price_minor: Price in current base-currency minor units
        display_order: Display order
        traffic_limit_gb: Traffic limit in GB (0 = unlimited)
        group_id: tariff group ID (default 1 - “Main”)
        max_ips: Device (IP address) limit (default 1)
        
    Returns:
        ID of the created tariff
    """
    duration_days = int(duration_days)
    traffic_limit_gb = int(traffic_limit_gb)
    max_ips = int(max_ips)
    if not 0 <= duration_days <= 99999:
        raise ValueError("duration_days must be between 0 and 99999")
    if not 0 <= traffic_limit_gb <= 99999:
        raise ValueError("traffic_limit_gb must be between 0 and 99999")
    if not 1 <= max_ips <= 999:
        raise ValueError("max_ips must be between 1 and 999")
    with get_db() as conn:
        base, rub_rate = _base_currency_and_rub_rate(conn)
        if price_minor is None:
            rub_major = Decimal(str(price_rub or 0))
            base_major = rub_major if base == 'RUB' else rub_major / rub_rate
            resolved_minor = int(
                (base_major * Decimal('100')).to_integral_value(rounding=ROUND_HALF_UP)
            )
        else:
            resolved_minor = max(0, int(price_minor))
        base_major = Decimal(resolved_minor) / Decimal('100')
        legacy_rub = base_major if base == 'RUB' else base_major * rub_rate
        cursor = conn.execute("""
            INSERT INTO tariffs (name, duration_days, price_rub, price_minor,
                                display_order, is_active, traffic_limit_gb, group_id, max_ips)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (name, duration_days, float(legacy_rub), resolved_minor, display_order, traffic_limit_gb, group_id, max_ips))
        tariff_id = cursor.lastrowid
        logger.info(f"Добавлен тариф: {name} (ID: {tariff_id}, трафик: {traffic_limit_gb} ГБ, группа: {group_id}, max_ips: {max_ips})")
        return tariff_id

def update_tariff(tariff_id: int, **fields) -> bool:
    """
    Updates rate fields.
    
    Args:
        tariff_id: Tariff ID
        **fields: Fields to update
        
    Returns:
        True if update is successful
    """
    allowed_fields = {'name', 'duration_days', 'price_rub', 'price_minor',
                      'display_order', 'is_active', 'group_id', 'traffic_limit_gb', 'max_ips'}
    fields = {k: v for k, v in fields.items() if k in allowed_fields}
    
    if not fields:
        return False

    if 'duration_days' in fields:
        fields['duration_days'] = int(fields['duration_days'])
        if not 0 <= fields['duration_days'] <= 99999:
            raise ValueError("duration_days must be between 0 and 99999")
    if 'traffic_limit_gb' in fields:
        fields['traffic_limit_gb'] = int(fields['traffic_limit_gb'])
        if not 0 <= fields['traffic_limit_gb'] <= 99999:
            raise ValueError("traffic_limit_gb must be between 0 and 99999")
    if 'max_ips' in fields:
        fields['max_ips'] = int(fields['max_ips'])
        if not 1 <= fields['max_ips'] <= 999:
            raise ValueError("max_ips must be between 1 and 999")
    
    with get_db() as conn:
        protected = conn.execute(
            "SELECT system_type FROM tariffs WHERE id = ?",
            (tariff_id,),
        ).fetchone()
        if protected and protected['system_type'] is not None:
            logger.warning("Protected system tariff ID %s cannot be updated", tariff_id)
            return False
        base, rub_rate = _base_currency_and_rub_rate(conn)
        if 'price_minor' in fields:
            resolved_minor = max(0, int(fields['price_minor']))
            base_major = Decimal(resolved_minor) / Decimal('100')
            fields['price_rub'] = float(base_major if base == 'RUB' else base_major * rub_rate)
        elif 'price_rub' in fields:
            rub_major = Decimal(str(fields['price_rub'] or 0))
            base_major = rub_major if base == 'RUB' else rub_major / rub_rate
            fields['price_minor'] = int(
                (base_major * Decimal('100')).to_integral_value(rounding=ROUND_HALF_UP)
            )
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [tariff_id]
        cursor = conn.execute(f"""
            UPDATE tariffs
            SET {set_clause}
            WHERE id = ?
        """, values)
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Обновлён тариф ID {tariff_id}: {list(fields.keys())}")
        return success

def update_tariff_field(tariff_id: int, field: str, value: Any) -> bool:
    """
    Updates one rate field.
    
    Args:
        tariff_id: Tariff ID
        field: Field name
        value: New value
        
    Returns:
        True if update is successful
    """
    return update_tariff(tariff_id, **{field: value})

def toggle_tariff_active(tariff_id: int) -> Optional[bool]:
    """
    Switches the tariff activity (hide/show).
    
    Args:
        tariff_id: Tariff ID
        
    Returns:
        New status (True = active) or None if tariff not found
    """
    tariff = get_tariff_by_id(tariff_id)
    if not tariff or tariff.get('system_type') is not None:
        return None
    
    new_status = 0 if tariff['is_active'] else 1
    
    with get_db() as conn:
        conn.execute("""
            UPDATE tariffs
            SET is_active = ?
            WHERE id = ?
        """, (new_status, tariff_id))
        status_text = "активирован" if new_status else "скрыт"
        logger.info(f"Тариф ID {tariff_id}: {status_text}")
        return bool(new_status)

def get_tariffs_count() -> int:
    """
    Returns the number of active tariffs.
    
    Returns:
        Number of active tariffs
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM tariffs "
            "WHERE is_active = 1 AND system_type IS NULL"
        )
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def _get_admin_custom_tariff_with_conn(
    conn: sqlite3.Connection,
    group_id: int,
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, name, duration_days, price_rub, price_minor,
               display_order, is_active, traffic_limit_gb, group_id, max_ips,
               system_type
        FROM tariffs
        WHERE group_id = ? AND system_type = ?
        LIMIT 1
        """,
        (int(group_id), ADMIN_CUSTOM_SYSTEM_TYPE),
    ).fetchone()
    if row is None:
        return None
    base, rub_rate = _base_currency_and_rub_rate(conn)
    return normalize_tariff_money(
        dict(row),
        base_currency=base,
        rub_rate=rub_rate,
    )


def get_admin_custom_tariff(group_id: int) -> Optional[Dict[str, Any]]:
    """Returns the protected custom admin tariff for one tariff group."""
    with get_db() as conn:
        return _get_admin_custom_tariff_with_conn(conn, group_id)


def ensure_admin_custom_tariff(
    group_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> Dict[str, Any]:
    """Returns or creates the protected custom admin tariff for a group."""
    if conn is None:
        with get_db() as owned_conn:
            return ensure_admin_custom_tariff(group_id, conn=owned_conn)

    group = conn.execute(
        "SELECT id FROM tariff_groups WHERE id = ?",
        (int(group_id),),
    ).fetchone()
    if group is None:
        raise ValueError(f"Tariff group {group_id} does not exist")
    existing = _get_admin_custom_tariff_with_conn(conn, group_id)
    if existing is not None:
        return existing

    cursor = conn.execute(
        """
        INSERT INTO tariffs (
            name, duration_days, price_rub, price_minor, display_order,
            is_active, traffic_limit_gb, group_id, max_ips, system_type
        )
        VALUES (?, 0, 0, 0, 999, 0, 0, ?, 1, ?)
        """,
        (f'Admin Custom {int(group_id)}', int(group_id), ADMIN_CUSTOM_SYSTEM_TYPE),
    )
    logger.info(
        "Created protected admin custom tariff for group %s (ID: %s)",
        group_id,
        cursor.lastrowid,
    )
    created = _get_admin_custom_tariff_with_conn(conn, group_id)
    if created is None:
        raise RuntimeError("Failed to create protected admin custom tariff")
    return created


def is_admin_custom_tariff(tariff_id: int) -> bool:
    """Returns whether a tariff is the protected custom admin tariff."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT system_type FROM tariffs WHERE id = ?",
            (int(tariff_id),),
        ).fetchone()
        return bool(
            row and row['system_type'] == ADMIN_CUSTOM_SYSTEM_TYPE
        )


def get_admin_tariff(group_id: int = 1) -> Optional[Dict[str, Any]]:
    """Compatibility wrapper for the group-aware protected admin tariff."""
    return ensure_admin_custom_tariff(group_id)


