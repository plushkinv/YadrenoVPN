"""Limited core facade for custom extensions."""
from __future__ import annotations

from typing import Any


class ExtensionCoreAPI:
    """Safe kernel read/command operations for one extension_id."""

    def __init__(self, extension_id: str):
        self.extension_id = extension_id

    def get_user_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        """Returns a secure user profile without secrets and service fields."""
        telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
        from database.requests import get_user_by_telegram_id

        user = get_user_by_telegram_id(telegram_id)
        if not user:
            return None
        return {
            'id': user.get('id'),
            'telegram_id': user.get('telegram_id'),
            'username': user.get('username'),
            'first_name': user.get('first_name'),
            'last_name': user.get('last_name'),
            'created_at': user.get('created_at'),
            'is_banned': bool(user.get('is_banned')),
            'is_bot_blocked': bool(user.get('is_bot_blocked')),
            'personal_balance': user.get('personal_balance') or 0,
        }

    def get_user_keys(self, telegram_id: int) -> list[dict[str, Any]]:
        """Returns display data of the user's keys without VPN secrets."""
        telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
        from database.requests import get_user_keys_for_display

        allowed = {
            'id',
            'display_name',
            'custom_name',
            'expires_at',
            'is_active',
            'traffic_used',
            'traffic_limit',
            'server_name',
            'inbound_name',
            'protocol',
            'tariff_name',
        }
        return [
            {key: value for key, value in dict(item).items() if key in allowed}
            for item in get_user_keys_for_display(telegram_id)
        ]

    async def grant_days_to_first_active_key(
        self,
        *,
        days: int,
        reason: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Accrues days to the user's first active key via core-log."""
        _ensure_mutation_allowed('grant_days_to_first_active_key')
        target_user_id = _resolve_user_id(user_id=user_id, telegram_id=telegram_id)
        return await _apply_core_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='grant_days_to_first_active_key',
            target_user_id=target_user_id,
            amount=_normalize_positive_int(days, 'days'),
            reason=reason,
        )

    async def add_balance_bonus(
        self,
        *,
        cents: int,
        reason: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Credits a bonus to the user's balance via core-log."""
        _ensure_mutation_allowed('add_balance_bonus')
        target_user_id = _resolve_user_id(user_id=user_id, telegram_id=telegram_id)
        return await _apply_core_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='add_balance_bonus',
            target_user_id=target_user_id,
            amount=_normalize_positive_int(cents, 'cents'),
            reason=reason,
        )

    async def check_telegram_chat_member(
        self,
        chat_id: int | str,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Checks Telegram chat membership through the current bot runtime context."""
        if telegram_id is None:
            from bot.utils.custom_extensions import _get_current_extension_telegram_id

            telegram_id = _get_current_extension_telegram_id()
        if telegram_id is None:
            raise ValueError('telegram_id is required')
        from bot.services.telegram_membership import check_telegram_chat_member
        from bot.utils.custom_extensions import _get_current_extension_bot

        return await check_telegram_chat_member(
            _get_current_extension_bot(),
            chat_id=chat_id,
            telegram_id=_normalize_positive_int(telegram_id, 'telegram_id'),
        )


def _resolve_user_id(*, user_id: int | None, telegram_id: int | None) -> int:
    if user_id is not None and telegram_id is not None:
        raise ValueError('передайте только user_id или только telegram_id')
    if user_id is not None:
        return _normalize_positive_int(user_id, 'user_id')
    if telegram_id is None:
        raise ValueError('нужно передать user_id или telegram_id')
    telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
    from database.requests import get_user_internal_id

    resolved = get_user_internal_id(telegram_id)
    if not resolved:
        raise ValueError('пользователь не найден')
    return int(resolved)


async def _apply_core_operation(**kwargs: Any) -> dict[str, Any]:
    from bot.services.extension_core_ops import apply_extension_core_operation

    return await apply_extension_core_operation(**kwargs)


def _ensure_mutation_allowed(operation: str) -> None:
    from bot.utils.action_policy import ensure_action_policy_read_only

    ensure_action_policy_read_only(operation)


def _normalize_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} должен быть положительным integer')
    return value


__all__ = ['ExtensionCoreAPI']
