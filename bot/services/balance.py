"""Доменные операции с балансом пользователя."""
from __future__ import annotations

from typing import Any


async def credit_user_balance(
    user_id: int,
    cents: int,
    *,
    source: str,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    performed_by: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Пополняет баланс через единый сервисный путь и пишет историю."""
    return await _apply_balance_operation(
        user_id,
        cents,
        operation_type='credit',
        source=source,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        performed_by=performed_by,
        metadata=metadata,
    )


async def debit_user_balance(
    user_id: int,
    cents: int,
    *,
    source: str,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    performed_by: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Списывает баланс через единый сервисный путь и пишет историю."""
    return await _apply_balance_operation(
        user_id,
        cents,
        operation_type='debit',
        source=source,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        performed_by=performed_by,
        metadata=metadata,
    )


async def _apply_balance_operation(
    user_id: int,
    cents: int,
    *,
    operation_type: str,
    source: str,
    reason: str,
    reference_type: str | None,
    reference_id: str | None,
    performed_by: int | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    from bot.services.user_locks import user_locks
    from database.requests import apply_balance_operation

    async with user_locks[user_id]:
        return apply_balance_operation(
            user_id=user_id,
            operation_type=operation_type,
            cents=cents,
            source=source,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            performed_by=performed_by,
            metadata=metadata,
        )


__all__ = ['credit_user_balance', 'debit_user_balance']
