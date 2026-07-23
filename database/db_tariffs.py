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
    'normalize_tariff_money',
]


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

def get_all_tariffs(include_hidden: bool = False) -> List[Dict[str, Any]]:
    """
    Gets a list of all tariffs.
    
    Args:
        include_hidden: Include hidden rates (is_active = 0)
        
    Returns:
        List of dictionaries with tariff data
    """
    with get_db() as conn:
        if include_hidden:
            cursor = conn.execute("""
                SELECT id, name, duration_days, price_rub, price_minor,
                       display_order, is_active, traffic_limit_gb, group_id, max_ips
                FROM tariffs
                ORDER BY display_order, id
            """)
        else:
            cursor = conn.execute("""
                SELECT id, name, duration_days, price_rub, price_minor,
                       display_order, is_active, traffic_limit_gb, group_id, max_ips
                FROM tariffs
                WHERE is_active = 1
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
                   display_order, is_active, traffic_limit_gb, group_id, max_ips
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
    
    with get_db() as conn:
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
    if not tariff:
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
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM tariffs WHERE is_active = 1")
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def get_admin_tariff() -> Optional[Dict[str, Any]]:
    """
    Gets the hidden Admin Tariff for the admin adding keys.
    
    If the tariff does not exist, it creates it automatically.
    
    Returns:
        Dictionary with tariff data
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, duration_days, price_rub, price_minor,
                   display_order, is_active, max_ips
            FROM tariffs
            WHERE name = 'Admin Tariff'
            LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            base, rub_rate = _base_currency_and_rub_rate(conn)
            return normalize_tariff_money(dict(row), base_currency=base, rub_rate=rub_rate)
        
        # If the tariff is not found, create it
        cursor = conn.execute("""
            INSERT INTO tariffs (name, duration_days, price_rub, price_minor, display_order, is_active, max_ips)
            VALUES ('Admin Tariff', 30, 0, 0, 999, 0, 1)
        """)
        logger.info("Создан Admin Tariff")
        
        return {
            'id': cursor.lastrowid,
            'name': 'Admin Tariff',
            'duration_days': 30,
            'price_rub': 0,
            'price_minor': 0,
            'base_currency': _base_currency_and_rub_rate(conn)[0],
            'display_order': 999,
            'is_active': 0,
            'max_ips': 1
        }


