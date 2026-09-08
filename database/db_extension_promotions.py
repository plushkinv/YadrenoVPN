"""Native promotional operations with transactional extension idempotency."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .connection import get_db
from .db_extension_core import (
    build_extension_core_request_fingerprint, claim_extension_core_operation,
    finalize_extension_core_operation,
)
from .db_extension_payments import bounded_page, iso_utc, normalize_datetime, positive_int
from .db_promotions import (
    _generate_code, activate_user_promo_code, clear_user_active_promo_code,
    create_promo_code, get_promo_code_availability, is_base62_code, update_promo_code,
)

__all__ = [
    'get_extension_promo_code', 'list_extension_promo_codes',
    'check_extension_promo_code', 'apply_extension_promo_operation',
]
_FIELDS = (
    'id', 'type', 'code', 'discount_percent', 'expires_at', 'is_active',
    'activation_limit', 'usage_count', 'source', 'issued_to_user_id',
    'created_at', 'updated_at',
)
_OPERATIONS = {'create_promo_code', 'update_promo_code', 'activate_promo_code', 'clear_active_promo_code'}


def _projection(row) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    item = {field: data.get(field) for field in _FIELDS}
    item['is_active'] = bool(item['is_active'])
    if item['type'] == 'coupon':
        item['activation_limit'] = 1
    for field in ('expires_at', 'created_at', 'updated_at'):
        item[field] = iso_utc(item[field])
    item['contract_version'] = 1
    return item


def _code(value) -> str:
    if not isinstance(value, str) or not is_base62_code(value.strip()) or len(value.strip()) > 128:
        raise ValueError('code must contain 1 to 128 base62 characters')
    return value.strip()


def _code_type(value) -> str:
    if value not in ('promo', 'coupon'):
        raise ValueError('code_type must be promo or coupon')
    return value


def get_extension_promo_code(code: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute('SELECT * FROM promo_codes WHERE code = ?', (_code(code),)).fetchone()
        return _projection(row)


def list_extension_promo_codes(
    *, code_type: str | None = None, include_inactive: bool = True,
    cursor: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    limit, before_id = bounded_page(limit, cursor)
    if not isinstance(include_inactive, bool):
        raise ValueError('include_inactive must be bool')
    conditions, params = ['1=1'], []
    if code_type is not None:
        conditions.append('type = ?')
        params.append(_code_type(code_type))
    if not include_inactive:
        conditions.append('is_active = 1')
    if before_id is not None:
        conditions.append('id < ?')
        params.append(before_id)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM promo_codes WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    return {
        'contract_version': 1, 'items': [_projection(row) for row in rows[:limit]],
        'next_cursor': str(rows[limit - 1]['id']) if len(rows) > limit else None,
        'limit': limit,
    }


def check_extension_promo_code(code: str, *, user_id: int) -> dict[str, Any]:
    result = get_promo_code_availability(
        _code(code), user_id=positive_int(user_id, 'user_id'), block_user_reservations=True,
    )
    return {**result, 'promo': _projection(result.get('promo'))}


def _normalize_fields(payload: dict[str, Any], *, create: bool) -> dict[str, Any]:
    allowed = {'discount_percent', 'expires_at', 'activation_limit', 'is_active'}
    if create:
        allowed |= {'code', 'code_type', 'lifetime_days', 'issued_to_user_id'}
    if set(payload) - allowed:
        raise ValueError('unsupported promo fields')
    values = dict(payload)
    if create and 'discount_percent' not in values:
        raise ValueError('discount_percent is required')
    if 'discount_percent' in values:
        discount = values['discount_percent']
        if type(discount) is not int or not 0 <= discount <= 100:
            raise ValueError('discount_percent must be an integer from 0 to 100')
    if 'expires_at' in values:
        values['expires_at'] = normalize_datetime(values['expires_at'], 'expires_at')
    for name in ('activation_limit', 'issued_to_user_id', 'lifetime_days'):
        if values.get(name) is not None:
            values[name] = positive_int(values[name], name)
    if 'is_active' in values and not isinstance(values['is_active'], bool):
        raise ValueError('is_active must be bool')
    if create:
        values['code_type'] = _code_type(values.get('code_type', 'promo'))
        if values.get('code') is not None:
            values['code'] = _code(values['code'])
        if values.get('lifetime_days') is not None and values.get('expires_at') is not None:
            raise ValueError('pass lifetime_days or expires_at, not both')
        if values.get('lifetime_days', 0) and values['lifetime_days'] > 36500:
            raise ValueError('lifetime_days must not exceed 36500')
    return values


def apply_extension_promo_operation(
    *, extension_id: str, idempotency_key: str, operation: str,
    user_id: int | None = None, payload: dict[str, Any],
) -> dict[str, Any]:
    """Commit the ledger result and native promo changes as one transaction."""
    if operation not in _OPERATIONS:
        raise ValueError('unsupported promo operation')
    if operation == 'create_promo_code':
        values = _normalize_fields(payload, create=True)
    elif operation == 'update_promo_code':
        values = _normalize_fields({k: v for k, v in payload.items() if k != 'promo_code_id'}, create=False)
        values['promo_code_id'] = positive_int(payload.get('promo_code_id'), 'promo_code_id')
    elif operation == 'activate_promo_code':
        if set(payload) != {'code'}:
            raise ValueError('activation accepts only code')
        values = {'code': _code(payload.get('code'))}
    else:
        if payload:
            raise ValueError('clear does not accept promo fields')
        values = {}
    fingerprint = build_extension_core_request_fingerprint(
        operation=operation, target_user_id=user_id, amount=None,
        reason='extension_promotion', payload=values,
    )
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        claimed = claim_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key, operation=operation,
            target_user_id=user_id, amount=None, reason='extension_promotion',
            request_fingerprint=fingerprint, _conn=conn,
        )
        if not claimed.get('claimed'):
            return {**claimed, **(claimed.get('metadata') or {})}
        result = _apply(conn, operation, user_id, values, extension_id)
        finalized = finalize_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key,
            status='applied' if result['ok'] else 'rejected', metadata=result, _conn=conn,
        )
        return {**finalized, **result}


def _apply(conn, operation, user_id, values, extension_id):
    values = dict(values)
    if values.get('expires_at') is not None:
        # Keep native storage/UI timestamps while the public API uses UTC ISO dates.
        values['expires_at'] = datetime.fromisoformat(values['expires_at'].replace('Z', '+00:00'))
    if user_id is not None and conn.execute('SELECT 1 FROM users WHERE id = ?', (user_id,)).fetchone() is None:
        return {'ok': False, 'reason': 'user_not_found', 'promo': None}
    if operation == 'activate_promo_code':
        result = activate_user_promo_code(user_id, values['code'], _conn=conn)
        return {**result, 'promo': _projection(result.get('promo'))}
    if operation == 'clear_active_promo_code':
        clear_user_active_promo_code(user_id, _conn=conn)
        return {'ok': True, 'reason': 'cleared', 'promo': None}
    if operation == 'update_promo_code':
        promo_id = values['promo_code_id']
        if conn.execute('SELECT 1 FROM promo_codes WHERE id = ?', (promo_id,)).fetchone() is None:
            return {'ok': False, 'reason': 'not_found', 'promo': None}
        update_promo_code(promo_id, **{k: v for k, v in values.items() if k != 'promo_code_id'}, _conn=conn)
    else:
        creation = dict(values)
        days = creation.pop('lifetime_days', None)
        if days is not None:
            creation['expires_at'] = datetime.now(timezone.utc) + timedelta(days=days)
            creation['snapshot_lifetime_days'] = days
        code = creation.pop('code', None)
        if creation.get('issued_to_user_id') is not None and conn.execute(
            'SELECT 1 FROM users WHERE id = ?', (creation['issued_to_user_id'],),
        ).fetchone() is None:
            return {'ok': False, 'reason': 'user_not_found', 'promo': None}
        for _ in range(100):
            candidate = code or _generate_code()
            try:
                promo_id = create_promo_code(
                    code=candidate, source=f'extension:{extension_id}', **creation, _conn=conn,
                )
                break
            except sqlite3.IntegrityError:
                if conn.execute('SELECT 1 FROM promo_codes WHERE code = ?', (candidate,)).fetchone() is None:
                    raise
                if code is not None:
                    return {'ok': False, 'reason': 'code_exists', 'promo': None}
        else:
            raise RuntimeError('unique code generation failed')
    row = conn.execute('SELECT * FROM promo_codes WHERE id = ?', (promo_id,)).fetchone()
    return {'ok': True, 'reason': 'applied', 'promo': _projection(row)}
