import sqlite3
import logging
import secrets
import string
import datetime
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
]

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
                SELECT id, name, duration_days, price_cents, price_stars, price_rub, 
                       display_order, is_active, traffic_limit_gb, group_id, max_ips
                FROM tariffs
                ORDER BY display_order, id
            """)
        else:
            cursor = conn.execute("""
                SELECT id, name, duration_days, price_cents, price_stars, price_rub, 
                       display_order, is_active, traffic_limit_gb, group_id, max_ips
                FROM tariffs
                WHERE is_active = 1
                ORDER BY display_order, id
            """)
        return [dict(row) for row in cursor.fetchall()]

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
            SELECT id, name, duration_days, price_cents, price_stars, price_rub, 
                   display_order, is_active, traffic_limit_gb, group_id, max_ips
            FROM tariffs
            WHERE id = ?
        """, (tariff_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def add_tariff(
    name: str,
    duration_days: int,
    price_cents: int,
    price_stars: int,
    price_rub: int = 0,
    display_order: int = 0,
    traffic_limit_gb: int = 0,
    group_id: int = 1,
    max_ips: int = 1
) -> int:
    """
    Adds a new tariff.
    
    Args:
        name: Tariff name
        duration_days: Duration in days
        price_cents: Price in cents (USDT * 100)
        price_stars: Price in Telegram Stars
        price_rub: Price in rubles
        display_order: Display order
        traffic_limit_gb: Traffic limit in GB (0 = unlimited)
        group_id: tariff group ID (default 1 - “Main”)
        max_ips: Device (IP address) limit (default 1)
        
    Returns:
        ID of the created tariff
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO tariffs (name, duration_days, price_cents, price_stars, price_rub, 
                                display_order, is_active, traffic_limit_gb, group_id, max_ips)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (name, duration_days, price_cents, price_stars, price_rub, display_order, traffic_limit_gb, group_id, max_ips))
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
    allowed_fields = {'name', 'duration_days', 'price_cents', 'price_stars', 'price_rub',
                      'display_order', 'is_active', 'group_id', 'traffic_limit_gb', 'max_ips'}
    fields = {k: v for k, v in fields.items() if k in allowed_fields}
    
    if not fields:
        return False
    
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [tariff_id]
    
    with get_db() as conn:
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
            SELECT id, name, duration_days, price_cents, price_stars, price_rub, 
                   display_order, is_active, max_ips
            FROM tariffs
            WHERE name = 'Admin Tariff'
            LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        
        # If the tariff is not found, create it
        cursor = conn.execute("""
            INSERT INTO tariffs (name, duration_days, price_cents, price_stars, price_rub, display_order, is_active, max_ips)
            VALUES ('Admin Tariff', 30, 0, 0, 0, 999, 0, 1)
        """)
        logger.info("Создан Admin Tariff")
        
        return {
            'id': cursor.lastrowid,
            'name': 'Admin Tariff',
            'duration_days': 30,
            'price_cents': 0,
            'price_stars': 0,
            'price_rub': 0,
            'display_order': 999,
            'is_active': 0,
            'max_ips': 1
        }


