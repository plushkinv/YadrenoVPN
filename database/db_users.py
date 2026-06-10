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
    """Генерация уникального 8-символьного кода (A-Z, a-z, 0-9)."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def get_or_create_user(telegram_id: int, username: Optional[str] = None) -> tuple[Dict[str, Any], bool]:
    """
    Получает или создаёт пользователя.
    
    Args:
        telegram_id: Telegram ID пользователя
        username: @username (опционально)
        
    Returns:
        Кортеж (user_dict, is_new):
        - user_dict: словарь с данными пользователя
        - is_new: True если пользователь был создан, False если уже существовал
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        
        if row:
            if username and row['username'] != username:
                conn.execute(
                    "UPDATE users SET username = ? WHERE telegram_id = ?",
                    (username, telegram_id)
                )
            return dict(row), False
        
        referral_code = _generate_referral_code()
        attempts = 0
        while attempts < 100:
            cursor = conn.execute("SELECT 1 FROM users WHERE referral_code = ?", (referral_code,))
            if not cursor.fetchone():
                break
            referral_code = _generate_referral_code()
            attempts += 1
        
        cursor = conn.execute(
            "INSERT INTO users (telegram_id, username, referral_code) VALUES (?, ?, ?)",
            (telegram_id, username, referral_code)
        )
        logger.info(f"Новый пользователь: {telegram_id} (@{username}), referral_code: {referral_code}")
        
        return {
            'id': cursor.lastrowid,
            'telegram_id': telegram_id,
            'username': username,
            'is_banned': 0,
            'is_bot_blocked': 0,
            'referral_code': referral_code,
            'referred_by': None,
            'personal_balance': 0,
            'referral_coefficient': 1.0
        }, True

def is_user_banned(telegram_id: int) -> bool:
    """
    Проверяет, забанен ли пользователь.
    
    Args:
        telegram_id: Telegram ID пользователя
        
    Returns:
        True если пользователь забанен
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT is_banned FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return bool(row['is_banned']) if row else False

def mark_user_bot_blocked(telegram_id: int) -> bool:
    """Помечает пользователя как недоступного для сообщений бота."""
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
    """Снимает флаг недоступности, когда пользователь снова обратился в бот."""
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
    Проверяет, использовал ли пользователь пробную подписку.
    
    Args:
        telegram_id: Telegram ID пользователя
        
    Returns:
        True если пользователь уже использовал пробный период
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
    Помечает, что пользователь использовал пробную подписку.
    
    Args:
        user_id: Внутренний ID пользователя (не Telegram ID)
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET used_trial = 1 WHERE id = ?",
            (user_id,)
        )
        logger.info(f"Пользователь ID {user_id} использовал пробный период")

def get_all_users_count() -> int:
    """
    Возвращает общее количество пользователей (не забаненных).
    
    Returns:
        Количество пользователей
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 0")
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def get_users_stats() -> Dict[str, int]:
    """
    Возвращает статистику пользователей по фильтрам (как в рассылке).
    
    Returns:
        Словарь с количеством пользователей по категориям:
        - total: все не забаненные
        - active: с активными ключами
        - inactive: без активных ключей
        - never_paid: никогда не покупали
        - expired: был ключ, но истёк
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
    Получает список пользователей с пагинацией и фильтрацией.
    
    Args:
        offset: Смещение для пагинации
        limit: Количество на странице (по умолчанию 20)
        filter_type: Тип фильтра (all, active, inactive, never_paid, expired)
    
    Returns:
        Кортеж (список пользователей, общее количество)
    """
    with get_db() as conn:
        # Базовый запрос с данными о ключах
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
        
        # Получаем общее количество
        cursor = conn.execute(count_query)
        total = cursor.fetchone()['cnt']
        
        # Получаем страницу
        cursor = conn.execute(f"{base_query} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        users = [dict(row) for row in cursor.fetchall()]
        
        return users, total

def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает пользователя по Telegram ID.
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Словарь с данными пользователя или None
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
    Получает пользователя по @username.
    
    Args:
        username: Username без @
    
    Returns:
        Словарь с данными пользователя или None
    """
    # Убираем @ если передали с ним
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
    Переключает бан пользователя.
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Новый статус (True = забанен) или None если не найден
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
    Получает количество новых пользователей за последние 24 часа.
    
    Returns:
        Количество новых пользователей
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
    Получает внутренний ID пользователя по Telegram ID.
    
    Args:
        telegram_id: Telegram ID
    
    Returns:
        Внутренний ID (users.id) или None
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
    Найти пользователя по реферальному коду.
    
    Args:
        code: Реферальный код (8 символов)
    
    Returns:
        Словарь с данными пользователя или None
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
    Привязать реферера к пользователю.
    
    Args:
        user_id: ID пользователя (того, кого пригласили)
        referrer_id: ID пригласившего (реферера)
    
    Returns:
        True если успешно
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
    Получить ID пригласившего пользователя (referred_by).
    
    Args:
        user_id: Внутренний ID пользователя
    
    Returns:
        ID реферера или None
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
    Убедиться что у пользователя есть реферальный код, вернуть его.
    FALLBACK: используется только если код не был создан при регистрации.
    
    Args:
        user_id: Внутренний ID пользователя
    
    Returns:
        Реферальный код пользователя
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
    Получить баланс пользователя в копейках.
    
    Args:
        user_id: Внутренний ID пользователя
    
    Returns:
        Баланс в копейках (0 если пользователь не найден)
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
    Добавить к балансу. СИНХРОННАЯ функция, вызывается внутри async with user_locks[user_id].
    
    Args:
        user_id: Внутренний ID пользователя
        cents: Сумма в копейках
    
    Returns:
        True если успешно
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
    Списать с баланса. СИНХРОННАЯ функция, вызывается внутри async with user_locks[user_id].
    
    Args:
        user_id: Внутренний ID пользователя
        cents: Сумма в копейках
    
    Returns:
        True если успешно
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
    Получить индивидуальный коэффициент реферальных отчислений.
    
    Args:
        user_id: Внутренний ID пользователя
    
    Returns:
        Коэффициент (по умолчанию 1.0)
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
    Установить индивидуальный коэффициент реферальных отчислений.
    
    Args:
        user_id: Внутренний ID пользователя
        coefficient: Коэффициент (0.0 - 10.0)
    
    Returns:
        True если успешно
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
