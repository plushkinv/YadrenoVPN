"""
Фасад для работы с API VPN-панелей.
"""
import json
import logging
import uuid as _uuid
from typing import Optional, Dict, Any, List
import asyncio

from .panels.base import VPNAPIError, BaseVPNClient
from .panels.xui import XUIClient
from .panels.marzban import MarzbanClient

logger = logging.getLogger(__name__)

_clients: Dict[int, BaseVPNClient] = {}

# Per-key locks для ensure_subscription_keys_on_server (защита от гонок)
_ensure_locks: Dict[int, asyncio.Lock] = {}


def get_bot_mode() -> str:
    """
    Возвращает текущий глобальный режим работы бота.

    Returns:
        'subscription' (по умолчанию) или 'key'
    """
    try:
        from database.db_settings import get_setting
        value = get_setting('bot_mode', 'subscription') or 'subscription'
        return value if value in ('subscription', 'key') else 'subscription'
    except Exception as e:
        logger.warning(f"get_bot_mode: ошибка чтения settings, fallback subscription: {e}")
        return 'subscription'


def is_subscription_mode() -> bool:
    """True, если бот работает в режиме Subscription."""
    return get_bot_mode() == 'subscription'

def get_client_from_server_data(server: Dict[str, Any]) -> BaseVPNClient:
    """
    Создает или возвращает экземпляр клиента для API панели.
    """
    server_id = server['id']
    if server_id in _clients:
        return _clients[server_id]
        
    pass_type = server.get('panel_type', 'xui')
    if pass_type == 'marzban':
        client = MarzbanClient(server)
    else:
        client = XUIClient(server)
        
    _clients[server_id] = client
    return client

def invalidate_client_cache(server_id: int):
    """Инвалидирует сессию клиента."""
    if server_id in _clients:
        client = _clients[server_id]
        import asyncio
        asyncio.create_task(client.close())
        del _clients[server_id]
        logger.debug(f'Кэш клиента {server_id} очищен')

def format_traffic(bytes_count: int) -> str:
    """Форматирует байты в читабельный вид."""
    if bytes_count < 1024:
        return f'{bytes_count} B'
    elif bytes_count < 1024 ** 2:
        return f'{bytes_count / 1024:.1f} KB'
    elif bytes_count < 1024 ** 3:
        return f'{bytes_count / 1024 ** 2:.1f} MB'
    elif bytes_count < 1024 ** 4:
        return f'{bytes_count / 1024 ** 3:.2f} GB'
    else:
        return f'{bytes_count / 1024 ** 4:.2f} TB'

async def close_all_clients():
    """Закрывает все открытые сессии клиентов."""
    for client in list(_clients.values()):
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Ошибка при закрытии клиента: {e}")
    _clients.clear()

async def get_client(server_id: int) -> XUIClient:
    """
    Получает клиент для сервера по ID (из БД).
    
    Args:
        server_id: ID сервера в БД
        
    Returns:
        Экземпляр XUIClient
        
    Raises:
        ValueError: Если сервер не найден
    """
    from database.requests import get_server_by_id
    if server_id in _clients:
        return _clients[server_id]
    server = get_server_by_id(server_id)
    if not server:
        raise ValueError(f'Сервер с ID {server_id} не найден')
    return get_client_from_server_data(server)

