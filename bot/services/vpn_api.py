"""
Facade for working with VPN panel APIs.
"""
import json
import logging
import uuid as _uuid
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
import asyncio
import inspect

from .panels.base import (
    VPNAPIError,
    BaseVPNClient,
    PanelClientState,
    PanelServerSnapshot,
)
from .panels.xui import XUIClient
from bot.utils.inbounds import split_ignored_inbounds
from bot.services.panel_sync_coordinator import (
    panel_sync_coordinator,
    regular_panel_operation,
)

logger = logging.getLogger(__name__)

_clients: Dict[int, BaseVPNClient] = {}

# Per-key locks for ensure_subscription_keys_on_server (race protection)
_ensure_locks: Dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def _unlocked_preview():
    """No-op async context used by read-only reconciliation previews."""
    yield


def get_bot_mode() -> str:
    """
    Returns the bot's current global operating mode.

    Returns:
        'subscription' (default) or 'key'
    """
    try:
        from database.db_settings import get_setting
        value = get_setting('bot_mode', 'subscription') or 'subscription'
        return value if value in ('subscription', 'key') else 'subscription'
    except Exception as e:
        logger.warning(f"get_bot_mode: ошибка чтения settings, fallback subscription: {e}")
        return 'subscription'


def is_subscription_mode() -> bool:
    """True if the bot is running in Subscription mode."""
    return get_bot_mode() == 'subscription'


async def get_client_subscription_inbounds(
    client: BaseVPNClient,
    include_ignored: bool = False,
) -> List[Dict[str, Any]]:
    """Returns the inbounds eligible for a shared subscription.

    The fallback keeps compatibility with third-party panel adapters and older
    test doubles that only implement ``get_inbounds()``.
    """
    class_method = getattr(type(client), 'get_subscription_inbounds', None)
    instance_method = getattr(client, '__dict__', {}).get('get_subscription_inbounds')
    if callable(class_method) or callable(instance_method):
        if include_ignored:
            return await client.get_subscription_inbounds(include_ignored=True)
        return await client.get_subscription_inbounds()
    if include_ignored:
        return await client.get_inbounds(include_ignored=True)
    return await client.get_inbounds()

def get_client_from_server_data(server: Dict[str, Any]) -> BaseVPNClient:
    """
    Creates or returns a client instance for the panel API.
    """
    server_id = server['id']
    if server_id in _clients:
        return _clients[server_id]
        
    client = XUIClient(server)
        
    _clients[server_id] = client
    return client

async def invalidate_client_cache(server_id: int):
    """Invalidates the client session."""
    client = _clients.pop(server_id, None)
    if not client:
        return
    try:
        await client.close()
    except Exception as e:
        logger.error(f"Ошибка при закрытии клиента {server_id}: {e}")
    logger.debug(f'Кэш клиента {server_id} очищен')

def format_traffic(bytes_count: int) -> str:
    """Formats bytes into a readable form."""
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


def _traffic_remaining_bytes(key: Dict[str, Any]) -> int:
    """Returns the remaining traffic for the cumulative database accounting."""
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit <= 0:
        return 0
    traffic_used = key.get('traffic_used', 0) or 0
    return max(0, int(traffic_limit) - int(traffic_used))


def calculate_panel_total_for_key(key: Dict[str, Any], panel_used_bytes: int = 0) -> int:
    """
    Counts the working totalGB of the panel for the current state of the key.

    In the database, traffic_limit stores the total purchased limit, and traffic_used stores the total
    consumption The panel can only store the counter of the current client, so it
    the limit is equal to the current consumption of the panel plus the balance in the database.
    """
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit <= 0:
        return 0
    return max(0, int(panel_used_bytes or 0)) + _traffic_remaining_bytes(key)


