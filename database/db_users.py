import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

from .db_stats import count_users_for_broadcast


__all__ = [
    '_generate_referral_code',
    'get_or_create_user',
    'is_user_banned',
    'mark_user_bot_blocked',
    'mark_user_bot_unblocked',
    'has_used_trial',
    'mark_trial_used',
    'get_all_users_count',
    'get_users_stats',
    'get_all_users_paginated',
    'get_user_by_id',
    'get_user_by_telegram_id',
    'get_user_by_username',
    'toggle_user_ban',
    'get_new_users_count_today',
    'get_user_internal_id',
    'get_user_by_referral_code',
    'set_user_referrer',
    'get_user_referrer',
    'ensure_user_referral_code',
    'get_user_balance',
    'add_to_balance',
    'deduct_from_balance',
    'get_user_referral_coefficient',
    'set_user_referral_coefficient',
]

def _generate_referral_code() -> str:
    """Generate a unique 8-character code (A-Z, a-z, 0-9)."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def get_or_create_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> tuple[Dict[str, Any], bool]:
    """
    Gets or creates a user.
    
    Args:
        telegram_id: Telegram user ID
        username: @username (optional)
        
    Returns:
        Tuple (user_dict, is_new):
        - user_dict: dictionary with user data
        - is_new: True if the user was created, False if already existed
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        
        if row:
            user = dict(row)
            updates = {}
            if username and user.get('username') != username:
                updates['username'] = username
            if 'first_name' in user and first_name and user.get('first_name') != first_name:
                updates['first_name'] = first_name
            if 'last_name' in user and last_name and user.get('last_name') != last_name:
                updates['last_name'] = last_name

            if updates:
                set_clause = ', '.join(f"{field} = ?" for field in updates)
                params = list(updates.values()) + [telegram_id]
                conn.execute(
                    f"UPDATE users SET {set_clause} WHERE telegram_id = ?",
                    params
                )
                user.update(updates)

            return user, False
        
        referral_code = _generate_referral_code()
        attempts = 0
        while attempts < 100:
            cursor = conn.execute("SELECT 1 FROM users WHERE referral_code = ?", (referral_code,))
            if not cursor.fetchone():
                break
            referral_code = _generate_referral_code()
            attempts += 1
        
        user_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        fields = ['telegram_id', 'username', 'referral_code']
        values = [telegram_id, username, referral_code]
        if 'first_name' in user_columns:
            fields.append('first_name')
            values.append(first_name)
        if 'last_name' in user_columns:
            fields.append('last_name')
            values.append(last_name)

        placeholders = ', '.join('?' for _ in fields)
        cursor = conn.execute(
            f"INSERT INTO users ({', '.join(fields)}) VALUES ({placeholders})",
            values
        )
        logger.info(f"Новый пользователь: {telegram_id} (@{username}), referral_code: {referral_code}")
        
        return {
            'id': cursor.lastrowid,
            'telegram_id': telegram_id,
            'username': username,
            'first_name': first_name,
            'last_name': last_name,
            'is_banned': 0,
            'is_bot_blocked': 0,
            'referral_code': referral_code,
            'referred_by': None,
            'personal_balance': 0,
            'referral_coefficient': 1.0
        }, True

def is_user_banned(telegram_id: int) -> bool:
    """
    Checks if the user is banned.
    
    Args:
        telegram_id: Telegram user ID
        
    Returns:
        True if the user is banned
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT is_banned FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return bool(row['is_banned']) if row else False

def mark_user_bot_blocked(telegram_id: int) -> bool:
    """Marks the user as unavailable for bot messages."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_bot_blocked = 1 WHERE telegram_id = ?",
            (telegram_id,)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Пользователь {telegram_id} помечен как заблокировавший бота")
        return success

def mark_user_bot_unblocked(telegram_id: int) -> bool:
    """Removes the unavailable flag when the user contacts the bot again."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_bot_blocked = 0 WHERE telegram_id = ? AND is_bot_blocked = 1",
            (telegram_id,)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Пользователь {telegram_id} снова доступен для сообщений бота")
        return success

def has_used_trial(telegram_id: int) -> bool:
    """
    Checks whether the user has used a trial subscription.
    
    Args:
        telegram_id: Telegram user ID
        
    Returns:
        True if the user has already used the trial period
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT used_trial FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return bool(row['used_trial']) if row else False

