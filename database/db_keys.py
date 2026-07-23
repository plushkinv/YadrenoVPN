import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_user_vpn_keys',
    'get_vpn_key_by_id',
    'extend_vpn_key',
    'create_vpn_key_admin',
    'create_vpn_key_subscription_admin',
    'update_vpn_key_connection',
    'create_vpn_key',
    'create_initial_vpn_key',
    'is_key_active',
    'is_traffic_exhausted',
    'get_all_active_keys_with_server',
    'get_all_panel_sync_keys',
    'bulk_update_traffic',
    'apply_panel_import_batch',
    'update_key_traffic',
    'update_key_notified_pct',
    'reset_key_traffic_notification',
    'update_key_traffic_limit',
    'update_vpn_key_tariff_and_traffic_limit',
    'update_vpn_key_config',
    'update_vpn_key_sub_id',
    'delete_vpn_key',
    'get_all_keys_with_server',
    'get_user_keys_for_display',
    'get_key_details_for_user',
    'update_key_custom_name',
    'add_days_to_first_active_key',
    'get_user_by_panel_email',
]

def get_user_vpn_keys(user_id: int) -> List[Dict[str, Any]]:
    """
    Receives all the user's VPN keys with data about the tariff and server.
    
    Args:
        user_id: Internal user ID (users.id)
    
    Returns:
        List of keys with full information
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.client_uuid, vk.custom_name, vk.expires_at,
                vk.created_at, vk.panel_inbound_id, vk.panel_email, vk.sub_id,
                t.name as tariff_name, t.duration_days,
                s.name as server_name, s.id as server_id
            FROM vpn_keys vk
            LEFT JOIN tariffs t ON vk.tariff_id = t.id
            LEFT JOIN servers s ON vk.server_id = s.id
            WHERE vk.user_id = ?
            ORDER BY vk.expires_at DESC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]

def get_vpn_key_by_id(key_id: int) -> Optional[Dict[str, Any]]:
    """
    Receives a VPN key by ID with complete information.
    
    Args:
        key_id: Key ID
    
    Returns:
        Dictionary with key data or None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.*,
                t.name as tariff_name, t.duration_days, t.price_rub, t.price_minor,
                COALESCE((SELECT value FROM settings WHERE key = 'base_currency'), 'RUB') AS base_currency,
                s.name as server_name, s.host, s.port, s.web_base_path,
                s.login, s.password, s.protocol, s.api_token,
                s.panel_version, s.panel_api_profile, s.panel_checked_at,
                s.is_active as server_active,
                u.telegram_id, u.username, u.is_banned
            FROM vpn_keys vk
            LEFT JOIN tariffs t ON vk.tariff_id = t.id
            LEFT JOIN servers s ON vk.server_id = s.id
            LEFT JOIN users u ON vk.user_id = u.id
            WHERE vk.id = ?
        """, (key_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def extend_vpn_key(key_id: int, days: int) -> bool:
    """
    Extends the VPN key for the specified number of days.
    
    Args:
        key_id: Key ID
        days: Number of days to extend
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        modifier = f"{days:+} days"
        cursor = conn.execute("""
            UPDATE vpn_keys 
            SET expires_at = MAX(
                datetime('now'),
                datetime(
                    CASE 
                        WHEN expires_at > datetime('now') THEN expires_at
                        ELSE datetime('now')
                    END, 
                    ?
                )
            )
            WHERE id = ?
        """, (modifier, key_id))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Ключ ID {key_id} продлён на {days} дней")
        return success

