"""
Общие операции жизненного цикла VPN-ключей.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def renew_key_access(
    key_id: int,
    days: int,
    reset_traffic: bool = True,
    tariff_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Универсально продлевает или уменьшает срок ключа и синхронизирует панель.

    БД остаётся источником истины. Если панель недоступна или обновилась
    частично, изменение в БД не откатывается: повторная синхронизация сможет
    дожать состояние позже.
    """
    from database.requests import extend_vpn_key
    from bot.services.vpn_api import restore_traffic_limit_in_db, sync_key_to_panel_state

    result: Dict[str, Any] = {
        'db_updated': False,
        'traffic_restored': False,
        'panel_synced': False,
        'sync_stats': {},
    }

    if not key_id or not days:
        return result

    paid_traffic_limit: Optional[int] = None
    if tariff_id:
        from database.requests import get_tariff_by_id

        tariff = get_tariff_by_id(tariff_id)
        if not tariff:
            logger.error(f"renew_key_access: тариф {tariff_id} не найден для ключа {key_id}")
            return result

        paid_traffic_limit = (tariff.get('traffic_limit_gb', 0) or 0) * (1024 ** 3)

    if not extend_vpn_key(key_id, days):
        logger.error(f"renew_key_access: не удалось обновить срок ключа {key_id}")
        return result

    result['db_updated'] = True

    if tariff_id:
        from database.requests import update_vpn_key_tariff_and_traffic_limit

        result['traffic_restored'] = update_vpn_key_tariff_and_traffic_limit(
            key_id,
            tariff_id,
            paid_traffic_limit or 0,
        )
    else:
        result['traffic_restored'] = restore_traffic_limit_in_db(key_id)

    panel_reset_traffic = reset_traffic and not tariff_id
    try:
        sync_stats = await sync_key_to_panel_state(key_id, reset_traffic=panel_reset_traffic)
        result['sync_stats'] = sync_stats
        result['panel_synced'] = bool(sync_stats.get('ok')) and sync_stats.get('errors', 0) == 0
    except Exception as e:
        logger.warning(f"renew_key_access: панель не синхронизирована для ключа {key_id}: {e}")
        result['sync_stats'] = {'errors': 1, 'ok': 0}

    await emit_key_lifecycle_event_safe(
        'key_renewed',
        {
            'key_id': key_id,
            'days': days,
            'reset_traffic': reset_traffic,
            'tariff_id': tariff_id,
            'paid_traffic_limit': paid_traffic_limit,
            'result': dict(result),
        },
    )
    return result


async def emit_key_lifecycle_event_safe(event: str, context: Dict[str, Any]) -> list[dict]:
    """Вызывает lifecycle hooks и защищает основной flow от ошибок registry."""
    try:
        from bot.utils.lifecycle_registry import emit_key_lifecycle_event

        return await emit_key_lifecycle_event(event, context)
    except Exception as e:
        logger.warning(f"Lifecycle hooks для события {event} не выполнены: {e}")
        return []


async def process_expired_key_lifecycle_events(limit: Optional[int] = None) -> list[Dict[str, Any]]:
    """Эмитит key_expired один раз для каждого key_id+expires_at."""
    from database.requests import (
        get_pending_expired_key_events,
        record_key_lifecycle_event_once,
    )

    processed: list[Dict[str, Any]] = []
    for key in get_pending_expired_key_events(limit=limit):
        key_id = int(key['id'])
        event_token = str(key.get('expires_at') or '')
        context = {
            'key_id': key_id,
            'user_id': key.get('user_id'),
            'telegram_id': key.get('telegram_id'),
            'tariff_id': key.get('tariff_id'),
            'tariff_name': key.get('tariff_name'),
            'server_id': key.get('server_id'),
            'server_name': key.get('server_name'),
            'panel_email': key.get('panel_email'),
            'expires_at': key.get('expires_at'),
            'custom_name': key.get('custom_name'),
            'traffic_limit': key.get('traffic_limit'),
            'traffic_used': key.get('traffic_used'),
            'is_banned': key.get('is_banned'),
        }
        claimed = record_key_lifecycle_event_once(
            key_id=key_id,
            event_name='key_expired',
            event_token=event_token,
            metadata={
                'expires_at': key.get('expires_at'),
                'telegram_id': key.get('telegram_id'),
                'tariff_id': key.get('tariff_id'),
                'server_id': key.get('server_id'),
            },
        )
        if not claimed:
            continue

        hook_results = await emit_key_lifecycle_event_safe('key_expired', context)
        processed.append({
            'key_id': key_id,
            'event_token': event_token,
            'hook_results': hook_results,
        })

    if processed:
        logger.info("Обработано key_expired lifecycle events: %s", len(processed))
    return processed


async def sync_user_keys_panel_access(telegram_id: int) -> Dict[str, Any]:
    """
    Синхронизирует доступ всех ключей пользователя с панелью после бана/разбана.

    Сам статус бана остаётся в БД. sync_key_to_panel_state() перечитывает ключ
    вместе с users.is_banned и выставляет enable на панели по актуальному состоянию.
    """
    from database.requests import get_user_by_telegram_id, get_user_vpn_keys
    from bot.services.vpn_api import sync_key_to_panel_state

    result: Dict[str, Any] = {
        'user_found': False,
        'keys_total': 0,
        'synced': 0,
        'errors': 0,
        'details': [],
    }

    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return result

    result['user_found'] = True
    keys = get_user_vpn_keys(user['id'])
    result['keys_total'] = len(keys)

    for key in keys:
        key_id = key.get('id')
        if not key_id:
            continue

        try:
            stats = await sync_key_to_panel_state(key_id)
            errors = int(stats.get('errors', 0) or 0)
            if errors:
                result['errors'] += errors
            else:
                result['synced'] += 1
            result['details'].append({'key_id': key_id, 'stats': stats})
        except Exception as e:
            result['errors'] += 1
            result['details'].append({'key_id': key_id, 'error': str(e)})
            logger.warning(
                f"sync_user_keys_panel_access: не удалось синхронизировать ключ "
                f"{key_id} пользователя {telegram_id}: {e}"
            )

    return result
