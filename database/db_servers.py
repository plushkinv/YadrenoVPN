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
    Gets a list of all VPN servers.

    Returns:
        List of dictionaries with server data
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
    Gets the server by ID.

    Args:
        server_id: Server ID

    Returns:
        Dictionary with server data or None
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
    Gets a list of active VPN servers.

    Returns:
        List of dictionaries with data from active servers
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
    Adds a new VPN server.
    
    Args:
        name: Server name
        host: IP address or domain
        port: 3X-UI panel port
        web_base_path: Secret API path
        login: Login for the panel
        password: Password for the panel
        protocol: Connection protocol (http/https)
        group_id: tariff group ID (default 1 - “Main”)
        
    Returns:
        ID of the created server
    """
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO servers (name, host, port, web_base_path, login, password, is_active, protocol)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, (name, host, port, web_base_path, login, password, protocol))
        server_id = cursor.lastrowid
        
        # Add the server to the server_groups connection table
        conn.execute(
            "INSERT INTO server_groups (server_id, group_id) VALUES (?, ?)",
            (server_id, group_id)
        )
        
        logger.info(f"Добавлен сервер: {name} (ID: {server_id}, группа: {group_id})")
        return server_id

def update_server(server_id: int, **fields) -> bool:
    """
    Updates server fields.
    
    Args:
        server_id: Server ID
        **fields: Fields to update (name, host, port, web_base_path, login, password, protocol)
        
    Returns:
        True if update is successful
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
    Atomically updates the server's Bearer token (3x-ui v3.0+).

    Token=None is passed for cleaning (for example, after the token is rotated by the admin
    in the UI panel - our saved token becomes invalid and must be erased,
    so that the next login will pull up a new one).

    Args:
        server_id: Server ID
        token: API token from 3x-ui (~48 characters string) or None to clear

    Returns:
        True if the server exists
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
    Updates the 3x-ui panel API diagnostic cache.

    Args:
        server_id: Server ID
        version: Panel version, if it was possible to determine
        api_profile: 'legacy_inbounds' or 'clients_api'

    Returns:
        True if the server exists
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
    Updates one server field.
    
    Args:
        server_id: Server ID
        field: Field name
        value: New value
        
    Returns:
        True if update is successful
    """
    return update_server(server_id, **{field: value})

def delete_server(server_id: int) -> bool:
    """
    Deletes the server.
    
    Args:
        server_id: Server ID
        
    Returns:
        True if deletion is successful
    """
    with get_db() as conn:
        # First, we unbind the keys from this server so as not to break the Foreign Key
        conn.execute("UPDATE vpn_keys SET server_id = NULL WHERE server_id = ?", (server_id,))
        
        cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Удалён сервер ID {server_id}")
        return success

def toggle_server_active(server_id: int) -> Optional[bool]:
    """
    Toggles server activity.
    
    Args:
        server_id: Server ID
        
    Returns:
        New status (True = active) or None if the server is not found
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