def create_vpn_key_admin(
    user_id: int, 
    server_id: int, 
    tariff_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    days: int,
    traffic_limit: int = 0
) -> int:
    """
    Creates a VPN key by the administrator (without payment).
    
    Args:
        user_id: Internal user ID
        server_id: Server ID
        tariff_id: Tariff ID
        panel_inbound_id: ID inbound in the panel
        panel_email: Email (identifier) of the client in the panel
        client_uuid: Client UUID
        days: Validity period in days
        traffic_limit: Traffic limit in bytes (0 = unlimited)
    
    Returns:
        Created key ID
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO vpn_keys 
            (user_id, server_id, tariff_id, panel_inbound_id, panel_email, client_uuid, 
             expires_at, traffic_limit)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+' || ? || ' days'), ?)
        """, (user_id, server_id, tariff_id, panel_inbound_id, panel_email, client_uuid, 
              days, traffic_limit))
        key_id = cursor.lastrowid
        logger.info(f"Администратор создал ключ ID {key_id} для user_id {user_id}")
        return key_id

def update_vpn_key_connection(
    key_id: int,
    server_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    sub_id: Optional[str] = ...,
) -> bool:
    """
    Updates the technical data of the key (server, UUID, inbound).
    Used when replacing a key.

    Args:
        key_id: Key ID
        server_id: ID of the new server
        panel_inbound_id: ID inbound in the panel
        panel_email: Email (identifier) of the client in the panel
        client_uuid: New client UUID
        sub_id: Subscription ID. If passed (including None) - updated
                in the database. Default (Ellipsis) - the field is not touched.

    Returns:
        True if successful
    """
    with get_db() as conn:
        if sub_id is ...:
            cursor = conn.execute("""
                UPDATE vpn_keys
                SET server_id = ?,
                    panel_inbound_id = ?,
                    panel_email = ?,
                    client_uuid = ?
                WHERE id = ?
            """, (server_id, panel_inbound_id, panel_email, client_uuid, key_id))
        else:
            cursor = conn.execute("""
                UPDATE vpn_keys
                SET server_id = ?,
                    panel_inbound_id = ?,
                    panel_email = ?,
                    client_uuid = ?,
                    sub_id = ?
                WHERE id = ?
            """, (server_id, panel_inbound_id, panel_email, client_uuid, sub_id, key_id))
        success = cursor.rowcount > 0
        if success:
            preview = (client_uuid[:4] + '...') if client_uuid else '?'
            logger.info(f"Ключ ID {key_id} перенесён на сервер {server_id} (новый UUID: {preview})")
        return success

def create_vpn_key(
    user_id: int, 
    server_id: int, 
    tariff_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    days: int,
    traffic_limit: int = 0
) -> int:
    """
    Creates a fully configured VPN key (wrapper over create_vpn_key_admin).
    To create a draft, use create_initial_vpn_key.
    """
    return create_vpn_key_admin(
        user_id, server_id, tariff_id, panel_inbound_id, 
        panel_email, client_uuid, days, traffic_limit
    )

def create_initial_vpn_key(
    user_id: int,
    tariff_id: int,
    days: int,
    traffic_limit: int = 0
) -> int:
    """
    Creates an initial (draft) VPN key without being tied to a server.
    The key is created immediately after payment.
    
    Args:
        user_id: User ID
        tariff_id: Tariff ID
        days: Validity period (days)
        traffic_limit: Traffic limit in bytes (0 = unlimited)
        
    Returns:
        Created key ID
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO vpn_keys 
            (user_id, tariff_id, expires_at, created_at, traffic_limit)
            VALUES (?, ?, datetime('now', '+' || ? || ' days'), CURRENT_TIMESTAMP, ?)
        """, (user_id, tariff_id, days, traffic_limit))
        return cursor.lastrowid

def is_key_active(key: dict) -> bool:
    """
    Checks the activity of the key (date + traffic).
    A single point of checking key status for the entire project.
    
    Args:
        key: Dictionary with key data (must contain expires_at, traffic_limit, traffic_used)
    
    Returns:
        True if the key is active
    """
    from datetime import datetime
    
    # Checking expiration date
    expires_at = key.get('expires_at')
    if expires_at:
        try:
            from datetime import timezone
            expires = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires < now:
                return False
        except (ValueError, TypeError):

            pass
    
    # Traffic check
    traffic_limit = key.get('traffic_limit', 0) or 0
    traffic_used = key.get('traffic_used', 0) or 0
    if traffic_limit > 0 and traffic_used >= traffic_limit:
        return False
    
    return True

