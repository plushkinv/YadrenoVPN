"""Доменные начисления наград, применяемые ядром."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def grant_days_to_first_active_key(
    user_id: int,
    days: int,
    *,
    source: str,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Начисляет дни на первый активный ключ пользователя через штатный lifecycle.

    Критичное состояние ключа меняет `renew_key_access()`, а этот сервис
    добавляет блокировку пользователя и видимую бизнес-историю.
    """
    _positive_int(user_id, 'user_id')
    _positive_int(days, 'days')
    _required_text(source, 'source')
    _required_text(reason, 'reason')

    from bot.services.user_locks import user_locks
    from database.requests import (
        get_first_active_key_for_user,
        record_key_operation,
    )

    async with user_locks[user_id]:
        key = get_first_active_key_for_user(user_id)
        if not key:
            return {
                'ok': False,
                'status': 'no_op',
                'reason': 'no_active_key',
                'user_id': user_id,
                'days': days,
            }

        key_id = int(key['id'])
        expires_before = key.get('expires_at')

        from bot.services.key_lifecycle import renew_key_access

        renew_result = await renew_key_access(
            key_id,
            days,
            reset_traffic=False,
            tariff_id=None,
        )
        if not renew_result.get('db_updated'):
            return {
                'ok': False,
                'status': 'failed',
                'reason': 'renew_failed',
                'key_id': key_id,
                'user_id': user_id,
                'days': days,
                'renew_result': renew_result,
            }

        updated_key = get_first_active_key_for_user(user_id) or {}
        expires_after = updated_key.get('expires_at')
        operation_id = record_key_operation(
            key_id=key_id,
            user_id=user_id,
            operation_type='grant_days',
            delta_days=days,
            source=source,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            expires_before=str(expires_before) if expires_before is not None else None,
            expires_after=str(expires_after) if expires_after is not None else None,
            metadata={
                **(metadata or {}),
                'panel_synced': bool(renew_result.get('panel_synced')),
                'sync_stats': renew_result.get('sync_stats') or {},
            },
        )
        logger.info(
            "Начислено %s дней key=%s user=%s source=%s operation_id=%s",
            days,
            key_id,
            user_id,
            source,
            operation_id,
        )
        return {
            'ok': True,
            'status': 'applied',
            'key_id': key_id,
            'user_id': user_id,
            'days': days,
            'operation_id': operation_id,
            'expires_before': expires_before,
            'expires_after': expires_after,
            'renew_result': renew_result,
        }


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} должен быть положительным integer')
    return int(value)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} должен быть непустой строкой')
    return value.strip()


__all__ = ['grant_days_to_first_active_key']
