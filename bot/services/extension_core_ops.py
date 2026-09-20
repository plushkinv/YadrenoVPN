"""Service layer for mutating commands extension core facade."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def apply_extension_core_operation(
    *,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    target_user_id: int,
    amount: int,
    reason: str,
    performed_by: int | None = None,
    _atomic_days: bool = False,
) -> dict[str, Any]:
    """Applies the allowed core extension command through domain services."""
    from database.requests import (
        build_extension_core_request_fingerprint,
        claim_extension_core_operation,
        finalize_extension_core_operation,
    )

    request_fingerprint = build_extension_core_request_fingerprint(
        operation=operation,
        target_user_id=target_user_id,
        amount=amount,
        reason=reason,
    )
    claimed = claim_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=idempotency_key,
        operation=operation,
        target_user_id=target_user_id,
        amount=amount,
        reason=reason,
        request_fingerprint=request_fingerprint,
    )
    if not claimed.get('claimed'):
        return _with_public_flags(claimed)

    try:
        if operation == 'grant_days_to_first_active_key':
            domain_result = await _grant_days(
                target_user_id,
                amount,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
                reason=reason,
                atomic=_atomic_days,
            )
        elif operation == 'add_balance_bonus':
            domain_result = await _add_balance_bonus(
                target_user_id,
                amount,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
                reason=reason,
            )
        elif operation == 'debit_balance':
            domain_result = await _debit_balance(
                target_user_id,
                amount,
                extension_id=extension_id,
                idempotency_key=idempotency_key,
                reason=reason,
                performed_by=performed_by,
            )
        else:
            domain_result = {
                'ok': False,
                'status': 'rejected',
                'reason': f'unknown_operation:{operation}',
            }
    except Exception as exc:
        logger.exception(
            "Extension core operation %s:%s failed: %s",
            extension_id,
            idempotency_key,
            exc,
        )
        domain_result = {'ok': False, 'status': 'failed', 'reason': str(exc)}

    status = _status_from_domain_result(domain_result)
    metadata = {
        'ok': bool(domain_result.get('ok')),
        'operation': operation,
        'target_user_id': target_user_id,
        'amount': amount,
        'amount_minor': amount,
        'performed_by': performed_by,
        **{k: v for k, v in domain_result.items() if k not in {'ok'}},
    }
    finalized = finalize_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=idempotency_key,
        status=status,
        metadata=metadata,
    )
    if domain_result.get('already_applied') or domain_result.get('status') == 'already_applied':
        finalized['status'] = 'already_applied'
        finalized['already_applied'] = True
    return _with_public_flags(finalized)


async def _grant_days(
    user_id: int,
    days: int,
    *,
    extension_id: str,
    idempotency_key: str,
    reason: str,
    atomic: bool = False,
) -> dict[str, Any]:
    from bot.services.rewards import grant_days_to_first_active_key

    return await grant_days_to_first_active_key(
        user_id,
        days,
        source='extension_core',
        reason=reason,
        reference_type='shared_module_operation' if atomic else 'extension_core_operation',
        reference_id=f'{extension_id}:{idempotency_key}',
        metadata={
            'extension_id': extension_id,
            'idempotency_key': idempotency_key,
        },
    )


async def _add_balance_bonus(
    user_id: int,
    cents: int,
    *,
    extension_id: str,
    idempotency_key: str,
    reason: str,
) -> dict[str, Any]:
    from bot.services.balance import credit_user_balance

    return await credit_user_balance(
        user_id,
        cents,
        source='extension_core',
        reason=reason,
        reference_type='extension_core_operation',
        reference_id=f'{extension_id}:{idempotency_key}',
        metadata={
            'extension_id': extension_id,
            'idempotency_key': idempotency_key,
        },
    )


async def _debit_balance(
    user_id: int,
    amount_minor: int,
    *,
    extension_id: str,
    idempotency_key: str,
    reason: str,
    performed_by: int | None,
) -> dict[str, Any]:
    from bot.services.balance import debit_user_balance

    return await debit_user_balance(
        user_id,
        amount_minor,
        source='extension_core',
        reason=reason,
        reference_type='extension_core_operation',
        reference_id=f'{extension_id}:{idempotency_key}',
        performed_by=performed_by,
        metadata={
            'extension_id': extension_id,
            'idempotency_key': idempotency_key,
            'performed_by': performed_by,
        },
    )


def _status_from_domain_result(result: dict[str, Any]) -> str:
    status = str(result.get('status') or '')
    if result.get('ok'):
        return 'applied'
    if status in {'no_op', 'rejected', 'failed'}:
        return status
    if status == 'insufficient_funds':
        return 'rejected'
    if status in {'no_active_key', 'user_not_found'}:
        return 'no_op'
    return 'failed'


def _with_public_flags(result: dict[str, Any]) -> dict[str, Any]:
    status = result.get('status')
    stored = result.get('stored_status') or status
    metadata = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
    domain_status = metadata.get('status')
    if status == 'idempotency_conflict':
        stored = 'idempotency_conflict'
    if stored in {'no_op', 'rejected', 'failed'} and domain_status:
        status = domain_status
    public = {
        **result,
        'status': status,
        'ok': stored == 'applied',
        'applied': status == 'applied' and not result.get('already_applied'),
        'already_applied': bool(result.get('already_applied')),
    }
    for key in (
        'operation_id',
        'operation_type',
        'amount_minor',
        'delta_minor',
        'currency',
        'balance_before',
        'balance_after',
        'performed_by',
    ):
        if key in metadata:
            public[key] = metadata[key]
    return public


__all__ = ['apply_extension_core_operation']
