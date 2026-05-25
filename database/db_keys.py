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
    'bulk_update_traffic',
    'update_key_traffic',
    'update_key_notified_pct',
    'reset_key_traffic_notification',
    'update_key_traffic_limit',
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
    Получает все VPN-ключи пользователя с данными о тарифе и сервере.
    
    Args:
        user_id: Внутренний ID пользователя (users.id)
    
    Returns:
        Список ключей с полной информацией
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
    Получает VPN-ключ по ID с полной информацией.
    
    Args:
        key_id: ID ключа
    
    Returns:
        Словарь с данными ключа или None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.*,
                t.name as tariff_name, t.duration_days, t.price_cents,
                s.name as server_name, s.host, s.port, s.web_base_path,
                s.login, s.password, s.protocol, s.api_token,
                s.panel_version, s.panel_api_profile, s.panel_checked_at,
                s.is_active as server_active,
                u.telegram_id, u.username
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
    Продлевает VPN-ключ на указанное количество дней.
    
    Args:
        key_id: ID ключа
        days: Количество дней для продления
    
    Returns:
        True если успешно
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
    Создаёт VPN-ключ администратором (без оплаты).
    
    Args:
        user_id: Внутренний ID пользователя
        server_id: ID сервера
        tariff_id: ID тарифа
        panel_inbound_id: ID inbound в панели
        panel_email: Email (идентификатор) клиента в панели
        client_uuid: UUID клиента
        days: Срок действия в днях
        traffic_limit: Лимит трафика в байтах (0 = безлимит)
    
    Returns:
        ID созданного ключа
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
    Обновляет технические данные ключа (сервер, UUID, inbound).
    Используется при замене ключа.

    Args:
        key_id: ID ключа
        server_id: ID нового сервера
        panel_inbound_id: ID inbound в панели
        panel_email: Email (идентификатор) клиента в панели
        client_uuid: Новый UUID клиента
        sub_id: Subscription ID. Если передан (включая None) — обновляется
                в БД. По умолчанию (Ellipsis) — поле не трогается.

    Returns:
        True если успешно
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
    Создаёт полностью настроенный VPN-ключ (обертка над create_vpn_key_admin).
    Для создания черновика используйте create_initial_vpn_key.
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
    Создаёт начальный (черновой) VPN-ключ без привязки к серверу.
    Ключ создается сразу после оплаты.
    
    Args:
        user_id: ID пользователя
        tariff_id: ID тарифа
        days: Срок действия (дней)
        traffic_limit: Лимит трафика в байтах (0 = безлимит)
        
    Returns:
        ID созданного ключа
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
    Проверяет активность ключа (дата + трафик).
    Единая точка проверки статуса ключа для всего проекта.
    
    Args:
        key: Словарь с данными ключа (должен содержать expires_at, traffic_limit, traffic_used)
    
    Returns:
        True если ключ активен
    """
    from datetime import datetime
    
    # Проверка срока действия
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
    
    # Проверка трафика
    traffic_limit = key.get('traffic_limit', 0) or 0
    traffic_used = key.get('traffic_used', 0) or 0
    if traffic_limit > 0 and traffic_used >= traffic_limit:
        return False
    
    return True

def is_traffic_exhausted(key: dict) -> bool:
    """
    Проверяет, исчерпан ли трафик ключа.
    
    Returns:
        True если трафик исчерпан (traffic_used >= traffic_limit > 0)
    """
    traffic_limit = key.get('traffic_limit', 0) or 0
    traffic_used = key.get('traffic_used', 0) or 0
    return traffic_limit > 0 and traffic_used >= traffic_limit

def get_all_active_keys_with_server() -> List[Dict[str, Any]]:
    """
    Получает все активные ключи с данными сервера.
    Для планировщика синхронизации трафика.
    
    Returns:
        Список ключей с данными сервера и пользователя
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

def get_all_keys_with_server() -> List[Dict[str, Any]]:
    """
    Получает ВСЕ ключи с привязкой к серверу (включая истёкшие).
    Для синхронизации удалённых ключей.
    
    Returns:
        Список ключей с данными сервера и пользователя
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
    Массовое обновление трафика для ключей.
    
    Args:
        updates: Список кортежей (traffic_used, key_id)
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

def update_key_traffic(key_id: int, traffic_used: int) -> None:
    """
    Обновляет трафик для одного ключа.
    
    Args:
        key_id: ID ключа
        traffic_used: Израсходованный трафик в байтах
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys 
            SET traffic_used = ?, traffic_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (traffic_used, key_id))

def update_key_notified_pct(key_id: int, pct: int) -> None:
    """
    Обновляет последний порог уведомления о трафике.
    
    Args:
        key_id: ID ключа
        pct: Порог в % (10, 5, 3, 2, 1, 0)
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys SET traffic_notified_pct = ? WHERE id = ?
        """, (pct, key_id))