def is_traffic_exhausted(key: dict) -> bool:
    """
    Checks whether the key has exhausted traffic.
    
    Returns:
        True if traffic is exhausted (traffic_used >= traffic_limit > 0)
    """
    traffic_limit = key.get('traffic_limit', 0) or 0
    traffic_used = key.get('traffic_used', 0) or 0
    return traffic_limit > 0 and traffic_used >= traffic_limit

def get_all_active_keys_with_server() -> List[Dict[str, Any]]:
    """
    Retrieves all active keys with server data.
    For traffic synchronization scheduler.
    
    Returns:
        List of keys with server and user data
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.panel_email, vk.traffic_used, vk.traffic_limit,
                vk.traffic_notified_pct, vk.custom_name, vk.client_uuid,
                vk.panel_inbound_id, vk.tariff_id, vk.expires_at, vk.sub_id,
                s.id as server_id, s.name as server_name,
                u.telegram_id
            FROM vpn_keys vk
            JOIN servers s ON vk.server_id = s.id
            JOIN users u ON vk.user_id = u.id
            WHERE (vk.expires_at > datetime('now') OR vk.expires_at IS NULL)
            AND vk.panel_email IS NOT NULL
            AND s.is_active = 1
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_all_panel_sync_keys() -> List[Dict[str, Any]]:
    """Return all managed keys on active servers, including expired keys."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.panel_email, vk.traffic_used, vk.traffic_limit,
                vk.traffic_notified_pct, vk.custom_name, vk.client_uuid,
                vk.panel_inbound_id, vk.tariff_id, vk.expires_at, vk.sub_id,
                s.id AS server_id, s.name AS server_name,
                u.telegram_id, u.is_banned
            FROM vpn_keys vk
            JOIN servers s ON vk.server_id = s.id
            JOIN users u ON vk.user_id = u.id
            WHERE vk.panel_email IS NOT NULL
              AND s.is_active = 1
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_all_keys_with_server() -> List[Dict[str, Any]]:
    """
    Receives ALL keys associated with the server (including expired ones).
    To synchronize remote keys.
    
    Returns:
        List of keys with server and user data
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.panel_email, vk.client_uuid,
                vk.panel_inbound_id, vk.server_id, vk.sub_id,
                s.name as server_name,
                u.telegram_id
            FROM vpn_keys vk
            JOIN servers s ON vk.server_id = s.id
            JOIN users u ON vk.user_id = u.id
            WHERE vk.panel_email IS NOT NULL
            AND s.is_active = 1
        """)
        return [dict(row) for row in cursor.fetchall()]

def bulk_update_traffic(updates: List[tuple]) -> None:
    """
    Massive traffic update for keys.
    
    Args:
        updates: List of tuples (traffic_used, key_id)
    """
    if not updates:
        return
    
    with get_db() as conn:
        conn.executemany("""
            UPDATE vpn_keys 
            SET traffic_used = ?, traffic_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, updates)
        logger.info(f"Обновлён трафик для {len(updates)} ключей")

def apply_panel_import_batch(updates: List[Dict[str, Any]]) -> int:
    """Atomically apply a normalized Panel -> DB import for one server."""
    if not updates:
        return 0

    rows = [
        (
            update.get('expires_at'),
            max(0, int(update.get('traffic_used', 0) or 0)),
            max(0, int(update.get('traffic_limit', 0) or 0)),
            int(update.get('traffic_notified_pct', 100) or 0),
            int(update['key_id']),
        )
        for update in updates
    ]
    with get_db() as conn:
        conn.executemany("""
            UPDATE vpn_keys
            SET expires_at = ?,
                traffic_used = ?,
                traffic_limit = ?,
                traffic_notified_pct = ?,
                traffic_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, rows)
    logger.info("Applied Panel -> DB state for %s keys", len(rows))
    return len(rows)