def mark_trial_used(user_id: int) -> None:
    """
    Indicates that the user has used a trial subscription.
    
    Args:
        user_id: Internal user ID (not Telegram ID)
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET used_trial = 1 WHERE id = ?",
            (user_id,)
        )
        logger.info(f"Пользователь ID {user_id} использовал пробный период")

def get_all_users_count() -> int:
    """
    Returns the total number of users (not banned).
    
    Returns:
        Number of users
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 0")
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def get_users_stats() -> Dict[str, int]:
    """
    Returns user statistics by filters (as in the newsletter).
    
    Returns:
        Dictionary with the number of users by category:
        - total: all not banned
        - active: with active keys
        - inactive: no active keys
        - never_paid: never purchased
        - expired: there was a key, but it expired
    """
    with get_db() as conn:
        def count(query: str) -> int:
            cursor = conn.execute(query)
            row = cursor.fetchone()
            return row['cnt'] if row else 0

        return {
            'total': count("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 0"),
            'active': count("""
                SELECT COUNT(DISTINCT u.id) as cnt FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0 AND vk.expires_at > datetime('now')
            """),
            'inactive': count("""
                SELECT COUNT(*) as cnt FROM users u
                WHERE u.is_banned = 0
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys
                    WHERE expires_at > datetime('now')
                )
            """),
            'never_paid': count("""
                SELECT COUNT(*) as cnt FROM users u
                WHERE u.is_banned = 0
                AND u.id NOT IN (SELECT DISTINCT user_id FROM vpn_keys)
            """),
            'expired': count("""
                SELECT COUNT(DISTINCT u.id) as cnt FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0
                AND vk.expires_at <= datetime('now')
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys
                    WHERE expires_at > datetime('now')
                )
            """),
            'bot_blocked': count("""
                SELECT COUNT(*) as cnt FROM users
                WHERE is_banned = 0 AND is_bot_blocked = 1
            """),
        }