def reset_key_traffic_notification(key_id: int) -> None:
    """
    Сбрасывает уведомления о трафике и кеш использования.
    Вызывается при продлении ключа (когда трафик сброшен на сервере).
    
    Args:
        key_id: ID ключа
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys 
            SET traffic_notified_pct = 100, traffic_used = 0, traffic_updated_at = NULL
            WHERE id = ?
        """, (key_id,))

def update_key_traffic_limit(key_id: int, traffic_limit_bytes: int) -> None:
    """
    Обновляет лимит трафика для ключа.
    Используется при замене ключа (перенос остатка) и при ежемесячном сбросе.
    
    Args:
        key_id: ID ключа
        traffic_limit_bytes: Новый лимит трафика в байтах
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE vpn_keys SET traffic_limit = ? WHERE id = ?
        """, (traffic_limit_bytes, key_id))

def update_vpn_key_config(
    key_id: int,
    server_id: int,
    panel_inbound_id: int,
    panel_email: str,
    client_uuid: str,
    sub_id: Optional[str] = ...,
) -> bool:
    """
    Обновляет конфигурацию ключа (привязывает к серверу).
    Используется для завершения настройки ключа.

    Args:
        key_id: ID ключа
        server_id: ID сервера
        panel_inbound_id: ID inbound на панели
        panel_email: Email на панели
        client_uuid: UUID клиента
        sub_id: Subscription ID. Если передан (включая None) — обновляется
                в БД. По умолчанию (Ellipsis) — поле не трогается.

    Returns:
        True если успешно
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
    Обновляет sub_id ключа.

    Args:
        key_id: ID ключа
        sub_id: Новый subscription ID (или None для очистки)

    Returns:
        True если успешно
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
    Создаёт VPN-ключ администратором в режиме subscription.

    Аналогичен create_vpn_key_admin, но дополнительно записывает sub_id.

    Args:
        user_id: Внутренний ID пользователя
        server_id: ID сервера
        tariff_id: ID тарифа
        panel_inbound_id: ID минимального inbound (для совместимости)
        panel_email: Email клиента (общий для всех inbound)
        client_uuid: UUID клиента из минимального inbound
        sub_id: Subscription ID (один на все inbound этого ключа)
        days: Срок действия в днях
        traffic_limit: Лимит трафика в байтах (0 = безлимит)

    Returns:
        ID созданного ключа
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
    Удаляет VPN-ключ из базы данных.
    Также удаляет связь с платежами и логи уведомлений, чтобы не нарушать FOREIGN KEY.
    
    Args:
        key_id: ID ключа
    
    Returns:
        True если успешно
    """
    with get_db() as conn:
        # Убираем привязку в истории оплат (чтобы сохранить саму историю)
        conn.execute("UPDATE payments SET vpn_key_id = NULL WHERE vpn_key_id = ?", (key_id,))
        # Удаляем логи уведомлений
        conn.execute("DELETE FROM notification_log WHERE vpn_key_id = ?", (key_id,))
        
        # Удаляем сам ключ
        cursor = conn.execute("DELETE FROM vpn_keys WHERE id = ?", (key_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Ключ ID {key_id} удален из БД")
        return success

def get_user_keys_for_display(telegram_id: int) -> List[Dict[str, Any]]:
    """
    Получает ключи пользователя для отображения в разделе «Мои ключи».
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Список ключей с полями: id, display_name, server_name, protocol,
        expires_at, is_active (не истёк), is_enabled, traffic_info
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                vk.id, vk.client_uuid, vk.custom_name, vk.expires_at,
                s.name as server_name, s.id as server_id, vk.panel_email,
                vk.sub_id,
                vk.traffic_used, vk.traffic_limit,
                CASE
                    WHEN vk.expires_at > datetime('now') THEN 1
                    ELSE 0
                END as is_active
            FROM vpn_keys vk
            LEFT JOIN servers s ON vk.server_id = s.id
            JOIN users u ON vk.user_id = u.id
            WHERE u.telegram_id = ?
            ORDER BY vk.expires_at DESC
        """, (telegram_id,))
        
        keys = []
        for row in cursor.fetchall():
            key = dict(row)
            # Формируем display_name
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
    Получает детальную информацию о ключе с проверкой принадлежности.
    
    Args:
        key_id: ID ключа
        telegram_id: Telegram ID пользователя
    
    Returns:
        Словарь с данными ключа или None если не найден или не принадлежит
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.*, 
                s.name as server_name, s.id as server_id,
                t.name as tariff_name, t.duration_days, t.price_cents, t.price_stars,
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
        # Формируем display_name
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
    Обновляет пользовательское имя ключа.
    
    Args:
        key_id: ID ключа
        telegram_id: Telegram ID владельца
        new_name: Новое имя (или пустая строка для сброса)
    
    Returns:
        True если успешно
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
    Добавить дни к первому активному ключу пользователя.
    
    Args:
        user_id: Внутренний ID пользователя
        days: Количество дней для добавления
    
    Returns:
        True если успешно, False если нет активных ключей
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
    Находит пользователя-владельца ключа по panel_email из панели 3X-UI.
    
    Args:
        email: Email (идентификатор клиента) в панели прокси
    
    Returns:
        Словарь с данными пользователя или None
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
