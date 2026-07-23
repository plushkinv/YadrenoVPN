import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db
from .db_tariffs import _base_currency_and_rub_rate, normalize_tariff_money

logger = logging.getLogger(__name__)

__all__ = [
    'get_all_groups',
    'get_group_by_id',
    'add_group',
    'update_group_name',
    'delete_group',
    'move_group_up',
    'get_groups_count',
    'get_tariffs_by_group',
    'get_active_servers_by_group',
    'get_server_group_ids',
    'toggle_server_group',
    'get_tariff_group_id',
]

def get_all_groups() -> List[Dict[str, Any]]:
    """
    Gets a list of all rate groups sorted by sort_order.
    
    Returns:
        List of dictionaries with group data
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, sort_order, created_at
            FROM tariff_groups
            ORDER BY sort_order, id
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_group_by_id(group_id: int) -> Optional[Dict[str, Any]]:
    """
    Gets a group by ID.
    
    Args:
        group_id: Group ID
        
    Returns:
        Dictionary with group data or None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, sort_order, created_at
            FROM tariff_groups
            WHERE id = ?
        """, (group_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def add_group(name: str) -> int:
    """
    Adds a new tariff group.
    sort_order = maximum existing + 1 (but not more than 99).
    
    Args:
        name: Group name
        
    Returns:
        ID of the created group
    """
    with get_db() as conn:
        # Define the next sort_order
        cursor = conn.execute("SELECT MAX(sort_order) FROM tariff_groups")
        max_order = cursor.fetchone()[0] or 0
        new_order = min(max_order + 1, 99)
        
        cursor = conn.execute("""
            INSERT INTO tariff_groups (name, sort_order)
            VALUES (?, ?)
        """, (name, new_order))
        group_id = cursor.lastrowid
        logger.info(f"Добавлена группа тарифов: {name} (ID: {group_id}, sort_order: {new_order})")
        return group_id

def update_group_name(group_id: int, name: str) -> bool:
    """
    Renames a tariff group.
    
    Args:
        group_id: Group ID
        name: New name
        
    Returns:
        True if update is successful
    """
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE tariff_groups
            SET name = ?
            WHERE id = ?
        """, (name, group_id))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Группа ID {group_id} переименована в '{name}'")
        return success

def delete_group(group_id: int) -> bool:
    """
    Deletes a tariff group. Group id=1 (“Main”) cannot be deleted.
    Tariffs and servers from the deleted group are transferred to group id=1.
    
    Args:
        group_id: Group ID to delete
        
    Returns:
        True if deletion is successful, False if group not found or id=1
    """
    if group_id == 1:
        logger.warning("Попытка удалить группу «Основная» (id=1) — запрещено")
        return False
    
    with get_db() as conn:
        # We transfer tariffs and servers to “Main”
        conn.execute("UPDATE tariffs SET group_id = 1 WHERE group_id = ?", (group_id,))
        conn.execute("""
            INSERT OR IGNORE INTO server_groups (server_id, group_id)
            SELECT server_id, 1 FROM server_groups WHERE group_id = ?
        """, (group_id,))
        
        cursor = conn.execute("DELETE FROM tariff_groups WHERE id = ?", (group_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Удалена группа ID {group_id}, тарифы/серверы перенесены в «Основная»")
        return success

def move_group_up(group_id: int) -> bool:
    """
    Moves a group up in sorting (swap from the previous one).
    When you press ⬆️, the group with the minimum sort_order goes to the end (receives the maximum sort_order).
    
    Args:
        group_id: ID of the group to move
        
    Returns:
        True if the move is complete
    """
    with get_db() as conn:
        # Getting the current group
        cursor = conn.execute("SELECT id, sort_order FROM tariff_groups WHERE id = ?", (group_id,))
        current = cursor.fetchone()
        if not current:
            return False
        
        current_order = current['sort_order']
        
        # We are looking for the previous group (the closest one with sort_order < current)
        cursor = conn.execute("""
            SELECT id, sort_order FROM tariff_groups
            WHERE sort_order < ?
            ORDER BY sort_order DESC
            LIMIT 1
        """, (current_order,))
        prev_group = cursor.fetchone()
        
        if prev_group:
            # Swap sort_order between current and previous
            conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (prev_group['sort_order'], group_id))
            conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (current_order, prev_group['id']))
            logger.info(f"Группа ID {group_id}: swap sort_order {current_order} <-> {prev_group['sort_order']}")
        else:
            # The current group is already the first - move it to the end
            cursor = conn.execute("SELECT MAX(sort_order) FROM tariff_groups")
            max_order = cursor.fetchone()[0] or 1
            if max_order != current_order:
                conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (max_order + 1, group_id))
                logger.info(f"Группа ID {group_id}: перемещена в конец (sort_order={max_order + 1})")
        
        return True