async def test_server_connection(server_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет подключение к серверу.
    
    Args:
        server_data: Словарь с данными сервера
        
    Returns:
        Словарь с результатом:
        - success: True если подключение успешно
        - message: Сообщение о результате
        - stats: Статистика (если успешно)
    """
    client = XUIClient(server_data)
    try:
        await client.login()
        stats = await client.get_stats()
        return {'success': True, 'message': 'Подключение успешно!', 'stats': stats}
    except VPNAPIError as e:
        return {'success': False, 'message': f'Ошибка: {e}', 'stats': None}
    finally:
        await client.close()

async def reset_key_traffic_if_active(key_id: int) -> bool:
    """
    Сбрасывает израсходованный трафик ключа в панели 3X-UI,
    если сервер активен.
    
    Args:
        key_id: ID ключа (VPNKey.id)
        
    Returns:
        True при успешном сбросе, иначе False.
    """
    from database.requests import get_vpn_key_by_id
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get('server_active'):
        return False
    server_data = {'id': key.get('server_id'), 'name': key.get('server_name'), 'host': key.get('host'), 'port': key.get('port'), 'web_base_path': key.get('web_base_path'), 'login': key.get('login'), 'password': key.get('password')}
    inbound_id = key.get('panel_inbound_id')
    email = key.get('panel_email')
    if not email:
        if key.get('username'):
            email = f"user_{key['username']}"
        else:
            email = f"user_{key['telegram_id']}"
    try:
        client = get_client_from_server_data(server_data)
        success = await client.reset_client_traffic(inbound_id, email)
        if success:
            logger.info(f'Трафик ключа {key_id} успешно сброшен при продлении.')
        return success
    except Exception as e:
        logger.error(f'Не удалось сбросить трафик ключа {key_id} при продлении: {e}')
        return False

async def extend_key_on_server(key_id: int, days: int) -> bool:
    """
    Продлевает срок действия ключа в панели 3X-UI, если сервер активен.
    
    Args:
        key_id: ID ключа (VPNKey.id)
        days: Количество дней для продления
        
    Returns:
        True при успешном продлении, иначе False.
    """
    from database.requests import get_vpn_key_by_id
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get('server_active'):
        return False
    server_data = {'id': key.get('server_id'), 'name': key.get('server_name'), 'host': key.get('host'), 'port': key.get('port'), 'web_base_path': key.get('web_base_path'), 'login': key.get('login'), 'password': key.get('password')}
    inbound_id = key.get('panel_inbound_id')
    client_uuid = key.get('client_uuid')
    email = key.get('panel_email')
    if not email:
        email = f"user_{key.get('username') or key.get('telegram_id')}"
    try:
        client = get_client_from_server_data(server_data)
        success = await client.extend_client_expiry(inbound_id, client_uuid, email, days)
        if success:
            logger.info(f'Срок действия ключа {key_id} успешно продлен на сервере на {days} дней.')
        return success
    except Exception as e:
        logger.error(f'Не удалось продлить срок действия ключа {key_id} на сервере: {e}')
        return False


async def restore_key_traffic_limit(key_id: int) -> bool:
    """
    Восстанавливает полный лимит трафика тарифа на панели и обнуляет traffic_used в БД.
    Вызывается при продлении ключа (после reset_key_traffic_if_active).
    
    Делает 3 вещи:
    1. Получает лимит из тарифа ключа
    2. Обновляет totalGB на панели до полного лимита тарифа
    3. Обнуляет traffic_used и сбрасывает пороги уведомлений в БД
    
    Args:
        key_id: ID ключа
        
    Returns:
        True при успехе, False при ошибке
    """
    from database.requests import (
        get_vpn_key_by_id, get_tariff_by_id,
        reset_key_traffic_notification, update_key_traffic_limit
    )
    
    key = get_vpn_key_by_id(key_id)
    if not key:
        return False
    
    # Получаем лимит из тарифа
    tariff_id = key.get('tariff_id')
    traffic_limit = key.get('traffic_limit', 0) or 0
    
    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff and (tariff.get('traffic_limit_gb', 0) or 0) > 0:
            traffic_limit = tariff['traffic_limit_gb'] * (1024**3)
    
    # Обнуляем traffic_used и сбрасываем пороги в БД
    reset_key_traffic_notification(key_id)
    
    # Обновляем traffic_limit в БД (на случай если тариф менялся)
    if traffic_limit > 0:
        update_key_traffic_limit(key_id, traffic_limit)
    
    # Обновляем totalGB на панели
    if key.get('server_active') and key.get('panel_email') and traffic_limit > 0:
        try:
            server_data = {
                'id': key.get('server_id'), 'name': key.get('server_name'),
                'host': key.get('host'), 'port': key.get('port'),
                'web_base_path': key.get('web_base_path'),
                'login': key.get('login'), 'password': key.get('password')
            }
            client = get_client_from_server_data(server_data)
            await client.update_client_limit(
                inbound_id=key.get('panel_inbound_id'),
                client_uuid=key.get('client_uuid'),
                email=key.get('panel_email'),
                total_gb_bytes=traffic_limit
            )
            logger.info(f'Лимит ключа {key_id} восстановлен на панели: {traffic_limit / 1024**3:.1f} ГБ')
        except Exception as e:
            logger.error(f'Не удалось восстановить лимит ключа {key_id} на панели: {e}')
            return False
    
    return True


async def push_key_to_panel(key_id: int, reset_traffic: bool = False) -> bool:
    """
    Пушит данные ключа из нашей БД на панель 3X-UI.
    
    Единственная точка записи на панель. Все данные (expiryTime, totalGB)
    формируются из нашей БД, а не читаются с панели.
    
    Args:
        key_id: ID ключа в нашей БД
        reset_traffic: True = обнулить счётчики up/down на панели перед обновлением
        
    Returns:
        True при успешном обновлении, False при ошибке
    """
    from database.requests import get_vpn_key_by_id
    from datetime import datetime
    
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get('server_active'):
        logger.warning(f'push_key_to_panel: ключ {key_id} не найден или сервер неактивен')
        return False
    
    email = key.get('panel_email')
    inbound_id = key.get('panel_inbound_id')
    client_uuid = key.get('client_uuid')
    
    if not email or not inbound_id or not client_uuid:
        logger.warning(f'push_key_to_panel: ключ {key_id} — неполные данные панели')
        return False
    
    # Конвертируем expires_at из БД → expiryTime (ms)
    expires_at = key.get('expires_at')
    if expires_at:
        from datetime import datetime, timedelta, timezone
        
        # Если есть 'Z', убираем/заменяем, парсим
        dt_str = str(expires_at).replace('Z', '+00:00')
        dt = datetime.fromisoformat(dt_str)
        
        # Убеждаемся что tzinfo установлен (в БД время всегда UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        now_utc = datetime.now(timezone.utc)
        
        # Если срок больше 90000 дней (бессрочный)
        if dt > now_utc + timedelta(days=90000):
            expiry_time_ms = 0
        else:
            expiry_time_ms = int(dt.timestamp() * 1000)
    else:
        expiry_time_ms = 0  # Бессрочный
    
    # Лимит трафика из БД (уже в байтах)
    traffic_limit = key.get('traffic_limit', 0) or 0
    
    try:
        server_data = {
            'id': key.get('server_id'),
            'name': key.get('server_name'),
            'host': key.get('host'),
            'port': key.get('port'),
            'web_base_path': key.get('web_base_path'),
            'login': key.get('login'),
            'password': key.get('password')
        }
        client = get_client_from_server_data(server_data)
        
        # Сброс счётчиков up/down на панели (если требуется)
        if reset_traffic:
            await client.reset_client_traffic(inbound_id, email)
            logger.info(f'Сброшены счётчики трафика ключа {key_id} на панели')
        
        # Обновляем ВСЕ данные клиента на панели из нашей БД
        success = await client.update_client_full(
            inbound_id=inbound_id,
            client_uuid=client_uuid,
            email=email,
            expiry_time_ms=expiry_time_ms,
            total_gb_bytes=traffic_limit
        )
        
        if success:
            logger.info(f'Данные ключа {key_id} ({email}) успешно запушены на панель')
        return success
        
    except Exception as e:
        logger.error(f'Ошибка пуша ключа {key_id} на панель: {e}')
        return False


def restore_traffic_limit_in_db(key_id: int) -> bool:
    """
    Восстанавливает полный лимит трафика тарифа в нашей БД.
    НЕ обращается к панели! Панель обновляется через push_key_to_panel.
    
    Делает:
    1. Получает лимит из тарифа ключа
    2. Обновляет traffic_limit в БД
    3. Обнуляет traffic_used и сбрасывает пороги уведомлений
    
    Args:
        key_id: ID ключа
        
    Returns:
        True при успехе
    """
    from database.requests import (
        get_vpn_key_by_id, get_tariff_by_id,
        reset_key_traffic_notification, update_key_traffic_limit
    )
    
    key = get_vpn_key_by_id(key_id)
    if not key:
        return False
    
    # Получаем лимит из тарифа
    tariff_id = key.get('tariff_id')
    traffic_limit = key.get('traffic_limit', 0) or 0
    
    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff and (tariff.get('traffic_limit_gb', 0) or 0) > 0:
            traffic_limit = tariff['traffic_limit_gb'] * (1024**3)
    
    # Обнуляем traffic_used и пороги уведомлений
    reset_key_traffic_notification(key_id)
    
    # Обновляем traffic_limit (на случай если тариф менялся)
    if traffic_limit > 0:
        update_key_traffic_limit(key_id, traffic_limit)
    
    logger.info(f'Лимит трафика ключа {key_id} восстановлен в БД: {traffic_limit / 1024**3:.1f} ГБ')
    return True


async def ensure_subscription_keys_on_server(key_id: int) -> Dict[str, int]:
    """
    Приводит набор клиентов с key.panel_email на key.server_id в соответствие
    с текущим bot_mode и состоянием ключа в БД.

    Режим 'subscription':
      - В каждом inbound сервера, где нет клиента с key.panel_email, создаёт
        клиента с key.sub_id, key.expires_at, key.traffic_limit.
        Если у ключа sub_id IS NULL — генерирует (или подхватывает существующий
        subId из найденного клиента на панели) и сохраняет в БД.
      - Обновляет vpn_keys.panel_inbound_id и client_uuid на минимальный inbound.
      - Если traffic_exhausted ИЛИ expired — set_clients_enabled_by_email(False)
        для всех клиентов с этим email.
      - Если ключ активен — set_clients_enabled_by_email(True).

    Режим 'key':
      - Оставляет клиента в МИНИМАЛЬНОМ inbound, остальных с тем же email удаляет.
      - Обновляет panel_inbound_id и client_uuid в БД на минимальный.

    Args:
        key_id: ID ключа в БД

    Returns:
        Словарь со статистикой: {'created', 'deleted', 'enabled', 'disabled'}
    """
    stats = {'created': 0, 'deleted': 0, 'enabled': 0, 'disabled': 0}

    lock = _ensure_locks.setdefault(key_id, asyncio.Lock())
    async with lock:
        from database.requests import get_vpn_key_by_id
        from database.db_keys import (
            is_key_active, is_traffic_exhausted,
            update_vpn_key_config, update_vpn_key_sub_id,
        )

        key = get_vpn_key_by_id(key_id)
        if not key:
            return stats
        if not key.get('server_active'):
            return stats
        email = key.get('panel_email')
        server_id = key.get('server_id')
        if not email or not server_id:
            return stats

        server_data = {
            'id': server_id,
            'name': key.get('server_name'),
            'host': key.get('host'),
            'port': key.get('port'),
            'web_base_path': key.get('web_base_path'),
            'login': key.get('login'),
            'password': key.get('password'),
            'protocol': key.get('protocol', 'https'),
            'api_token': key.get('api_token'),
        }

        try:
            client = get_client_from_server_data(server_data)
            inbounds = await client.get_inbounds()
        except Exception as e:
            logger.warning(f"ensure_subscription_keys: сервер {server_id} недоступен: {e}")
            return stats

        if not inbounds:
            return stats

        # presence: inbound_id -> client_obj
        presence: Dict[int, Dict[str, Any]] = {}
        for inb in inbounds:
            try:
                settings_raw = inb.get('settings', '{}')
                settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            except (json.JSONDecodeError, TypeError):
                continue
            for cl in settings.get('clients', []):
                if cl.get('email') == email:
                    presence.setdefault(inb['id'], cl)

        mode = get_bot_mode()

        if mode == 'subscription':
            # Гарантируем sub_id у ключа
            sub_id = key.get('sub_id')
            if not sub_id:
                # Подхватим subId из существующего клиента на панели, если есть
                for cl in presence.values():
                    existing = cl.get('subId')
                    if existing:
                        sub_id = existing
                        break
                if not sub_id:
                    sub_id = _uuid.uuid4().hex
                update_vpn_key_sub_id(key_id, sub_id)
                key['sub_id'] = sub_id

            # Параметры для add_client в отсутствующих inbound
            from datetime import datetime, timezone
            traffic_limit = key.get('traffic_limit', 0) or 0
            total_gb = int(traffic_limit / (1024 ** 3)) if traffic_limit > 0 else 0

            expires_at = key.get('expires_at')
            if expires_at:
                try:
                    dt = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta = dt - datetime.now(timezone.utc)
                    days_left = max(1, int(delta.total_seconds() / 86400) + (1 if delta.seconds else 0))
                except Exception:
                    days_left = 30
            else:
                days_left = 0

            active = is_key_active(key) and not is_traffic_exhausted(key)

            # Создаём в отсутствующих inbound
            missing = [inb for inb in inbounds if inb['id'] not in presence]
            for inb in missing:
                try:
                    flow = await client.get_inbound_flow(inb['id'])
                    res = await client.add_client(
                        inbound_id=inb['id'],
                        email=email,
                        total_gb=total_gb,
                        expire_days=days_left if days_left > 0 else 365,
                        limit_ip=1,
                        enable=active,
                        tg_id=str(key.get('telegram_id') or ''),
                        flow=flow,
                        sub_id=sub_id,
                    )
                    stats['created'] += 1
                    presence[inb['id']] = {
                        'email': email,
                        'id': res['uuid'],
                        'password': res['uuid'],
                        'subId': sub_id,
                        'enable': active,
                    }
                except Exception as e:
                    logger.warning(
                        f"ensure_subscription_keys: не удалось создать клиента {email} "
                        f"в inbound {inb['id']} сервера {server_id}: {e}"
                    )

            # Обновляем panel_inbound_id/client_uuid на МИНИМАЛЬНЫЙ присутствующий inbound
            if presence:
                min_inb_id = min(presence.keys())
                min_client = presence[min_inb_id]
                uuid_or_pwd = min_client.get('id') or min_client.get('password') or ''
                if (key.get('panel_inbound_id') != min_inb_id
                        or (key.get('client_uuid') or '') != uuid_or_pwd):
                    update_vpn_key_config(
                        key_id=key_id,
                        server_id=server_id,
                        panel_inbound_id=min_inb_id,
                        panel_email=email,
                        client_uuid=uuid_or_pwd,
                        sub_id=sub_id,
                    )

            # Включить/отключить всех клиентов по состоянию ключа
            target_enable = active
            need_change = any(
                bool(cl.get('enable', True)) != target_enable
                for cl in presence.values()
            )
            if need_change:
                try:
                    cnt = await client.set_clients_enabled_by_email(email, target_enable)
                    if target_enable:
                        stats['enabled'] += cnt
                    else:
                        stats['disabled'] += cnt
                except Exception as e:
                    logger.warning(
                        f"ensure_subscription_keys: не удалось переключить enable={target_enable} "
                        f"для {email} на сервере {server_id}: {e}"
                    )

        else:  # mode == 'key'
            if len(presence) <= 1:
                # Уже один или ноль клиентов — обновим только panel_inbound_id если нужно
                if presence:
                    min_inb_id = min(presence.keys())
                    min_client = presence[min_inb_id]
                    uuid_or_pwd = min_client.get('id') or min_client.get('password') or ''
                    if (key.get('panel_inbound_id') != min_inb_id
                            or (key.get('client_uuid') or '') != uuid_or_pwd):
                        update_vpn_key_config(
                            key_id=key_id,
                            server_id=server_id,
                            panel_inbound_id=min_inb_id,
                            panel_email=email,
                            client_uuid=uuid_or_pwd,
                        )
                return stats

            min_inb_id = min(presence.keys())
            for inb_id, cl in list(presence.items()):
                if inb_id == min_inb_id:
                    continue
                cid = cl.get('id') or cl.get('password')
                if not cid:
                    continue
                try:
                    await client.delete_client(inb_id, cid)
                    stats['deleted'] += 1
                    presence.pop(inb_id, None)
                except Exception as e:
                    logger.warning(
                        f"ensure_subscription_keys (key-mode): не удалось удалить {email} "
                        f"из inbound {inb_id} сервера {server_id}: {e}"
                    )

            min_client = presence.get(min_inb_id)
            if min_client:
                uuid_or_pwd = min_client.get('id') or min_client.get('password') or ''
                if (key.get('panel_inbound_id') != min_inb_id
                        or (key.get('client_uuid') or '') != uuid_or_pwd):
                    update_vpn_key_config(
                        key_id=key_id,
                        server_id=server_id,
                        panel_inbound_id=min_inb_id,
                        panel_email=email,
                        client_uuid=uuid_or_pwd,
                    )

    return stats


async def get_subscription_url_for_key(key: Dict[str, Any]) -> Optional[str]:
    """
    Возвращает HTTP-URL подписки для ключа.

    Args:
        key: dict с полями sub_id, server_id (+ обычные поля сервера если есть)

    Returns:
        Subscription URL или None (если у ключа нет sub_id или сервер недоступен)
    """
    sub_id = key.get('sub_id')
    server_id = key.get('server_id')
    if not sub_id or not server_id:
        return None
    try:
        client = await get_client(server_id)
        return await client.build_subscription_url(sub_id)
    except Exception as e:
        logger.warning(f"get_subscription_url_for_key: не удалось построить URL: {e}")
        return None


__all__ = [
    "VPNAPIError", "get_client_from_server_data", "invalidate_client_cache",
    "format_traffic", "close_all_clients", "get_client", "test_server_connection",
    "reset_key_traffic_if_active", "extend_key_on_server", "restore_key_traffic_limit",
    "push_key_to_panel", "restore_traffic_limit_in_db",
    "get_bot_mode", "is_subscription_mode",
    "ensure_subscription_keys_on_server", "get_subscription_url_for_key",
]
