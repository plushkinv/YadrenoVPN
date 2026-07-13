import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_users_for_broadcast',
    'count_users_for_broadcast',
    'get_expiring_keys',
    'is_notification_sent_today',
    'log_notification_sent',
    'get_keys_stats',
]

def get_users_for_broadcast(filter_type: str) -> List[int]:
    """
    Gets a list of telegram_id users for mailing.
    
    Args:
        filter_type: Filter type:
            - 'all': all non-banned users
            - 'active': with active (not expired) keys
            - 'inactive': no active keys
            - 'never_paid': never purchased a VPN
            - 'expired': there was a key, but it expired
    
    Returns:
        List of telegram_id users
    """
    with get_db() as conn:
        if filter_type == 'all':
            # All not banned
            cursor = conn.execute("""
                SELECT telegram_id FROM users
                WHERE is_banned = 0 AND is_bot_blocked = 0
            """)
        elif filter_type == 'active':
            # There is at least one unexpired key
            cursor = conn.execute("""
                SELECT DISTINCT u.telegram_id 
                FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0
                AND u.is_bot_blocked = 0
                AND vk.expires_at > datetime('now')
            """)
        elif filter_type == 'inactive':
            # No active keys (either all expired or never existed)
            cursor = conn.execute("""
                SELECT u.telegram_id 
                FROM users u
                WHERE u.is_banned = 0
                AND u.is_bot_blocked = 0
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """)
        elif filter_type == 'never_paid':
            # Never bought a VPN (no keys at all)
            cursor = conn.execute("""
                SELECT u.telegram_id 
                FROM users u
                WHERE u.is_banned = 0
                AND u.is_bot_blocked = 0
                AND u.id NOT IN (SELECT DISTINCT user_id FROM vpn_keys)
            """)
        elif filter_type == 'expired':
            # There was a key, but it has already expired (and there are no active ones)
            cursor = conn.execute("""
                SELECT DISTINCT u.telegram_id 
                FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0
                AND u.is_bot_blocked = 0
                AND vk.expires_at <= datetime('now')
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """)
        else:
            return []
        
        return [row['telegram_id'] for row in cursor.fetchall()]

def count_users_for_broadcast(filter_type: str) -> int:
    """
    Counts the number of users for the newsletter.
    
    Args:
        filter_type: Filter type (see get_users_for_broadcast)
    
    Returns:
        Number of users
    """
    return len(get_users_for_broadcast(filter_type))

def get_expiring_keys(days: int) -> List[Dict[str, Any]]:
    """
    Retrieves keys that will expire in the next N days (but have not yet expired).
    
    Args:
        days: Number of days until expiration
    
    Returns:
        List of dictionaries: vpn_key_id, user_telegram_id, expires_at, custom_name, days_left
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.id as vpn_key_id,
                u.telegram_id as user_telegram_id,
                vk.expires_at,
                vk.custom_name,
                CAST((julianday(vk.expires_at) - julianday('now')) AS INTEGER) as days_left
            FROM vpn_keys vk
            JOIN users u ON vk.user_id = u.id
            WHERE u.is_banned = 0
            AND u.is_bot_blocked = 0
            AND vk.expires_at > datetime('now')
            AND vk.expires_at <= datetime('now', '+' || ? || ' days')
        """, (days,))
        return [dict(row) for row in cursor.fetchall()]

def is_notification_sent_today(vpn_key_id: int) -> bool:
    """
    Checks whether a notification was sent for this key today.
    
    Args:
        vpn_key_id: VPN key ID
    
    Returns:
        True if the notification has already been sent today
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 1 FROM notification_log
            WHERE vpn_key_id = ? AND sent_at = date('now')
        """, (vpn_key_id,))
        return cursor.fetchone() is not None

def log_notification_sent(vpn_key_id: int) -> None:
    """
    Records the fact that a notification was sent.
    
    Args:
        vpn_key_id: VPN key ID
    """
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO notification_log (vpn_key_id, sent_at)
            VALUES (?, date('now'))
        """, (vpn_key_id,))
        logger.debug(f"Записано уведомление для ключа {vpn_key_id}")

def get_keys_stats() -> Dict[str, int]:
    """
    Gets VPN key statistics.
    
    Returns:
        Dictionary with statistics:
        - total: total keys
        - active: active (not expired)
        - expired: expired
        - created_today: created in the last 24 hours
    """
    with get_db() as conn:
        # Total keys
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM vpn_keys")
        total = cursor.fetchone()['cnt']
        
        # Active (not expired)
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM vpn_keys 
            WHERE expires_at > datetime('now')
        """)
        active = cursor.fetchone()['cnt']
        
        # Created per day
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM vpn_keys 
            WHERE created_at >= datetime('now', '-1 day')
        """)
        created_today = cursor.fetchone()['cnt']
        
        return {
            'total': total,
            'active': active,
            'expired': total - active,
            'created_today': created_today
        }
