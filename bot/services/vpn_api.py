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
from bot.utils.inbounds import split_ignored_inbounds

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
    server_data = _build_server_data_from_key(key)
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
    server_data = _build_server_data_from_key(key)
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
        if tariff:
            traffic_limit = (tariff.get('traffic_limit_gb', 0) or 0) * (1024**3)
    
    # Обнуляем traffic_used и сбрасываем пороги в БД
    reset_key_traffic_notification(key_id)
    
    # Обновляем traffic_limit в БД (на случай если тариф менялся)
    update_key_traffic_limit(key_id, traffic_limit)
    
    # Обновляем totalGB на панели
    if key.get('server_active') and key.get('panel_email'):
        try:
            server_data = _build_server_data_from_key(key)
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


def _client_identifier(client: Dict[str, Any]) -> str:
    """Возвращает идентификатор клиента 3X-UI для update/delete."""
    return client.get('id') or client.get('password') or ''


def _key_expiry_time_ms(key: Dict[str, Any]) -> int:
    """Конвертирует expires_at из БД в expiryTime 3X-UI."""
    from datetime import datetime, timedelta, timezone

    expires_at = key.get('expires_at')
    if not expires_at:
        return 0

    try:
        dt = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if dt > datetime.now(timezone.utc) + timedelta(days=90000):
            return 0
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError) as e:
        logger.warning(f"_key_expiry_time_ms: не удалось разобрать expires_at={expires_at!r}: {e}")
        return 0


def _key_days_left_for_add(key: Dict[str, Any]) -> int:
    """
    Возвращает положительный срок для add_client.

    3X-UI не принимает создание клиента с 0 или отрицательным сроком, поэтому
    точное значение потом всё равно выравнивается через update_client_full().
    """
    from datetime import datetime, timezone
    import math

    expires_at = key.get('expires_at')
    if not expires_at:
        return 365

    try:
        dt = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds_left = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(1, math.ceil(seconds_left / 86400))
    except (ValueError, TypeError):
        return 30


def _build_server_data_from_key(key: Dict[str, Any]) -> Dict[str, Any]:
    """Собирает данные сервера из JOIN-строки ключа."""
    return {
        'id': key.get('server_id'),
        'name': key.get('server_name'),
        'host': key.get('host'),
        'port': key.get('port'),
        'web_base_path': key.get('web_base_path'),
        'login': key.get('login'),
        'password': key.get('password'),
        'protocol': key.get('protocol', 'https'),
        'api_token': key.get('api_token'),
        'panel_version': key.get('panel_version'),
        'panel_api_profile': key.get('panel_api_profile'),
        'panel_checked_at': key.get('panel_checked_at'),
    }


def _parse_clients_by_email(inbounds: List[Dict[str, Any]], email: str) -> Dict[int, Dict[str, Any]]:
    """Собирает map inbound_id -> client для указанного email."""
    presence: Dict[int, Dict[str, Any]] = {}
    for inbound in inbounds:
        try:
            settings_raw = inbound.get('settings', '{}')
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
        except (json.JSONDecodeError, TypeError):
            continue
        for client in settings.get('clients', []):
            if client.get('email') == email:
                presence.setdefault(inbound['id'], client)
    return presence


async def push_key_to_panel(key_id: int, reset_traffic: bool = False) -> bool:
    """
    Совместимый алиас старой точки записи.

    Новая логика находится в sync_key_to_panel_state(): она умеет обновлять
    как одиночный ключ, так и все inbound subscription-ключа.
    """
    stats = await sync_key_to_panel_state(key_id, reset_traffic=reset_traffic)
    success = bool(stats.get('ok')) and stats.get('errors', 0) == 0
    if success:
        logger.info(f'Данные ключа {key_id} успешно синхронизированы с панелью: {stats}')
    else:
        logger.warning(f'Синхронизация ключа {key_id} с панелью завершилась не полностью: {stats}')
    return success


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
        if tariff:
            traffic_limit = (tariff.get('traffic_limit_gb', 0) or 0) * (1024**3)
    
    # Обнуляем traffic_used и пороги уведомлений
    reset_key_traffic_notification(key_id)
    
    # Обновляем traffic_limit (включая 0, если тариф стал безлимитным)
    update_key_traffic_limit(key_id, traffic_limit)
    
    logger.info(f'Лимит трафика ключа {key_id} восстановлен в БД: {traffic_limit / 1024**3:.1f} ГБ')
    return True