def get_groups_count() -> int:
    """
    Returns the number of tariff groups.
    
    Returns:
        Number of groups
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM tariff_groups")
        return cursor.fetchone()[0]

def get_tariffs_by_group(group_id: int) -> List[Dict[str, Any]]:
    """
    Retrieves active tariffs of the specified group.
    
    Args:
        group_id: Group ID
        
    Returns:
        List of group rates
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, duration_days, price_rub, price_minor,
                   display_order, is_active, traffic_limit_gb, group_id
            FROM tariffs
            WHERE group_id = ? AND is_active = 1
            ORDER BY display_order, id
        """, (group_id,))
        base, rub_rate = _base_currency_and_rub_rate(conn)
        return [
            normalize_tariff_money(dict(row), base_currency=base, rub_rate=rub_rate)
            for row in cursor.fetchall()
        ]

def get_active_servers_by_group(group_id: int) -> List[Dict[str, Any]]:
    """
    Gets the active servers of the specified group (many-to-many via server_groups).
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT s.id, s.name, s.host, s.port, s.web_base_path, s.login,
                   s.password, s.is_active, s.protocol, s.api_token,
                   s.panel_version, s.panel_api_profile, s.panel_checked_at
            FROM servers s
            JOIN server_groups sg ON sg.server_id = s.id
            WHERE sg.group_id = ? AND s.is_active = 1
            ORDER BY s.id
        """, (group_id,))
        return [dict(row) for row in cursor.fetchall()]

def get_server_group_ids(server_id: int) -> List[int]:
    """
    Returns a list of group IDs that the server is a member of.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT group_id FROM server_groups WHERE server_id = ? ORDER BY group_id",
            (server_id,)
        )
        return [row[0] for row in cursor.fetchall()]

def toggle_server_group(server_id: int, group_id: int) -> bool:
    """
    Adds or removes a server from a group (toggle).
    You cannot remove from the last group - the server must be in at least one.

    Returns:
        True if the server is now part of the group, False if deleted
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM server_groups WHERE server_id = ? AND group_id = ?",
            (server_id, group_id)
        )
        exists = cursor.fetchone() is not None

        if exists:
            # You can't delete the last group
            cursor = conn.execute(
                "SELECT COUNT(*) FROM server_groups WHERE server_id = ?",
                (server_id,)
            )
            if cursor.fetchone()[0] <= 1:
                logger.warning(f"Сервер ID {server_id}: нельзя удалить последнюю группу {group_id}")
                return True  # Stays in the group
            conn.execute(
                "DELETE FROM server_groups WHERE server_id = ? AND group_id = ?",
                (server_id, group_id)
            )
            logger.info(f"Сервер ID {server_id} удалён из группы {group_id}")
            return False
        else:
            conn.execute(
                "INSERT INTO server_groups (server_id, group_id) VALUES (?, ?)",
                (server_id, group_id)
            )
            logger.info(f"Сервер ID {server_id} добавлен в группу {group_id}")
            return True

def get_tariff_group_id(tariff_id: int) -> int:
    """
    Gets the group_id of the tariff.
    
    Args:
        tariff_id: Tariff ID
        
    Returns:
        Fare group ID (1 by default if not found)
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT group_id FROM tariffs WHERE id = ?", (tariff_id,))
        row = cursor.fetchone()
        return row['group_id'] if row else 1
