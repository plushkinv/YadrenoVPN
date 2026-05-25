import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

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
    Получает список всех групп тарифов, отсортированных по sort_order.
    
    Returns:
        Список словарей с данными групп
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
    Получает группу по ID.
    
    Args:
        group_id: ID группы
        
    Returns:
        Словарь с данными группы или None
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
    Добавляет новую группу тарифов.
    sort_order = максимальный существующий + 1 (но не больше 99).
    
    Args:
        name: Название группы
        
    Returns:
        ID созданной группы
    """
    with get_db() as conn:
        # Определяем следующий sort_order
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
    Переименовывает группу тарифов.
    
    Args:
        group_id: ID группы
        name: Новое название
        
    Returns:
        True если обновление успешно
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
    Удаляет группу тарифов. Группу id=1 («Основная») удалить нельзя.
    Тарифы и серверы из удалённой группы переносятся в группу id=1.
    
    Args:
        group_id: ID группы для удаления
        
    Returns:
        True если удаление успешно, False если группа не найдена или id=1
    """
    if group_id == 1:
        logger.warning("Попытка удалить группу «Основная» (id=1) — запрещено")
        return False
    
    with get_db() as conn:
        # Переносим тарифы и серверы в «Основную»
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
    Поднимает группу вверх в сортировке (swap с предыдущей).
    Группа с минимальным sort_order при нажатии ⬆️ уходит в конец (получает макс. sort_order).
    
    Args:
        group_id: ID группы для перемещения
        
    Returns:
        True если перемещение выполнено
    """
    with get_db() as conn:
        # Получаем текущую группу
        cursor = conn.execute("SELECT id, sort_order FROM tariff_groups WHERE id = ?", (group_id,))
        current = cursor.fetchone()
        if not current:
            return False
        
        current_order = current['sort_order']
        
        # Ищем предыдущую группу (ближайшую с sort_order < текущего)
        cursor = conn.execute("""
            SELECT id, sort_order FROM tariff_groups
            WHERE sort_order < ?
            ORDER BY sort_order DESC
            LIMIT 1
        """, (current_order,))
        prev_group = cursor.fetchone()
        
        if prev_group:
            # Swap sort_order между текущей и предыдущей
            conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (prev_group['sort_order'], group_id))
            conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (current_order, prev_group['id']))
            logger.info(f"Группа ID {group_id}: swap sort_order {current_order} <-> {prev_group['sort_order']}")
        else:
            # Текущая группа уже первая — перемещаем в конец
            cursor = conn.execute("SELECT MAX(sort_order) FROM tariff_groups")
            max_order = cursor.fetchone()[0] or 1
            if max_order != current_order:
                conn.execute("UPDATE tariff_groups SET sort_order = ? WHERE id = ?", (max_order + 1, group_id))
                logger.info(f"Группа ID {group_id}: перемещена в конец (sort_order={max_order + 1})")
        
        return True

def get_groups_count() -> int:
    """
    Возвращает количество групп тарифов.
    
    Returns:
        Число групп
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM tariff_groups")
        return cursor.fetchone()[0]

def get_tariffs_by_group(group_id: int) -> List[Dict[str, Any]]:
    """
    Получает активные тарифы указанной группы.
    
    Args:
        group_id: ID группы
        
    Returns:
        Список тарифов группы
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, duration_days, price_cents, price_stars, price_rub, 
                   display_order, is_active, traffic_limit_gb, group_id
            FROM tariffs
            WHERE group_id = ? AND is_active = 1
            ORDER BY display_order, id
        """, (group_id,))
        return [dict(row) for row in cursor.fetchall()]

def get_active_servers_by_group(group_id: int) -> List[Dict[str, Any]]:
    """
    Получает активные серверы указанной группы (many-to-many через server_groups).
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
    Возвращает список ID групп, в которые входит сервер.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT group_id FROM server_groups WHERE server_id = ? ORDER BY group_id",
            (server_id,)
        )
        return [row[0] for row in cursor.fetchall()]

def toggle_server_group(server_id: int, group_id: int) -> bool:
    """
    Добавляет или удаляет сервер из группы (toggle).
    Нельзя удалить из последней группы — сервер должен быть хотя бы в одной.

    Returns:
        True если сервер теперь входит в группу, False если удалён
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM server_groups WHERE server_id = ? AND group_id = ?",
            (server_id, group_id)
        )
        exists = cursor.fetchone() is not None

        if exists:
            # Нельзя удалять последнюю группу
            cursor = conn.execute(
                "SELECT COUNT(*) FROM server_groups WHERE server_id = ?",
                (server_id,)
            )
            if cursor.fetchone()[0] <= 1:
                logger.warning(f"Сервер ID {server_id}: нельзя удалить последнюю группу {group_id}")
                return True  # Остаётся в группе
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
    Получает group_id тарифа.
    
    Args:
        tariff_id: ID тарифа
        
    Returns:
        ID группы тарифа (1 по умолчанию если не найден)
    """
    with get_db() as conn:
        cursor = conn.execute("SELECT group_id FROM tariffs WHERE id = ?", (tariff_id,))
        row = cursor.fetchone()
        return row['group_id'] if row else 1