async def ensure_subscription_keys_on_server(key_id: int, reset_traffic: bool = False) -> Dict[str, int]:
    """
    Приводит набор клиентов с key.panel_email на key.server_id в соответствие
    с текущим bot_mode и состоянием ключа в БД.

    Режим 'subscription':
      - В каждом inbound сервера, где нет клиента с key.panel_email, создаёт
        клиента с key.sub_id, key.expires_at, key.traffic_limit.
        Если у ключа sub_id IS NULL — генерирует (или подхватывает существующий
        subId из найденного клиента на панели) и сохраняет в БД.
      - Обновляет vpn_keys.panel_inbound_id и client_uuid на минимальный inbound.
      - Обновляет expiryTime, totalGB, enable и subId у всех клиентов с этим email.
      - Если traffic_exhausted ИЛИ expired — выставляет enable=False.
      - Если ключ активен — выставляет enable=True.

    Режим 'key':
      - Оставляет клиента в МИНИМАЛЬНОМ inbound, остальных с тем же email удаляет.
      - Обновляет panel_inbound_id и client_uuid в БД на минимальный.

    Args:
        key_id: ID ключа в БД
        reset_traffic: True = сбросить up/down на панели перед записью состояния

    Returns:
        Словарь со статистикой: {'created', 'deleted', 'enabled', 'disabled',
        'updated', 'reset', 'errors', 'ok'}
    """
    stats = {
        'created': 0,
        'deleted': 0,
        'enabled': 0,
        'disabled': 0,
        'updated': 0,
        'reset': 0,
        'errors': 0,
        'ok': 0,
    }

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

        server_data = _build_server_data_from_key(key)

        try:
            client = get_client_from_server_data(server_data)
            all_inbounds = await client.get_inbounds(include_ignored=True)
        except Exception as e:
            logger.warning(f"ensure_subscription_keys: сервер {server_id} недоступен: {e}")
            return stats

        if not all_inbounds:
            return stats

        inbounds, ignored_inbounds = split_ignored_inbounds(all_inbounds)
        all_presence = _parse_clients_by_email(all_inbounds, email)
        presence = _parse_clients_by_email(inbounds, email)

        ignored_presence = _parse_clients_by_email(ignored_inbounds, email)
        for inb_id, cl in sorted(ignored_presence.items()):
            cid = _client_identifier(cl)
            if not cid:
                stats['errors'] += 1
                continue
            try:
                await client.delete_client(inb_id, cid)
                stats['deleted'] += 1
            except Exception as e:
                stats['errors'] += 1
                logger.warning(
                    f"ensure_subscription_keys: не удалось удалить скрытого клиента {email} "
                    f"из inbound {inb_id} сервера {server_id}: {e}"
                )

        mode = get_bot_mode()
        expiry_time_ms = _key_expiry_time_ms(key)
        traffic_limit = key.get('traffic_limit', 0) or 0
        user_banned = bool(key.get('is_banned', 0))
        active = is_key_active(key) and not is_traffic_exhausted(key) and not user_banned
        limit_ip = 1
        if key.get('tariff_id'):
            from database.db_tariffs import get_tariff_by_id
            try:
                tariff = get_tariff_by_id(key['tariff_id'])
                if tariff:
                    limit_ip = tariff.get('max_ips', 1)
            except Exception as e:
                logger.warning(
                    f"ensure_subscription_keys: не удалось получить тариф "
                    f"{key.get('tariff_id')} для limitIp, используется 1: {e}"
                )

        if mode == 'subscription':
            # Гарантируем sub_id у ключа
            sub_id = key.get('sub_id')
            if not sub_id:
                # Подхватим subId из существующего клиента на панели, если есть
                for cl in all_presence.values():
                    existing = cl.get('subId')
                    if existing:
                        sub_id = existing
                        break
                if not sub_id:
                    sub_id = _uuid.uuid4().hex
                update_vpn_key_sub_id(key_id, sub_id)
                key['sub_id'] = sub_id

            # Параметры для add_client в отсутствующих inbound
            total_gb = int(traffic_limit / (1024 ** 3)) if traffic_limit > 0 else 0
            days_left = _key_days_left_for_add(key)

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
                        limit_ip=limit_ip,
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
                    stats['errors'] += 1

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

            # Сбрасываем трафик и выравниваем ВСЕ существующие клиенты подписки.
            target_enable = active
            for inb_id, cl in sorted(presence.items()):
                cid = _client_identifier(cl)
                if not cid:
                    stats['errors'] += 1
                    continue
                if reset_traffic:
                    try:
                        await client.reset_client_traffic(inb_id, email)
                        stats['reset'] += 1
                    except Exception as e:
                        stats['errors'] += 1
                        logger.warning(
                            f"ensure_subscription_keys: не удалось сбросить трафик {email} "
                            f"в inbound {inb_id}: {e}"
                        )
                try:
                    await client.update_client_full(
                        inbound_id=inb_id,
                        client_uuid=cid,
                        email=email,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=traffic_limit,
                        enable=target_enable,
                        sub_id=sub_id,
                        limit_ip=limit_ip,
                    )
                    stats['updated'] += 1
                    if bool(cl.get('enable', True)) != target_enable:
                        if target_enable:
                            stats['enabled'] += 1
                        else:
                            stats['disabled'] += 1
                except Exception as e:
                    stats['errors'] += 1
                    logger.warning(
                        f"ensure_subscription_keys: не удалось обновить клиента {email} "
                        f"в inbound {inb_id} сервера {server_id}: {e}"
                    )

        else:  # mode == 'key'
            target_inbound_id = None
            if key.get('panel_inbound_id') is not None:
                try:
                    target_inbound_id = int(key['panel_inbound_id'])
                except (TypeError, ValueError):
                    target_inbound_id = None

            visible_inbound_ids = set()
            for inb in inbounds:
                try:
                    visible_inbound_ids.add(int(inb['id']))
                except (KeyError, TypeError, ValueError):
                    continue
            if not presence and target_inbound_id in visible_inbound_ids:
                try:
                    total_gb = int(traffic_limit / (1024 ** 3)) if traffic_limit > 0 else 0
                    days_left = _key_days_left_for_add(key)
                    flow = await client.get_inbound_flow(target_inbound_id)
                    res = await client.add_client(
                        inbound_id=target_inbound_id,
                        email=email,
                        total_gb=total_gb,
                        expire_days=days_left if days_left > 0 else 365,
                        limit_ip=limit_ip,
                        enable=active,
                        tg_id=str(key.get('telegram_id') or ''),
                        flow=flow,
                    )
                    stats['created'] += 1
                    presence[target_inbound_id] = {
                        'email': email,
                        'id': res['uuid'],
                        'password': res['uuid'],
                        'enable': active,
                    }
                except Exception as e:
                    stats['errors'] += 1
                    logger.warning(
                        f"ensure_subscription_keys (key-mode): не удалось восстановить клиента {email} "
                        f"в inbound {target_inbound_id} сервера {server_id}: {e}"
                    )

            min_inb_id = min(presence.keys()) if presence else None
            if min_inb_id is not None and len(presence) > 1:
                for inb_id, cl in list(presence.items()):
                    if inb_id == min_inb_id:
                        continue
                    cid = _client_identifier(cl)
                    if not cid:
                        stats['errors'] += 1
                        continue
                    try:
                        await client.delete_client(inb_id, cid)
                        stats['deleted'] += 1
                        presence.pop(inb_id, None)
                    except Exception as e:
                        stats['errors'] += 1
                        logger.warning(
                            f"ensure_subscription_keys (key-mode): не удалось удалить {email} "
                            f"из inbound {inb_id} сервера {server_id}: {e}"
                        )

            min_client = presence.get(min_inb_id) if min_inb_id is not None else None
            if min_client:
                uuid_or_pwd = _client_identifier(min_client)
                if (key.get('panel_inbound_id') != min_inb_id
                        or (key.get('client_uuid') or '') != uuid_or_pwd):
                    update_vpn_key_config(
                        key_id=key_id,
                        server_id=server_id,
                        panel_inbound_id=min_inb_id,
                        panel_email=email,
                        client_uuid=uuid_or_pwd,
                    )
                if reset_traffic:
                    try:
                        await client.reset_client_traffic(min_inb_id, email)
                        stats['reset'] += 1
                    except Exception as e:
                        stats['errors'] += 1
                        logger.warning(
                            f"ensure_subscription_keys (key-mode): не удалось сбросить трафик "
                            f"{email} в inbound {min_inb_id}: {e}"
                        )
                try:
                    await client.update_client_full(
                        inbound_id=min_inb_id,
                        client_uuid=uuid_or_pwd,
                        email=email,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=traffic_limit,
                        enable=active,
                        limit_ip=limit_ip,
                    )
                    stats['updated'] += 1
                    if bool(min_client.get('enable', True)) != active:
                        if active:
                            stats['enabled'] += 1
                        else:
                            stats['disabled'] += 1
                except Exception as e:
                    stats['errors'] += 1
                    logger.warning(
                        f"ensure_subscription_keys (key-mode): не удалось обновить клиента "
                        f"{email} в inbound {min_inb_id}: {e}"
                    )

    stats['ok'] = 1 if stats['errors'] == 0 else 0
    return stats


async def sync_key_to_panel_state(key_id: int, reset_traffic: bool = False) -> Dict[str, int]:
    """
    Единая точка синхронизации состояния ключа из БД на панель.

    Для subscription-режима обновляет всех клиентов с одним email/subId во всех
    inbound. Для key-режима обновляет основной клиент и чистит лишние через ту
    же материализацию состояния.
    """
    return await ensure_subscription_keys_on_server(key_id, reset_traffic=reset_traffic)


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
    "ensure_subscription_keys_on_server", "sync_key_to_panel_state",
    "get_subscription_url_for_key",
]