def update_key_traffic(key_id: int, traffic_used: int) -> None:
    """
    Updates traffic for one key.
    
    Args:
        key_id: Key ID
        traffic_used: Traffic consumed in bytes
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys 
            SET traffic_used = ?, traffic_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (traffic_used, key_id))

def update_key_notified_pct(key_id: int, pct: int) -> None:
    """
    Updates the latest traffic notification threshold.
    
    Args:
        key_id: Key ID
        pct: Threshold in % (10, 5, 3, 2, 1, 0)
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys SET traffic_notified_pct = ? WHERE id = ?
        """, (pct, key_id))

def reset_key_traffic_notification(key_id: int) -> None:
    """
    Resets traffic notifications and usage cache.
    Called when a key is renewed (when traffic is dropped on the server).
    
    Args:
        key_id: Key ID
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys 
            SET traffic_notified_pct = 100, traffic_used = 0, traffic_updated_at = NULL
            WHERE id = ?
        """, (key_id,))

def update_key_traffic_limit(key_id: int, traffic_limit_bytes: int) -> None:
    """
    Updates the traffic limit for the key.
    Used when replacing a key (remaining transfer) and during monthly reset.
    
    Args:
        key_id: Key ID
        traffic_limit_bytes: New traffic limit in bytes
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys SET traffic_limit = ? WHERE id = ?
        """, (traffic_limit_bytes, key_id))

def update_vpn_key_tariff_and_traffic_limit(
    key_id: int,
    tariff_id: int,
    traffic_limit_bytes: int
) -> bool:
    """
    Applies the paid tariff to the existing key and adds a traffic package.

    traffic_limit_bytes=0 means purchasing unlimited. To switch from unlimited
    for a limit tariff, the new limit becomes traffic_used + purchased package,
    so that the user receives a full new balance from the current moment.
    """
    with get_db() as conn:
        row = conn.execute("""
            SELECT traffic_limit, traffic_used
            FROM vpn_keys
            WHERE id = ?
        """, (key_id,)).fetchone()
        if not row:
            return False

        current_limit = row['traffic_limit'] or 0
        current_used = row['traffic_used'] or 0
        if traffic_limit_bytes <= 0:
            new_limit = 0
        elif current_limit <= 0:
            new_limit = current_used + traffic_limit_bytes
        else:
            new_limit = current_limit + traffic_limit_bytes

        cursor = conn.execute("""
            UPDATE vpn_keys
            SET tariff_id = ?,
                traffic_limit = ?,
                traffic_notified_pct = 100
            WHERE id = ?
        """, (tariff_id, new_limit, key_id))
        success = cursor.rowcount > 0
        if success:
            limit_gb = new_limit / (1024 ** 3) if new_limit > 0 else 0
            limit_text = f"{limit_gb:.1f} ГБ" if new_limit > 0 else "безлимит"
            added_gb = traffic_limit_bytes / (1024 ** 3) if traffic_limit_bytes > 0 else 0
            logger.info(
                f"Ключ ID {key_id} переведён на тариф {tariff_id}, "
                f"добавлено: {added_gb:.1f} ГБ, накопительный лимит: {limit_text}"
            )
        return success

def update_vpn_key_config(
    key_id: int,
    server_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    sub_id: Optional[str] = ...,
) -> bool:
    """
    Updates the key configuration (binds it to the server).
    Used to complete the key setup.

    Args:
        key_id: Key ID
        server_id: Server ID
        panel_inbound_id: ID inbound on the panel
        panel_email: Panel email
        client_uuid: Client UUID
        sub_id: Subscription ID. If passed (including None) - updated
                in the database. Default (Ellipsis) - the field is not touched.

    Returns:
        True if successful
    """
    with get_db() as conn:
        if sub_id is ...:
            cursor = conn.execute("""
                UPDATE vpn_keys
                SET server_id = ?,
                    panel_inbound_id = ?,
                    panel_email = ?,
                    client_uuid = ?
                WHERE id = ?
            """, (server_id, panel_inbound_id, panel_email, client_uuid, key_id))
        else:
            cursor = conn.execute("""
                UPDATE vpn_keys
                SET server_id = ?,
                    panel_inbound_id = ?,
                    panel_email = ?,
                    client_uuid = ?,
                    sub_id = ?
                WHERE id = ?
            """, (server_id, panel_inbound_id, panel_email, client_uuid, sub_id, key_id))
        return cursor.rowcount > 0


def update_vpn_key_sub_id(key_id: int, sub_id: Optional[str]) -> bool:
    """
    Updates the sub_id of the key.

    Args:
        key_id: Key ID
        sub_id: New subscription ID (or None to clear)

    Returns:
        True if successful
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE vpn_keys SET sub_id = ? WHERE id = ?",
            (sub_id, key_id),
        )
        return cursor.rowcount > 0


