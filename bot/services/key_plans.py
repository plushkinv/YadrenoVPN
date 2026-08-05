"""Administrator-only full VPN key plan reissue operations."""
from __future__ import annotations

import logging
from typing import Any

from bot.services.panel_sync_coordinator import regular_panel_operation

logger = logging.getLogger(__name__)

GIB = 1024 ** 3


@regular_panel_operation
async def reissue_key_plan(
    key_id: int,
    target_tariff_id: int,
    *,
    custom_traffic_gb: int | None = None,
    custom_duration_days: int | None = None,
    custom_max_ips: int | None = None,
    performed_by: int | None = None,
) -> dict[str, Any]:
    """Fully replaces one key plan and best-effort synchronizes its panel state."""
    from database.requests import (
        get_tariff_by_id,
        get_vpn_key_by_id,
        record_key_operation,
        reissue_vpn_key_plan,
    )

    key = get_vpn_key_by_id(int(key_id))
    tariff = get_tariff_by_id(int(target_tariff_id))
    if key is None or tariff is None:
        return {'ok': False, 'reason': 'key_or_tariff_not_found'}
    if int(key.get('tariff_group_id') or 1) != int(tariff.get('group_id') or 1):
        return {'ok': False, 'reason': 'different_tariff_group'}

    is_custom = tariff.get('system_type') == 'admin_custom'
    if tariff.get('system_type') is not None and not is_custom:
        return {'ok': False, 'reason': 'unsupported_system_tariff'}

    if is_custom:
        if (
            custom_traffic_gb is None
            or custom_duration_days is None
            or custom_max_ips is None
        ):
            return {'ok': False, 'reason': 'custom_values_required'}
        traffic_gb = int(custom_traffic_gb)
        duration_days = int(custom_duration_days)
        max_ips = int(custom_max_ips)
        if not 0 <= traffic_gb <= 99999:
            return {'ok': False, 'reason': 'invalid_custom_traffic'}
        if not 0 <= duration_days <= 99999:
            return {'ok': False, 'reason': 'invalid_custom_duration'}
        if not 1 <= max_ips <= 999:
            return {'ok': False, 'reason': 'invalid_custom_max_ips'}
        traffic_limit = traffic_gb * GIB
        traffic_override = traffic_limit
        max_ips_override = max_ips
    else:
        traffic_gb = max(0, int(tariff.get('traffic_limit_gb') or 0))
        duration_days = max(0, int(tariff.get('duration_days') or 0))
        max_ips = max(1, min(999, int(tariff.get('max_ips') or 1)))
        traffic_limit = traffic_gb * GIB
        traffic_override = None
        max_ips_override = None

    from bot.services.user_locks import user_locks

    user_id = int(key['user_id'])
    async with user_locks[user_id]:
        change = reissue_vpn_key_plan(
            int(key_id),
            int(target_tariff_id),
            duration_days,
            traffic_limit,
            traffic_limit_override=traffic_override,
            max_ips_override=max_ips_override,
        )
        if change is None:
            return {'ok': False, 'reason': 'database_update_failed'}

        operation_id = record_key_operation(
            key_id=int(key_id),
            user_id=user_id,
            operation_type='change_tariff_plan',
            source='admin',
            reason=(
                'Переоформление на произвольный тариф'
                if is_custom
                else f"Переоформление на тариф «{tariff.get('name') or target_tariff_id}»"
            ),
            expires_before=(
                str(change['expires_before'])
                if change.get('expires_before') is not None
                else None
            ),
            expires_after=(
                str(change['expires_after'])
                if change.get('expires_after') is not None
                else None
            ),
            metadata={
                'performed_by': performed_by,
                'tariff_id_before': change['tariff_id_before'],
                'tariff_id_after': change['tariff_id_after'],
                'custom': is_custom,
                'traffic_limit': traffic_limit,
                'duration_days': duration_days,
                'max_ips': max_ips,
            },
        )

        from bot.services.vpn_api import sync_key_to_panel_state

        try:
            sync_stats = await sync_key_to_panel_state(
                int(key_id),
                reset_traffic=True,
            )
        except Exception as error:
            logger.warning(
                "Admin plan reissue panel sync failed key=%s: %s",
                key_id,
                error,
            )
            sync_stats = {'ok': 0, 'errors': 1}

    panel_synced = bool(sync_stats.get('ok')) and not int(
        sync_stats.get('errors', 0) or 0
    )
    return {
        'ok': True,
        'db_updated': True,
        'panel_synced': panel_synced,
        'sync_stats': sync_stats,
        'operation_id': operation_id,
        'custom': is_custom,
        'traffic_limit': traffic_limit,
        'duration_days': duration_days,
        'max_ips': max_ips,
        'tariff': tariff,
    }


__all__ = ['reissue_key_plan']
