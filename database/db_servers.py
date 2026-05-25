import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_all_servers',
    'get_server_by_id',
    'get_active_servers',
    'add_server',
    'update_server',
    'update_server_field',
    'update_server_api_token',
    'update_server_panel_info',
    'delete_server',
    'toggle_server_active',
]

SERVER_SELECT_FIELDS = """
    id, name, host, port, web_base_path, login, password, is_active, protocol,
    api_token, panel_version, panel_api_profile, panel_checked_at
"""

def get_all_servers() -> List[Dict[str, Any]]:
    """
    Получает список всех VPN-серверов.

    Returns:
        Список словарей с данными серверов
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT """ + SERVER_SELECT_FIELDS + """
            FROM servers
            ORDER BY id
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_server_by_id(server_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает сервер по ID.

    Args:
        server_id: ID сервера

    Returns:
        Словарь с данными сервера или None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT """ + SERVER_SELECT_FIELDS + """
            FROM servers
            WHERE id = ?
        """, (server_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_active_servers() -> List[Dict[str, Any]]:
    """
    Получает список активных VPN-серверов.

    Returns:
        Список словарей с данными активных серверов
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT """ + SERVER_SELECT_FIELDS + """
            FROM servers
            WHERE is_active = 1
            ORDER BY id
        """)
        return [dict(row) for row in cursor.fetchall()]

def add_server(
    name: str,
    host: str,
    port: int,
    web_base_path: str,
    login: str,
    password: str,
    protocol: str = 'https',
    group_id: int = 1
) -> int:
    """
    Добавляет новый VPN-сервер.
    
    Args:
        name: Название сервера
        host: IP-адрес или домен
        port: Порт панели 3X-UI
        web_base_path: Секретный путь API
        login: Логин для панели
        password: Пароль для панели
        protocol: Протокол подключения (http/https)
        group_id: ID группы тарифов (по умолчанию 1 — «Основная»)
        
    Returns:
        ID созданного сервера
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO servers (name, host, port, web_base_path, login, password, is_active, protocol)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, (name, host, port, web_base_path, login, password, protocol))
        server_id = cursor.lastrowid
        
        # Добавляем сервер в таблицу связей server_groups
        conn.execute(
            "INSERT INTO server_groups (server_id, group_id) VALUES (?, ?)",
            (server_id, group_id)
        )
        
        logger.info(f"Добавлен сервер: {name} (ID: {server_id}, группа: {group_id})")
        return server_id

def update_server(server_id: int, **fields) -> bool:
    """
    Обновляет поля сервера.
    
    Args:
        server_id: ID сервера
        **fields: Поля для обновления (name, host, port, web_base_path, login, password, protocol)
        
    Returns:
        True если обновление успешно
    """
    allowed_fields = {
        'name', 'host', 'port', 'web_base_path', 'login', 'password',
        'is_active', 'protocol', 'api_token', 'panel_version',
        'panel_api_profile', 'panel_checked_at',
    }
    fields = {k: v for k, v in fields.items() if k in allowed_fields}
    
    if not fields:
        return False
    
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [server_id]
    
    with get_db() as conn:
        cursor = conn.execute(f"""
            UPDATE servers
            SET {set_clause}
            WHERE id = ?
        """, values)
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Обновлён сервер ID {server_id}: {list(fields.keys())}")
        return success

def update_server_api_token(server_id: int, token: Optional[str]) -> bool:
    """
    Атомарно обновляет Bearer-токен сервера (3x-ui v3.0+).

    Передаётся token=None для очистки (например, после ротации токена админом
    в UI панели — наш сохранённый токен становится невалидным и его надо стереть,
    чтобы следующий логин подтянул новый).

    Args:
        server_id: ID сервера
        token: API-токен из 3x-ui (строка ~48 символов) или None для очистки

    Returns:
        True если сервер существует
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE servers SET api_token = ? WHERE id = ?",
            (token, server_id)
        )
        success = cursor.rowcount > 0
        if success:
            if token:
                logger.info(f"Сохранён api_token для сервера ID {server_id} (3x-ui v3.0+)")
            else:
                logger.info(f"Очищен api_token для сервера ID {server_id}")
        return success


def update_server_panel_info(
    server_id: int,
    version: Optional[str],
    api_profile: Optional[str],
) -> bool:
    """
    Обновляет кеш диагностики API панели 3x-ui.

    Args:
        server_id: ID сервера
        version: Версия панели, если удалось определить
        api_profile: 'legacy_inbounds' или 'clients_api'

    Returns:
        True если сервер существует
    """
    checked_at = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE servers
            SET panel_version = ?,
                panel_api_profile = ?,
                panel_checked_at = ?
            WHERE id = ?
            """,
            (version, api_profile, checked_at, server_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(
                f"Обновлена диагностика 3x-ui для сервера ID {server_id}: "
                f"version={version or 'unknown'}, profile={api_profile or 'unknown'}"
            )
        return success


def update_server_field(server_id: int, field: str, value: Any) -> bool:
    """
    Обновляет одно поле сервера.
    
    Args:
        server_id: ID сервера
        field: Название поля
        value: Новое значение
        
    Returns:
        True если обновление успешно
    """
    return update_server(server_id, **{field: value})

def delete_server(server_id: int) -> bool:
    """
    Удаляет сервер.
    
    Args:
        server_id: ID сервера
        
    Returns:
        True если удаление успешно
    """
    with get_db() as conn:
        # Сначала отвязываем ключи от этого сервера, чтобы не нарушить Foreign Key
        conn.execute("UPDATE vpn_keys SET server_id = NULL WHERE server_id = ?", (server_id,))
        
        cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Удалён сервер ID {server_id}")
        return success

def toggle_server_active(server_id: int) -> Optional[bool]:
    """
    Переключает активность сервера.
    
    Args:
        server_id: ID сервера
        
    Returns:
        Новый статус (True = активен) или None если сервер не найден
    """
    server = get_server_by_id(server_id)
    if not server:
        return None
    
    new_status = 0 if server['is_active'] else 1
    
    with get_db() as conn:
        conn.execute("""
            UPDATE servers
            SET is_active = ?
            WHERE id = ?
        """, (new_status, server_id))
        logger.info(f"Сервер ID {server_id}: is_active = {new_status}")
        return bool(new_status)