def _panel_total_gb_for_key(key: Dict[str, Any], panel_used_bytes: int = 0) -> int:
    """Converts the working limit of the panel to whole GB for add_client()."""
    total_bytes = calculate_panel_total_for_key(key, panel_used_bytes)
    if total_bytes <= 0:
        return 0
    gb = 1024 ** 3
    return int((total_bytes + gb - 1) // gb)

async def close_all_clients():
    """Closes all open client sessions."""
    clients = list(_clients.items())
    _clients.clear()
    for server_id, client in clients:
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Ошибка при закрытии клиента {server_id}: {e}")

async def get_client(server_id: int) -> XUIClient:
    """
    Retrieves the client for the server by ID (from the database).
    
    Args:
        server_id: Server ID in the database
        
    Returns:
        XUIClient instance
        
    Raises:
        ValueError: If the server is not found
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
    Checks the connection to the server.
    
    Args:
        server_data: Dictionary with server data
        
    Returns:
        Dictionary with result:
        - success: True if the connection is successful
        - message: Message about the result
        - stats: Statistics (if successful)
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

@regular_panel_operation
async def reset_key_traffic_if_active(key_id: int) -> bool:
    """
    Resets spent dongle traffic in the 3X-UI panel,
    if the server is active.
    
    Args:
        key_id: Key ID (VPNKey.id)
        
    Returns:
        True if the reset was successful, otherwise False.
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

@regular_panel_operation
async def extend_key_on_server(key_id: int, days: int) -> bool:
    """
    Extends the validity period of the key in the 3X-UI panel if the server is active.
    
    Args:
        key_id: Key ID (VPNKey.id)
        days: Number of days to extend
        
    Returns:
        True if renewal is successful, otherwise False.
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


@regular_panel_operation
async def restore_key_traffic_limit(key_id: int) -> bool:
    """
    Restores the full tariff traffic limit on the panel and resets traffic_used in the database.
    Called when a key is renewed (after reset_key_traffic_if_active).
    
    Does 3 things:
    1. Gets the limit from the key tariff
    2. Updates totalGB on the panel to the full tariff limit
    3. Resets traffic_used and resets notification thresholds in the database
    
    Args:
        key_id: Key ID
        
    Returns:
        True on success, False on error
    """
    from database.requests import (
        get_vpn_key_by_id, get_tariff_by_id,
        reset_key_traffic_notification, update_key_traffic_limit
    )
    
    key = get_vpn_key_by_id(key_id)
    if not key:
        return False
    
    # We get the limit from the tariff
    tariff_id = key.get('tariff_id')
    traffic_limit = key.get('traffic_limit', 0) or 0
    
    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff:
            traffic_limit = (tariff.get('traffic_limit_gb', 0) or 0) * (1024**3)
    
    # Reset traffic_used and reset thresholds in the database
    reset_key_traffic_notification(key_id)
    
    # Update traffic_limit in the database (in case the tariff has changed)
    update_key_traffic_limit(key_id, traffic_limit)
    
    # Update totalGB on the panel
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
    """Returns the 3X-UI client ID for update/delete."""
    return (
        client.get('id')
        or client.get('password')
        or client.get('auth')
        or client.get('email')
        or ''
    )


def _key_expiry_time_ms(key: Dict[str, Any]) -> int:
    """Converts expires_at from DB to expiryTime 3X-UI."""
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
    Returns a positive term for add_client.

    3X-UI does not accept creating a client with a 0 or negative term, so
    the exact value is then adjusted anyway via update_client_full().
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
    """Collects server data from a JOIN key string."""
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
    """Collects map inbound_id -> client for the specified email."""
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


def _panel_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Normalizes the panel client's numeric fields for comparison."""
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _panel_bool(value: Any, default: bool = True) -> bool:
    """Normalizes the boolean fields of the panel client for comparison."""
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _client_needs_panel_update(
    client: Dict[str, Any],
    *,
    expiry_time_ms: int,
    total_gb_bytes: int,
    enable: bool,
    limit_ip: int,
    sub_id: Optional[str] = None,
    flow: Optional[str] = None,
    compare_sub_id: bool = True,
) -> bool:
    """True if the panel client is different from the target state from the database."""
    checks = (
        (_panel_int(client.get('expiryTime')), int(expiry_time_ms)),
        (_panel_int(client.get('totalGB')), int(total_gb_bytes)),
        (_panel_int(client.get('limitIp')), int(limit_ip)),
        (_panel_int(client.get('reset')), 0),
    )
    if any(current != expected for current, expected in checks):
        return True
    if _panel_bool(client.get('enable'), True) != bool(enable):
        return True
    if compare_sub_id and (client.get('subId') or '') != (sub_id or ''):
        return True
    if flow is not None and (client.get('flow') or '') != flow:
        return True
    return False


def _client_uses_clients_api(client: BaseVPNClient) -> bool:
    """True for first-class Clients API 3x-ui v3.1.0+."""
    return getattr(client, 'api_profile', None) == 'clients_api'


async def _add_client_from_snapshot(
    client: BaseVPNClient,
    snapshot: Optional[PanelServerSnapshot],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Create/attach one client without re-reading data already in a snapshot."""
    call_kwargs = dict(kwargs)
    email = str(kwargs.get('email') or '').strip()
    existing_state = snapshot.get_client(email) if snapshot is not None else None
    if snapshot is not None and isinstance(client, XUIClient):
        call_kwargs['panel_snapshot'] = snapshot
    result = await client.add_client(**call_kwargs)

    if snapshot is not None:
        normalized = email.lower()
        inbound_id = int(kwargs['inbound_id'])
        state = existing_state or snapshot.clients.get(normalized)
        if state is None:
            state = PanelClientState(email=email, source=snapshot.api_profile)
            snapshot.clients[normalized] = state
        identifier = result.get('uuid') or kwargs.get('client_uuid') or ''
        if existing_state is not None and _client_uses_clients_api(client):
            placement = dict(
                existing_state.client
                or next(iter(existing_state.placements.values()), {})
            )
            placement.update({
                'email': email,
                'id': identifier,
                'password': identifier,
            })
        else:
            total_gb = int(result.get('total_gb', kwargs.get('total_gb', 0)) or 0)
            placement = {
                'email': email,
                'id': identifier,
                'password': identifier,
                'subId': kwargs.get('sub_id') or result.get('sub_id') or '',
                'enable': bool(kwargs.get('enable', True)),
                'limitIp': int(kwargs.get('limit_ip', 1) or 1),
                'flow': kwargs.get('flow') or '',
                'expiryTime': int(result.get('expire_time', 0) or 0),
                'totalGB': total_gb * (1024 ** 3),
                'reset': 0,
            }
        state.inbound_ids.add(inbound_id)
        state.placements[inbound_id] = placement
        if not state.client:
            state.client = dict(placement)
            state.expiry_time = int(placement.get('expiryTime', 0) or 0)
            state.total_gb = int(placement.get('totalGB', 0) or 0)
            state.enable = bool(placement.get('enable', True))
            state.sub_id = str(placement.get('subId') or '')
            state.limit_ip = int(placement.get('limitIp', 1) or 1)
            state.reset = int(placement.get('reset', 0) or 0)
            state.traffic_known = True
            state.traffic_used = 0
    return result


async def _delete_client_from_snapshot(
    client: BaseVPNClient,
    snapshot: Optional[PanelServerSnapshot],
    *,
    inbound_id: int,
    client_uuid: str,
    email: str,
) -> bool:
    """Detach/delete one placement using the already known logical state."""
    state = snapshot.get_client(email) if snapshot is not None else None
    if state is not None and isinstance(client, XUIClient):
        result = await client.delete_client(
            inbound_id,
            client_uuid,
            panel_state=state,
        )
    else:
        result = await client.delete_client(inbound_id, client_uuid)

    if result and snapshot is not None and state is not None:
        state.inbound_ids.discard(int(inbound_id))
        state.placements.pop(int(inbound_id), None)
        if not state.inbound_ids:
            snapshot.clients.pop(str(email).strip().lower(), None)
    return bool(result)


async def _update_client_from_snapshot(
    client: BaseVPNClient,
    snapshot: Optional[PanelServerSnapshot],
    *,
    panel_client: Dict[str, Any],
    **kwargs: Any,
) -> bool:
    """Point-update a client without a preliminary per-client/inbound read."""
    call_kwargs = dict(kwargs)
    if snapshot is not None and isinstance(client, XUIClient):
        call_kwargs['panel_client'] = panel_client
    result = bool(await client.update_client_full(**call_kwargs))
    if not result:
        return False

    updated_fields = {
        'expiryTime': kwargs.get('expiry_time_ms', panel_client.get('expiryTime', 0)),
        'totalGB': kwargs.get('total_gb_bytes', panel_client.get('totalGB', 0)),
        'enable': kwargs.get('enable', panel_client.get('enable', True)),
        'limitIp': kwargs.get('limit_ip', panel_client.get('limitIp', 1)),
        'reset': 0,
    }
    if kwargs.get('sub_id') is not None:
        updated_fields['subId'] = kwargs['sub_id']
    if kwargs.get('flow') is not None:
        updated_fields['flow'] = kwargs['flow']
    panel_client.update(updated_fields)

    if snapshot is not None:
        state = snapshot.get_client(kwargs.get('email'))
        if state is not None:
            inbound_id = int(kwargs.get('inbound_id', 0) or 0)
            if _client_uses_clients_api(client):
                state.client.update(updated_fields)
                for placement in state.placements.values():
                    placement.update(updated_fields)
            elif inbound_id in state.placements:
                state.placements[inbound_id].update(updated_fields)
                if state.client is state.placements[inbound_id]:
                    state.client.update(updated_fields)
            state.expiry_time = int(updated_fields['expiryTime'] or 0)
            state.total_gb = int(updated_fields['totalGB'] or 0)
            state.enable = bool(updated_fields['enable'])
            state.limit_ip = int(updated_fields['limitIp'] or 1)
            state.reset = 0
            if 'subId' in updated_fields:
                state.sub_id = str(updated_fields['subId'] or '')
    return True


def _traffic_int(value: Any) -> Optional[int]:
    """Normalizes numeric traffic fields from the panel API."""
    if value is None or value == '':
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_traffic_int(data: Dict[str, Any], fields: tuple, default: int = 0) -> int:
    for field in fields:
        value = _traffic_int(data.get(field))
        if value is not None:
            return value
    return default


def _traffic_used_from_record(data: Dict[str, Any]) -> Optional[int]:
    up = _traffic_int(data.get('up'))
    down = _traffic_int(data.get('down'))
    if up is not None or down is not None:
        return (up or 0) + (down or 0)

    for field in (
        'traffic_used',
        'trafficUsed',
        'usedTraffic',
        'usedBytes',
        'used_bytes',
        'usedGB',
        'used',
        'consumedTraffic',
        'consumed',
    ):
        value = _traffic_int(data.get(field))
        if value is not None:
            return value
    return None


def _cumulative_traffic_used_from_panel(
    key: Dict[str, Any],
    used_on_server: int,
    total_on_server: int,
) -> int:
    """Converts panel counters to cumulative key consumption from the database."""
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0 and total_on_server > 0:
        remaining_on_server = max(0, int(total_on_server) - int(used_on_server))
        calculated = max(0, int(traffic_limit) - remaining_on_server)
        return max(int(key.get('traffic_used', 0) or 0), calculated)
    return max(int(key.get('traffic_used', 0) or 0), int(used_on_server))


def _normalize_global_traffic_stats(
    stats: Dict[str, Any],
    key: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    data = dict(stats)
    client_payload = stats.get('client')
    if isinstance(client_payload, dict):
        data.update(client_payload)

    traffic_used = _traffic_used_from_record(data)
    if traffic_used is None:
        return None

    total_gb = _first_traffic_int(
        data,
        ('total', 'totalGB', 'traffic_limit', 'trafficLimit'),
        0,
    )
    return {
        'traffic_used': _cumulative_traffic_used_from_panel(key, traffic_used, total_gb),
        'panel_traffic_used': traffic_used,
        'totalGB': total_gb,
        'expiryTime': _first_traffic_int(
            data,
            ('expiry_time', 'expiryTime', 'expire', 'expires_at'),
            0,
        ),
        'source': data.get('source') or 'clients_api_global',
    }


def _ensure_traffic_entry(
    stats_map: Dict[str, Dict[str, Any]],
    email: str,
) -> Dict[str, Any]:
    if email not in stats_map:
        stats_map[email] = {
            'up': 0,
            'down': 0,
            'totalGB': 0,
            'expiryTime': 0,
            'has_client': False,
            'has_stats': False,
        }
    return stats_map[email]


def build_inbound_traffic_map(inbounds: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Collects email -> traffic/meta from legacy inbound/clientStats."""
    stats_map: Dict[str, Dict[str, Any]] = {}

    for inbound in inbounds:
        for stats in inbound.get('clientStats', []):
            email = stats.get('email')
            if not email:
                continue
            entry = _ensure_traffic_entry(stats_map, email)
            entry['has_stats'] = True
            entry['up'] += stats.get('up', 0) or 0
            entry['down'] += stats.get('down', 0) or 0
            entry['totalGB'] = max(
                entry['totalGB'],
                stats.get('total', 0) or stats.get('totalGB', 0) or 0,
            )
            entry['expiryTime'] = max(
                entry['expiryTime'],
                stats.get('expiryTime', 0) or stats.get('expiry_time', 0) or 0,
            )

        try:
            settings_raw = inbound.get('settings', '{}')
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
        except (json.JSONDecodeError, TypeError):
            settings = {}

        for panel_client in settings.get('clients', []) if isinstance(settings, dict) else []:
            email = panel_client.get('email')
            if not email:
                continue
            entry = _ensure_traffic_entry(stats_map, email)
            entry['has_client'] = True
            entry['totalGB'] = max(
                entry['totalGB'],
                panel_client.get('totalGB', 0) or 0,
            )
            entry['expiryTime'] = max(
                entry['expiryTime'],
                panel_client.get('expiryTime', 0) or 0,
            )

    return stats_map


def _build_inbound_usage_by_id(inbounds: List[Dict[str, Any]], email: str) -> Dict[int, int]:
    """Collects inbound_id -> up+down for the specified email."""
    usage: Dict[int, int] = {}
    for inbound in inbounds:
        inbound_id = inbound.get('id')
        if inbound_id is None:
            continue
        for stats in inbound.get('clientStats', []):
            if stats.get('email') != email:
                continue
            usage[inbound_id] = (
                usage.get(inbound_id, 0)
                + (stats.get('up', 0) or 0)
                + (stats.get('down', 0) or 0)
            )
    return usage


async def _get_global_panel_used_safe(
    client: BaseVPNClient,
    email: str,
    fallback_used: int,
) -> int:
    """Reads the clients API total flow, if available."""
    try:
        try:
            stats_result = client.get_client_stats(email, resolve_inbound=False)
        except TypeError:
            stats_result = client.get_client_stats(email)
        stats = await stats_result if inspect.isawaitable(stats_result) else stats_result
    except Exception as e:
        logger.debug(f"_get_global_panel_used_safe: общий счётчик {email} недоступен: {e}")
        return fallback_used

    if not isinstance(stats, dict):
        return fallback_used
    used = _traffic_used_from_record(stats)
    return fallback_used if used is None else used


def _snapshot_from_inbound_entry(
    key: Dict[str, Any],
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    used_on_server = (entry.get('up', 0) or 0) + (entry.get('down', 0) or 0)
    total_on_server = entry.get('totalGB', 0) or 0

    return {
        'traffic_used': _cumulative_traffic_used_from_panel(
            key,
            used_on_server,
            total_on_server,
        ),
        'panel_traffic_used': used_on_server,
        'totalGB': total_on_server,
        'expiryTime': entry.get('expiryTime', 0) or 0,
        'source': 'inbound_aggregate',
    }


async def get_key_traffic_snapshot(
    client: BaseVPNClient,
    key: Dict[str, Any],
    inbounds: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns a normalized snapshot of the key's traffic.

    For subscription clients on the new 3X-UI, the common one is first used
    first-class client counter by email. If it is not available, it applies
    legacy aggregation by inbound/clientStats.
    """
    email = key.get('panel_email')
    if not email:
        return None

    if key.get('sub_id'):
        try:
            try:
                stats_result = client.get_client_stats(email, resolve_inbound=False)
            except TypeError:
                stats_result = client.get_client_stats(email)
            stats = await stats_result if inspect.isawaitable(stats_result) else stats_result
        except Exception as e:
            logger.debug(f"get_key_traffic_snapshot: общий счётчик {email} недоступен: {e}")
            stats = None

        if isinstance(stats, dict):
            source = str(stats.get('source') or '')
            can_be_global = source.startswith('clients_api') or (
                _client_uses_clients_api(client) and source != 'inbound_first'
            )
            if can_be_global:
                snapshot = _normalize_global_traffic_stats(stats, key)
                if snapshot:
                    if inbounds is not None:
                        entry = build_inbound_traffic_map(inbounds).get(email)
                        if entry:
                            if snapshot.get('totalGB', 0) == 0 and entry.get('totalGB', 0) > 0:
                                snapshot['totalGB'] = entry['totalGB']
                            if snapshot.get('expiryTime', 0) == 0 and entry.get('expiryTime', 0) > 0:
                                snapshot['expiryTime'] = entry['expiryTime']
                    snapshot['source'] = source or 'clients_api_global'
                    return snapshot

    if inbounds is None:
        if key.get('sub_id'):
            inbounds = await get_client_subscription_inbounds(client)
        else:
            inbounds = await client.get_inbounds()

    entry = build_inbound_traffic_map(inbounds).get(email)
    if not entry:
        return None
    return _snapshot_from_inbound_entry(key, entry)


def _get_inbound_flow_from_data(inbound: Dict[str, Any]) -> Optional[str]:
    """Determines flow based on already loaded inbound; None = There was not enough data."""
    protocol = inbound.get('protocol', '')
    if protocol != 'vless':
        return ""
    if 'streamSettings' not in inbound:
        return None

    try:
        stream_raw = inbound.get('streamSettings', '{}')
        stream = json.loads(stream_raw) if isinstance(stream_raw, str) else stream_raw
        if not isinstance(stream, dict):
            return None
    except (json.JSONDecodeError, TypeError):
        return None

    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    if network == 'tcp' and security in ('reality', 'tls'):
        return 'xtls-rprx-vision'
    return ""


async def _get_inbound_flow_safe(
    client: BaseVPNClient,
    inbound_id: int,
    server_id: Any,
    inbound: Optional[Dict[str, Any]] = None,
) -> str:
    """Returns flow inbound without disrupting all synchronization when a panel error occurs."""
    if inbound is not None:
        flow = _get_inbound_flow_from_data(inbound)
        if flow is not None:
            return flow

    try:
        result = client.get_inbound_flow(inbound_id)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else ""
    except Exception as e:
        logger.warning(
            f"ensure_subscription_keys: не удалось определить flow для inbound "
            f"{inbound_id} сервера {server_id}: {e}"
        )
        return ""


async def _get_first_required_flow(
    client: BaseVPNClient,
    inbounds: List[Dict[str, Any]],
    server_id: Any,
) -> str:
    """For clients_api, selects a common flow if any visible inbound needs it."""
    for inbound in inbounds:
        try:
            inbound_id = inbound['id']
        except KeyError:
            continue
        flow = await _get_inbound_flow_safe(client, inbound_id, server_id, inbound)
        if flow:
            return flow
    return ""


@regular_panel_operation
async def push_key_to_panel(key_id: int, reset_traffic: bool = False) -> bool:
    """
    Compatible alias for the old record point.

    The new logic is in sync_key_to_panel_state(): it can update
    both a single key and all inbound subscription keys.
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
    Restores the full tariff traffic limit in our database.
    DOES NOT access the panel! The panel is updated via push_key_to_panel.
    
    Does:
    1. Gets the limit from the key tariff
    2. Updates traffic_limit in the database
    3. Resets traffic_used and resets notification thresholds
    
    Args:
        key_id: Key ID
        
    Returns:
        True on success
    """
    from database.requests import (
        get_vpn_key_by_id, get_tariff_by_id,
        reset_key_traffic_notification, update_key_traffic_limit
    )
    
    key = get_vpn_key_by_id(key_id)
    if not key:
        return False
    
    # We get the limit from the tariff
    tariff_id = key.get('tariff_id')
    traffic_limit = key.get('traffic_limit', 0) or 0
    
    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff:
            traffic_limit = (tariff.get('traffic_limit_gb', 0) or 0) * (1024**3)
    
    # Resetting traffic_used and notification thresholds
    reset_key_traffic_notification(key_id)
    
    # Update traffic_limit (including 0 if the tariff has become unlimited)
    update_key_traffic_limit(key_id, traffic_limit)
    
    logger.info(f'Лимит трафика ключа {key_id} восстановлен в БД: {traffic_limit / 1024**3:.1f} ГБ')
    return True


async def _ensure_subscription_keys_on_server_impl(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    Matches the set of clients with key.panel_email to key.server_id
    with the current bot_mode and the key state in the database.

    'subscription' mode:
      - In every inbound server where there is no client with key.panel_email, creates
        client with key.sub_id, key.expires_at, key.traffic_limit.
        If the key has sub_id IS NULL, it generates (or picks up an existing
        subId from the client found on the panel) and saves it in the database.
      - Updates vpn_keys.panel_inbound_id and client_uuid to the minimum inbound.
      - Updates expiryTime, totalGB, enable and subId for all clients with this email.
      - If traffic_exhausted OR expired - sets enable=False.
      - If the key is active, set enable=True.

    'key' mode:
      - Leaves the client in MINIMUM inbound, deletes others with the same email.
      - Updates panel_inbound_id and client_uuid in the database to the minimum.

    Args:
        key_id: ID of the key in the database
        reset_traffic: True = reset up/down on panel before recording state

    Returns:
        Dictionary with statistics: {'created', 'deleted', 'enabled', 'disabled',
        'updated', 'skipped', 'reset', 'errors', 'ok'}
    """
    stats = {
        'created': 0,
        'deleted': 0,
        'enabled': 0,
        'disabled': 0,
        'updated': 0,
        'skipped': 0,
        'reset': 0,
        'errors': 0,
        'ok': 0,
    }

    lock_context = (
        _unlocked_preview()
        if dry_run
        else _ensure_locks.setdefault(key_id, asyncio.Lock())
    )
    async with lock_context:
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
        mode = get_bot_mode()

        try:
            client = get_client_from_server_data(server_data)
            if panel_snapshot is not None:
                all_inbounds = panel_snapshot.inbounds
            elif mode == 'subscription':
                all_inbounds = await get_client_subscription_inbounds(client, include_ignored=True)
            else:
                all_inbounds = await client.get_inbounds(include_ignored=True)
        except Exception as e:
            logger.warning(f"ensure_subscription_keys: сервер {server_id} недоступен: {e}")
            return stats

        if not all_inbounds:
            return stats

        inbounds, ignored_inbounds = split_ignored_inbounds(all_inbounds)
        if panel_snapshot is not None:
            all_presence = panel_snapshot.presence_for_email(email)
            visible_ids = {int(inbound['id']) for inbound in inbounds}
            ignored_ids = {int(inbound['id']) for inbound in ignored_inbounds}
            presence = {
                inbound_id: client_data
                for inbound_id, client_data in all_presence.items()
                if inbound_id in visible_ids
            }
            ignored_presence = {
                inbound_id: client_data
                for inbound_id, client_data in all_presence.items()
                if inbound_id in ignored_ids
            }
        else:
            all_presence = _parse_clients_by_email(all_inbounds, email)
            presence = _parse_clients_by_email(inbounds, email)
            ignored_presence = _parse_clients_by_email(ignored_inbounds, email)

        for inb_id, cl in sorted(ignored_presence.items()):
            cid = _client_identifier(cl)
            if not cid:
                stats['errors'] += 1
                continue
            if dry_run:
                stats['deleted'] += 1
                continue
            try:
                await _delete_client_from_snapshot(
                    client,
                    panel_snapshot,
                    inbound_id=inb_id,
                    client_uuid=cid,
                    email=email,
                )
                stats['deleted'] += 1
            except Exception as e:
                stats['errors'] += 1
                logger.warning(
                    f"ensure_subscription_keys: не удалось удалить скрытого клиента {email} "
                    f"из inbound {inb_id} сервера {server_id}: {e}"
                )

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
                    max_ips = tariff.get('max_ips', 1)
                    limit_ip = max_ips if max_ips is not None else 1
            except Exception as e:
                logger.warning(
                    f"ensure_subscription_keys: не удалось получить тариф "
                    f"{key.get('tariff_id')} для limitIp, используется 1: {e}"
                )

        traffic_entry = build_inbound_traffic_map(inbounds).get(email, {})
        aggregate_panel_used = (traffic_entry.get('up', 0) or 0) + (traffic_entry.get('down', 0) or 0)
        snapshot_client = panel_snapshot.get_client(email) if panel_snapshot is not None else None
        snapshot_traffic_known = bool(snapshot_client and snapshot_client.traffic_known)
        uses_clients_api = _client_uses_clients_api(client)
        if snapshot_traffic_known:
            aggregate_panel_used = int(snapshot_client.traffic_used)
        inbound_usage_by_id = _build_inbound_usage_by_id(inbounds, email)

        def presence_after_add(
            inbound_id: int,
            result: Dict[str, Any],
            flow: str,
            sub_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            if panel_snapshot is not None and not dry_run:
                current_state = panel_snapshot.get_client(email)
                if current_state is not None:
                    stored = current_state.placements.get(int(inbound_id))
                    if stored is not None:
                        return dict(stored)
            if uses_clients_api and snapshot_client is not None:
                existing = dict(snapshot_client.client)
                identifier = result.get('uuid') or key.get('client_uuid') or email
                existing.update({
                    'email': email,
                    'id': identifier,
                    'password': identifier,
                })
                return existing
            return {
                'email': email,
                'id': result.get('uuid') or key.get('client_uuid') or email,
                'password': result.get('uuid') or key.get('client_uuid') or email,
                'subId': sub_id or '',
                'enable': active,
                'flow': flow,
                'totalGB': calculate_panel_total_for_key(key, 0),
            }

        if mode == 'subscription':
            # We guarantee the sub_id of the key
            sub_id = key.get('sub_id')
            if not sub_id:
                # Let's pick up the subId from the existing client on the panel, if any
                for cl in all_presence.values():
                    existing = cl.get('subId')
                    if existing:
                        sub_id = existing
                        break
                if not sub_id:
                    sub_id = _uuid.uuid4().hex
                if not dry_run:
                    update_vpn_key_sub_id(key_id, sub_id)
                key['sub_id'] = sub_id

            subscription_panel_used = 0 if reset_traffic else aggregate_panel_used
            if uses_clients_api and not reset_traffic:
                if panel_snapshot is not None:
                    if not snapshot_traffic_known and traffic_limit > 0:
                        stats['errors'] += 1
                        logger.warning(
                            "ensure_subscription_keys: batch traffic is unavailable for %s "
                            "on server %s; key was skipped",
                            email,
                            server_id,
                        )
                        stats['ok'] = 0
                        return stats
                    subscription_panel_used = aggregate_panel_used
                else:
                    subscription_panel_used = await _get_global_panel_used_safe(
                        client,
                        email,
                        aggregate_panel_used,
                    )
            target_total_bytes = calculate_panel_total_for_key(key, subscription_panel_used)

            # Parameters for add_client in missing inbound
            total_gb = _panel_total_gb_for_key(key, subscription_panel_used)
            days_left = _key_days_left_for_add(key)
            visible_inbounds_by_id = {inb.get('id'): inb for inb in inbounds}

            # Create in missing inbound
            missing = [inb for inb in inbounds if inb['id'] not in presence]
            for inb in missing:
                try:
                    flow = await _get_inbound_flow_safe(client, inb['id'], server_id, inb)
                    if dry_run:
                        res = {'uuid': key.get('client_uuid') or email}
                    else:
                        res = await _add_client_from_snapshot(
                            client,
                            panel_snapshot,
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
                    presence[inb['id']] = presence_after_add(
                        inb['id'],
                        res,
                        flow,
                        sub_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"ensure_subscription_keys: не удалось создать клиента {email} "
                        f"в inbound {inb['id']} сервера {server_id}: {e}"
                    )
                    stats['errors'] += 1

            # Update panel_inbound_id/client_uuid to the MINIMUM present inbound
            if presence:
                min_inb_id = min(presence.keys())
                min_client = presence[min_inb_id]
                uuid_or_pwd = min_client.get('id') or min_client.get('password') or ''
                if (key.get('panel_inbound_id') != min_inb_id
                        or (key.get('client_uuid') or '') != uuid_or_pwd):
                    if not dry_run:
                        update_vpn_key_config(
                            key_id=key_id,
                            server_id=server_id,
                            panel_inbound_id=min_inb_id,
                            panel_email=email,
                            client_uuid=uuid_or_pwd,
                            sub_id=sub_id,
                        )

            # We reset traffic and align ALL existing subscription clients.
            target_enable = active
            clients_api_flow = (
                await _get_first_required_flow(client, inbounds, server_id)
                if uses_clients_api
                else None
            )

            if uses_clients_api and presence:
                sorted_presence = sorted(presence.items())
                needs_update = [
                    (inb_id, cl)
                    for inb_id, cl in sorted_presence
                    if _client_needs_panel_update(
                        cl,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=target_total_bytes,
                        enable=target_enable,
                        sub_id=sub_id,
                        limit_ip=limit_ip,
                        flow=clients_api_flow,
                    )
                ]

                if reset_traffic:
                    first_inb_id = sorted_presence[0][0]
                    try:
                        if not dry_run:
                            await client.reset_client_traffic(first_inb_id, email)
                        stats['reset'] += 1
                    except Exception as e:
                        stats['errors'] += 1
                        logger.warning(
                            f"ensure_subscription_keys: не удалось сбросить трафик {email} "
                            f"через clients API: {e}"
                        )

                if not needs_update:
                    stats['skipped'] += len(sorted_presence)
                else:
                    first_inb_id, first_client = sorted_presence[0]
                    first_cid = _client_identifier(first_client)
                    changed_enable = sum(
                        1
                        for _, cl in sorted_presence
                        if _panel_bool(cl.get('enable'), True) != target_enable
                    )
                    if dry_run:
                        stats['updated'] += 1
                        stats['skipped'] += len(sorted_presence) - len(needs_update)
                        if target_enable:
                            stats['enabled'] += changed_enable
                        else:
                            stats['disabled'] += changed_enable
                    else:
                        try:
                            await _update_client_from_snapshot(
                                client,
                                panel_snapshot,
                                panel_client=first_client,
                                inbound_id=first_inb_id,
                                client_uuid=first_cid,
                                email=email,
                                expiry_time_ms=expiry_time_ms,
                                total_gb_bytes=target_total_bytes,
                                enable=target_enable,
                                sub_id=sub_id,
                                limit_ip=limit_ip,
                                flow=clients_api_flow,
                            )
                            stats['updated'] += 1
                            stats['skipped'] += len(sorted_presence) - len(needs_update)
                            if target_enable:
                                stats['enabled'] += changed_enable
                            else:
                                stats['disabled'] += changed_enable
                        except Exception as e:
                            stats['errors'] += 1
                            logger.warning(
                                f"ensure_subscription_keys: не удалось обновить клиента {email} "
                                f"через clients API сервера {server_id}: {e}"
                            )
            else:
                for inb_id, cl in sorted(presence.items()):
                    cid = _client_identifier(cl)
                    if not cid:
                        stats['errors'] += 1
                        continue
                    flow = await _get_inbound_flow_safe(
                        client,
                        inb_id,
                        server_id,
                        visible_inbounds_by_id.get(inb_id),
                    )
                    enable_changed = (
                        _panel_bool(cl.get('enable'), True) != target_enable
                    )
                    needs_update = _client_needs_panel_update(
                        cl,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=target_total_bytes,
                        enable=target_enable,
                        sub_id=sub_id,
                        limit_ip=limit_ip,
                        flow=flow,
                    )
                    if reset_traffic:
                        try:
                            if not dry_run:
                                await client.reset_client_traffic(inb_id, email)
                            stats['reset'] += 1
                        except Exception as e:
                            stats['errors'] += 1
                            logger.warning(
                                f"ensure_subscription_keys: не удалось сбросить трафик {email} "
                                f"в inbound {inb_id}: {e}"
                            )
                    if not needs_update:
                        stats['skipped'] += 1
                        continue
                    if dry_run:
                        stats['updated'] += 1
                        if enable_changed:
                            if target_enable:
                                stats['enabled'] += 1
                            else:
                                stats['disabled'] += 1
                        continue
                    try:
                        await _update_client_from_snapshot(
                            client,
                            panel_snapshot,
                            panel_client=cl,
                            inbound_id=inb_id,
                            client_uuid=cid,
                            email=email,
                            expiry_time_ms=expiry_time_ms,
                            total_gb_bytes=target_total_bytes,
                            enable=target_enable,
                            sub_id=sub_id,
                            limit_ip=limit_ip,
                            flow=flow,
                        )
                        stats['updated'] += 1
                        if enable_changed:
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
            if (
                target_inbound_id in visible_inbound_ids
                and target_inbound_id not in presence
            ):
                try:
                    total_gb = _panel_total_gb_for_key(key, 0)
                    days_left = _key_days_left_for_add(key)
                    target_inbound = next(
                        (inb for inb in inbounds if inb.get('id') == target_inbound_id),
                        None,
                    )
                    flow = await _get_inbound_flow_safe(
                        client,
                        target_inbound_id,
                        server_id,
                        target_inbound,
                    )
                    if dry_run:
                        res = {'uuid': key.get('client_uuid') or email}
                    else:
                        res = await _add_client_from_snapshot(
                            client,
                            panel_snapshot,
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
                    presence[target_inbound_id] = presence_after_add(
                        target_inbound_id,
                        res,
                        flow,
                    )
                except Exception as e:
                    stats['errors'] += 1
                    logger.warning(
                        f"ensure_subscription_keys (key-mode): не удалось восстановить клиента {email} "
                        f"в inbound {target_inbound_id} сервера {server_id}: {e}"
                    )

            # The database owns placement. Keep the configured inbound when it
            # is still manageable; only fall back to an existing minimum when
            # the configured inbound no longer exists/is deliberately hidden.
            min_inb_id = (
                target_inbound_id
                if target_inbound_id in presence
                else (min(presence.keys()) if presence else None)
            )
            if min_inb_id is not None and len(presence) > 1:
                for inb_id, cl in list(presence.items()):
                    if inb_id == min_inb_id:
                        continue
                    cid = _client_identifier(cl)
                    if not cid:
                        stats['errors'] += 1
                        continue
                    if dry_run:
                        stats['deleted'] += 1
                        presence.pop(inb_id, None)
                        continue
                    try:
                        await _delete_client_from_snapshot(
                            client,
                            panel_snapshot,
                            inbound_id=inb_id,
                            client_uuid=cid,
                            email=email,
                        )
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
                    if not dry_run:
                        update_vpn_key_config(
                            key_id=key_id,
                            server_id=server_id,
                            panel_inbound_id=min_inb_id,
                            panel_email=email,
                            client_uuid=uuid_or_pwd,
                        )
                if reset_traffic:
                    try:
                        if not dry_run:
                            await client.reset_client_traffic(min_inb_id, email)
                        stats['reset'] += 1
                    except Exception as e:
                        stats['errors'] += 1
                        logger.warning(
                            f"ensure_subscription_keys (key-mode): не удалось сбросить трафик "
                            f"{email} в inbound {min_inb_id}: {e}"
                        )
                min_inbound = next(
                    (inb for inb in inbounds if inb.get('id') == min_inb_id),
                    None,
                )
                flow = await _get_inbound_flow_safe(client, min_inb_id, server_id, min_inbound)
                min_panel_used = 0 if reset_traffic else inbound_usage_by_id.get(min_inb_id, 0)
                target_total_bytes = calculate_panel_total_for_key(key, min_panel_used)
                enable_changed = (
                    _panel_bool(min_client.get('enable'), True) != active
                )
                if not _client_needs_panel_update(
                    min_client,
                    expiry_time_ms=expiry_time_ms,
                    total_gb_bytes=target_total_bytes,
                    enable=active,
                    limit_ip=limit_ip,
                    flow=flow,
                    compare_sub_id=False,
                ):
                    stats['skipped'] += 1
                else:
                    if dry_run:
                        stats['updated'] += 1
                        if enable_changed:
                            if active:
                                stats['enabled'] += 1
                            else:
                                stats['disabled'] += 1
                    else:
                        try:
                            await _update_client_from_snapshot(
                                client,
                                panel_snapshot,
                                panel_client=min_client,
                                inbound_id=min_inb_id,
                                client_uuid=uuid_or_pwd,
                                email=email,
                                expiry_time_ms=expiry_time_ms,
                                total_gb_bytes=target_total_bytes,
                                enable=active,
                                limit_ip=limit_ip,
                                flow=flow,
                            )
                            stats['updated'] += 1
                            if enable_changed:
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


async def ensure_subscription_keys_on_server(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Coordinate and materialize one key, optionally using a batch snapshot."""
    if dry_run:
        return await _ensure_subscription_keys_on_server_impl(
            key_id,
            reset_traffic=reset_traffic,
            panel_snapshot=panel_snapshot,
            dry_run=True,
        )
    async with panel_sync_coordinator.regular():
        return await _ensure_subscription_keys_on_server_impl(
            key_id,
            reset_traffic=reset_traffic,
            panel_snapshot=panel_snapshot,
            dry_run=dry_run,
        )


async def sync_key_to_panel_state(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
) -> Dict[str, int]:
    """
    A single point of synchronization of the key state from the database to the panel.

    For subscription mode, updates all clients with the same email/subId in all
    inbound. For key mode, it updates the main client and cleans up unnecessary ones through that
    the same materialization of the state.
    """
    return await ensure_subscription_keys_on_server(
        key_id,
        reset_traffic=reset_traffic,
        panel_snapshot=panel_snapshot,
    )


async def get_subscription_url_for_key(key: Dict[str, Any]) -> Optional[str]:
    """
    Returns the HTTP subscription URL for the key.

    Args:
        key: dict with fields sub_id, server_id (+ regular server fields if any)

    Returns:
        Subscription URL or None (if the key does not have a sub_id or the server is unavailable)
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
    "calculate_panel_total_for_key",
    "format_traffic", "close_all_clients", "get_client", "test_server_connection",
    "reset_key_traffic_if_active", "extend_key_on_server", "restore_key_traffic_limit",
    "push_key_to_panel", "restore_traffic_limit_in_db",
    "get_bot_mode", "is_subscription_mode",
    "ensure_subscription_keys_on_server", "sync_key_to_panel_state",
    "get_subscription_url_for_key", "get_key_traffic_snapshot",
]