def get_all_users_paginated(offset: int = 0, limit: int = 20, 
                             filter_type: str = 'all') -> tuple[List[Dict[str, Any]], int]:
    """
    Gets a list of users with pagination and filtering.
    
    Args:
        offset: Offset for pagination
        limit: Number per page (default 20)
        filter_type: Filter type (all, active, inactive, never_paid, expired)
    
    Returns:
        Tuple (list of users, total number)
    """
    with get_db() as conn:
        # Basic query with key data
        if filter_type == 'all':
            base_query = "SELECT * FROM users WHERE is_banned = 0"
            count_query = "SELECT COUNT(*) as cnt FROM users WHERE is_banned = 0"
        elif filter_type == 'active':
            base_query = """
                SELECT DISTINCT u.* FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0 AND vk.expires_at > datetime('now')
            """
            count_query = """
                SELECT COUNT(DISTINCT u.id) as cnt FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0 AND vk.expires_at > datetime('now')
            """
        elif filter_type == 'inactive':
            base_query = """
                SELECT u.* FROM users u
                WHERE u.is_banned = 0 
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """
            count_query = """
                SELECT COUNT(*) as cnt FROM users u
                WHERE u.is_banned = 0 
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """
        elif filter_type == 'never_paid':
            base_query = """
                SELECT u.* FROM users u
                WHERE u.is_banned = 0 
                AND u.id NOT IN (SELECT DISTINCT user_id FROM vpn_keys)
            """
            count_query = """
                SELECT COUNT(*) as cnt FROM users u
                WHERE u.is_banned = 0 
                AND u.id NOT IN (SELECT DISTINCT user_id FROM vpn_keys)
            """
        elif filter_type == 'expired':
            base_query = """
                SELECT DISTINCT u.* FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0 
                AND vk.expires_at <= datetime('now')
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """
            count_query = """
                SELECT COUNT(DISTINCT u.id) as cnt FROM users u
                JOIN vpn_keys vk ON u.id = vk.user_id
                WHERE u.is_banned = 0 
                AND vk.expires_at <= datetime('now')
                AND u.id NOT IN (
                    SELECT DISTINCT user_id FROM vpn_keys 
                    WHERE expires_at > datetime('now')
                )
            """
        elif filter_type == 'bot_blocked':
            base_query = """
                SELECT * FROM users
                WHERE is_banned = 0 AND is_bot_blocked = 1
            """
            count_query = """
                SELECT COUNT(*) as cnt FROM users
                WHERE is_banned = 0 AND is_bot_blocked = 1
            """
        else:
            return [], 0
        
        # We get the total quantity
        cursor = conn.execute(count_query)
        total = cursor.fetchone()['cnt']
        
        # We get the page
        cursor = conn.execute(f"{base_query} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        users = [dict(row) for row in cursor.fetchall()]
        
        return users, total

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Gets the user by internal ID."""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Gets the user by Telegram ID.
    
    Args:
        telegram_id: Telegram user ID
    
    Returns:
        Dictionary with user data or None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """
    Gets the user by @username.
    
    Args:
        username: Username without @
    
    Returns:
        Dictionary with user data or None
    """
    # Remove @ if it was passed along with it
    username = username.lstrip('@')
    
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (username,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def toggle_user_ban(telegram_id: int) -> Optional[bool]:
    """
    Toggles user ban.
    
    Args:
        telegram_id: Telegram user ID
    
    Returns:
        New status (True = banned) or None if not found
    """
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return None
    
    new_status = 0 if user['is_banned'] else 1
    
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET is_banned = ? WHERE telegram_id = ?",
            (new_status, telegram_id)
        )
        status_text = "забанен" if new_status else "разбанен"
        logger.info(f"Пользователь {telegram_id}: {status_text}")
        return bool(new_status)

def get_new_users_count_today() -> int:
    """
    Gets the number of new users in the last 24 hours.
    
    Returns:
        Number of new users
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM users 
            WHERE created_at >= datetime('now', '-1 day')
        """)
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def get_user_internal_id(telegram_id: int) -> Optional[int]:
    """
    Gets the internal user ID from the Telegram ID.
    
    Args:
        telegram_id: Telegram ID
    
    Returns:
        Internal ID (users.id) or None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return row['id'] if row else None

def get_user_by_referral_code(code: str) -> Optional[Dict[str, Any]]:
    """
    Find a user by referral code.
    
    Args:
        code: Referral code (8 characters)
    
    Returns:
        Dictionary with user data or None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE referral_code = ?",
            (code,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def set_user_referrer(user_id: int, referrer_id: int) -> bool:
    """
    Link the referrer to the user.
    
    Args:
        user_id: User ID (the one who was invited)
        referrer_id: ID of the inviter (referrer)
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET referred_by = ? WHERE id = ? AND referred_by IS NULL",
            (referrer_id, user_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Пользователь {user_id} привязан к рефереру {referrer_id}")
        return success

def get_user_referrer(user_id: int) -> Optional[int]:
    """
    Get the ID of the inviting user (referred_by).
    
    Args:
        user_id: Internal user ID
    
    Returns:
        Referrer ID or None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT referred_by FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row['referred_by'] if row else None

def ensure_user_referral_code(user_id: int) -> str:
    """
    Make sure that the user has a referral code and return it.
    FALLBACK: only used if the code was not created during registration.
    
    Args:
        user_id: Internal user ID
    
    Returns:
        User referral code
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT referral_code FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        
        if row and row['referral_code']:
            return row['referral_code']
        
        referral_code = _generate_referral_code()
        attempts = 0
        while attempts < 100:
            cursor = conn.execute("SELECT 1 FROM users WHERE referral_code = ?", (referral_code,))
            if not cursor.fetchone():
                break
            referral_code = _generate_referral_code()
            attempts += 1
        
        conn.execute(
            "UPDATE users SET referral_code = ? WHERE id = ?",
            (referral_code, user_id)
        )
        logger.info(f"Сгенерирован referral_code для user_id {user_id}: {referral_code}")
        return referral_code

def get_user_balance(user_id: int) -> int:
    """
    Get the user's balance in kopecks.
    
    Args:
        user_id: Internal user ID
    
    Returns:
        Balance in kopecks (0 if the user is not found)
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT personal_balance FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row['personal_balance'] if row else 0

def add_to_balance(user_id: int, cents: int) -> bool:
    """
    Add to balance. SYNCHRONOUS function, called inside async with user_locks[user_id].
    
    Args:
        user_id: Internal user ID
        cents: Amount in kopecks
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET personal_balance = personal_balance + ? WHERE id = ?",
            (cents, user_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Баланс пользователя {user_id} пополнен на {cents} копеек")
        return success

def deduct_from_balance(user_id: int, cents: int) -> bool:
    """
    Write off from balance sheet. SYNCHRONOUS function, called inside async with user_locks[user_id].
    
    Args:
        user_id: Internal user ID
        cents: Amount in kopecks
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET personal_balance = personal_balance - ? WHERE id = ? AND personal_balance >= ?",
            (cents, user_id, cents)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"С баланса пользователя {user_id} списано {cents} копеек")
        return success

def get_user_referral_coefficient(user_id: int) -> float:
    """
    Get an individual referral rate.
    
    Args:
        user_id: Internal user ID
    
    Returns:
        Coefficient (default 1.0)
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT referral_coefficient FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row['referral_coefficient'] if row else 1.0

def set_user_referral_coefficient(user_id: int, coefficient: float) -> bool:
    """
    Set an individual referral rate.
    
    Args:
        user_id: Internal user ID
        coefficient: Coefficient (0.0 - 10.0)
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET referral_coefficient = ? WHERE id = ?",
            (coefficient, user_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Коэффициент пользователя {user_id} установлен: {coefficient}")
        return success