def create_vpn_key_subscription_admin(
    user_id: int,
    server_id: int,
    tariff_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    sub_id: str,
    days: int,
    traffic_limit: int = 0,
) -> int:
    """
    Creates a VPN key by the administrator in subscription mode.

    Similar to create_vpn_key_admin, but additionally records sub_id.

    Args:
        user_id: Internal user ID
        server_id: Server ID
        tariff_id: Tariff ID
        panel_inbound_id: Minimum inbound ID (for compatibility)
        panel_email: Client email (common for all inbound)
        client_uuid: UUID of the client from the minimum inbound
        sub_id: Subscription ID (one for all inbounds of this key)
        days: Validity period in days
        traffic_limit: Traffic limit in bytes (0 = unlimited)

    Returns:
        Created key ID
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO vpn_keys
            (user_id, server_id, tariff_id, panel_inbound_id, panel_email,
             client_uuid, sub_id, expires_at, traffic_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+' || ? || ' days'), ?)
        """, (user_id, server_id, tariff_id, panel_inbound_id, panel_email,
              client_uuid, sub_id, days, traffic_limit))
        key_id = cursor.lastrowid
        logger.info(
            f"Администратор создал subscription-ключ ID {key_id} для user_id {user_id} "
            f"(sub_id={sub_id[:8]}...)"
        )
        return key_id

def delete_vpn_key(key_id: int) -> bool:
    """
    Removes the VPN key from the database.
    Also deletes connections with payments and notification logs so as not to violate FOREIGN KEY.
    
    Args:
        key_id: Key ID
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        # Remove the link in the payment history (to save the history itself)
        conn.execute("UPDATE payments SET vpn_key_id = NULL WHERE vpn_key_id = ?", (key_id,))
        # Deleting notification logs
        conn.execute("DELETE FROM notification_log WHERE vpn_key_id = ?", (key_id,))
        
        # We delete the key itself
        cursor = conn.execute("DELETE FROM vpn_keys WHERE id = ?", (key_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Ключ ID {key_id} удален из БД")
        return success

def get_user_keys_for_display(telegram_id: int) -> List[Dict[str, Any]]:
    """
    Retrieves the user's keys for display in the My Keys section.
    
    Args:
        telegram_id: Telegram user ID
    
    Returns:
        List of keys with fields: id, display_name, server_name, protocol,
        expires_at, is_active (not expired), is_enabled, traffic_info
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.client_uuid, vk.custom_name, vk.expires_at,
                s.name as server_name, s.id as server_id, vk.panel_email,
                vk.sub_id,
                vk.traffic_used, vk.traffic_limit,
                t.name as tariff_name, t.max_ips as tariff_max_ips,
                CASE
                    WHEN vk.expires_at > datetime('now') THEN 1
                    ELSE 0
                END as is_active
            FROM vpn_keys vk
            LEFT JOIN servers s ON vk.server_id = s.id
            LEFT JOIN tariffs t ON vk.tariff_id = t.id
            JOIN users u ON vk.user_id = u.id
            WHERE u.telegram_id = ?
            ORDER BY vk.expires_at DESC
        """, (telegram_id,))
        
        keys = []
        for row in cursor.fetchall():
            key = dict(row)
            # Forming display_name
            if key['custom_name']:
                key['display_name'] = key['custom_name']
            elif key['client_uuid']:
                uuid = key['client_uuid']
                key['display_name'] = f"{uuid[:4]}...{uuid[-4:]}"
            else:
                if not key['server_id']:
                     key['display_name'] = f"Ключ #{key['id']} (Не настроен)"
                else:
                     key['display_name'] = f"Ключ #{key['id']}"
            keys.append(key)
        
        return keys

def get_key_details_for_user(key_id: int, telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Receives detailed information about the key with verification of ownership.
    
    Args:
        key_id: Key ID
        telegram_id: Telegram user ID
    
    Returns:
        Dictionary with key data or None if not found or not owned
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.*, 
                s.name as server_name, s.id as server_id,
                t.name as tariff_name, t.duration_days, t.price_rub, t.price_minor,
                COALESCE((SELECT value FROM settings WHERE key = 'base_currency'), 'RUB') AS base_currency,
                t.max_ips as tariff_max_ips,
                u.telegram_id, u.username,
                s.is_active as server_active,
                CASE 
                    WHEN vk.expires_at > datetime('now') THEN 1 
                    ELSE 0 
                END as is_active
            FROM vpn_keys vk
            LEFT JOIN servers s ON vk.server_id = s.id
            LEFT JOIN tariffs t ON vk.tariff_id = t.id
            JOIN users u ON vk.user_id = u.id
            WHERE vk.id = ? AND u.telegram_id = ?
        """, (key_id, telegram_id))
        row = cursor.fetchone()
        if not row:
            return None
        
        key = dict(row)
        # Forming display_name
        if key['custom_name']:
            key['display_name'] = key['custom_name']
        elif key['client_uuid']:
            uuid = key['client_uuid']
            key['display_name'] = f"{uuid[:4]}...{uuid[-4:]}"
        else:
            if not key['server_id']:
                 key['display_name'] = f"Ключ #{key['id']} (Не настроен)"
            else:
                 key['display_name'] = f"Ключ #{key['id']}"
        
        return key

def update_key_custom_name(key_id: int, telegram_id: int, new_name: str) -> bool:
    """
    Updates the custom key name.
    
    Args:
        key_id: Key ID
        telegram_id: Telegram ID of the owner
        new_name: New name (or empty string to reset)
    
    Returns:
        True if successful
    """
    if new_name and len(new_name) > 30:
        logger.warning(f"Попытка установить слишком длинное имя ключа {key_id}: {new_name}")
        return False

    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        return False
    
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys SET custom_name = ? WHERE id = ?
        """, (new_name or None, key_id))
        logger.info(f"Ключ {key_id}: переименован в '{new_name}'")
        return True

def add_days_to_first_active_key(user_id: int, days: int) -> bool:
    """
    Add days to the user's first active key.
    
    Args:
        user_id: Internal user ID
        days: Number of days to add
    
    Returns:
        True if successful, False if there are no active keys
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id FROM vpn_keys 
            WHERE user_id = ? AND expires_at > datetime('now')
            ORDER BY expires_at DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        
        if not row:
            logger.info(f"Нет активных ключей у пользователя {user_id} для добавления дней")
            return False
        
        key_id = row['id']
        conn.execute("""
            UPDATE vpn_keys 
            SET expires_at = datetime(expires_at, '+' || ? || ' days')
            WHERE id = ?
        """, (days, key_id))
        
        logger.info(f"Ключ {key_id} пользователя {user_id} продлён на {days} дней (реферальное вознаграждение)")
        return True

def get_user_by_panel_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Finds the key owner user by panel_email from the 3X-UI panel.
    
    Args:
        email: Email (client ID) in the proxy panel
    
    Returns:
        Dictionary with user data or None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT u.* FROM users u
            JOIN vpn_keys vk ON u.id = vk.user_id
            WHERE LOWER(vk.panel_email) = LOWER(?)
            LIMIT 1
        """, (email,))
        row = cursor.fetchone()
        return dict(row) if row else None
