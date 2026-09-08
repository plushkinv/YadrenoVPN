"""Bounded, secret-free payment projections for installed extensions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .connection import get_db
from .payment_semantics import paid_key_purchase_predicate

__all__ = [
    'get_extension_payment', 'list_extension_user_payments',
    'get_extension_user_payment_summary', 'get_extension_user_identity',
    'get_extension_key_user_identity',
]

_PAYMENT_FIELDS = (
    'id', 'order_id', 'user_id', 'purpose', 'status', 'fulfillment_status',
    'payment_type', 'base_currency', 'nominal_amount_minor',
    'payable_amount_minor', 'balance_deduct_minor', 'charge_amount',
    'charge_currency', 'tariff_id', 'vpn_key_id', 'promo_code_id',
    'created_at', 'paid_at', 'provider_confirmed_at', 'fulfilled_at',
)
_DATES = {'created_at', 'paid_at', 'provider_confirmed_at', 'fulfilled_at'}
_PURPOSES = {
    'key_purchase', 'key_renewal', 'balance_topup', 'trial',
    'historical_key_payment',
}
_STATUSES = {'pending', 'paid', 'failed', 'canceled', 'cancelled', 'expired'}


def iso_utc(value: Any) -> str | None:
    """Normalize persisted timestamps; tolerate empty legacy dates."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except (TypeError, ValueError):
        return None


def normalize_datetime(value: Any, field: str) -> str | None:
    """Accept an ISO timestamp, treating a naive value as UTC."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or iso_utc(value) is None:
        raise ValueError(f'{field} must be an ISO timestamp or None')
    return iso_utc(value)


def positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} must be a positive integer')
    return value


def bounded_page(limit: int, cursor: str | None) -> tuple[int, int | None]:
    limit = positive_int(limit, 'limit')
    if limit > 100:
        raise ValueError('limit must not exceed 100')
    if cursor is None:
        return limit, None
    if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdigit():
        raise ValueError('cursor must be a previously returned cursor')
    value = int(cursor) if len(cursor) <= 19 else 0
    if not 0 < value <= 9223372036854775807:
        raise ValueError('invalid cursor')
    return limit, value


def get_extension_user_identity(*, user_id: int) -> dict[str, int] | None:
    with get_db() as conn:
        row = conn.execute(
            'SELECT id AS user_id, telegram_id FROM users WHERE id = ?',
            (positive_int(user_id, 'user_id'),),
        ).fetchone()
        return dict(row) if row else None


def get_extension_key_user_identity(key_id: int) -> dict[str, int] | None:
    with get_db() as conn:
        row = conn.execute(
            'SELECT u.id AS user_id, u.telegram_id FROM vpn_keys k '
            'JOIN users u ON u.id = k.user_id WHERE k.id = ?',
            (positive_int(key_id, 'key_id'),),
        ).fetchone()
        return dict(row) if row else None


def payment_select() -> str:
    return ', '.join(f'p.{name}' for name in _PAYMENT_FIELDS) + (
        f', CASE WHEN {paid_key_purchase_predicate("p")} THEN 1 ELSE 0 END AS is_paid_key'
        ', p.is_promo_free'
    )


def payment_projection(row) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    result = {name: item.get(name) for name in _PAYMENT_FIELDS}
    for name in _DATES:
        result[name] = iso_utc(result[name])
    for name in ('nominal_amount_minor', 'payable_amount_minor', 'balance_deduct_minor'):
        result[name] = int(result[name] or 0)
    if result['charge_amount'] is not None:
        result['charge_amount'] = str(result['charge_amount'])
    result.update(
        contract_version=1,
        is_paid_key=bool(item.get('is_paid_key')),
        is_free=bool(item.get('is_promo_free') or item.get('payment_type') == 'promo_free'),
        is_trial=item.get('purpose') == 'trial' or item.get('payment_type') == 'trial',
    )
    return result


def payment_with_conn(conn, order_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f'SELECT {payment_select()} FROM payments p WHERE p.order_id = ?',
        (order_id,),
    ).fetchone()
    return payment_projection(row)


def get_extension_payment(order_id: str) -> dict[str, Any] | None:
    if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 256:
        raise ValueError('order_id must be a non-empty string of at most 256 characters')
    with get_db() as conn:
        return payment_with_conn(conn, order_id.strip())


def _filters(user_id, purpose, status, since, until):
    conditions = ['p.user_id = ?']
    params: list[Any] = [positive_int(user_id, 'user_id')]
    for name, value, allowed in (
        ('purpose', purpose, _PURPOSES), ('status', status, _STATUSES),
    ):
        if value is not None:
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f'unsupported {name}')
            conditions.append(f'p.{name} = ?')
            params.append(value)
    start, end = normalize_datetime(since, 'since'), normalize_datetime(until, 'until')
    if start and end and datetime.fromisoformat(start) >= datetime.fromisoformat(end):
        raise ValueError('since must precede until')
    for operator, value in (('>=', start), ('<', end)):
        if value:
            conditions.append(f'julianday(p.created_at) {operator} julianday(?)')
            params.append(value)
    return ' AND '.join(conditions), params


def list_extension_user_payments(
    user_id: int, *, purpose=None, status=None, since=None, until=None,
    cursor: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    limit, before_id = bounded_page(limit, cursor)
    where, params = _filters(user_id, purpose, status, since, until)
    if before_id is not None:
        where += ' AND p.id < ?'
        params.append(before_id)
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT {payment_select()} FROM payments p WHERE {where} '
            'ORDER BY p.id DESC LIMIT ?', (*params, limit + 1),
        ).fetchall()
    return {
        'contract_version': 1,
        'items': [payment_projection(row) for row in rows[:limit]],
        'next_cursor': str(rows[limit - 1]['id']) if len(rows) > limit else None,
        'limit': limit,
    }


def summary_with_conn(conn, user_id, *, purpose=None, status=None, since=None, until=None):
    where, params = _filters(user_id, purpose, status, since, until)
    row = conn.execute(
        f"""SELECT COUNT(*) AS total_count,
        COALESCE(SUM(p.status = 'paid'), 0) AS successful_count,
        COALESCE(SUM(CASE WHEN {paid_key_purchase_predicate('p')} THEN 1 ELSE 0 END), 0)
            AS paid_key_count,
        MAX(CASE WHEN p.status = 'paid' THEN datetime(p.paid_at) END) AS last_payment_at
        FROM payments p WHERE {where}""", params,
    ).fetchone()
    amounts = conn.execute(
        f"""SELECT UPPER(p.base_currency) AS currency,
        COALESCE(SUM(p.nominal_amount_minor), 0) AS nominal_amount_minor,
        COALESCE(SUM(p.payable_amount_minor), 0) AS payable_amount_minor,
        COALESCE(SUM(p.balance_deduct_minor), 0) AS balance_deduct_minor
        FROM payments p WHERE {where} AND p.status = 'paid'
        AND TRIM(COALESCE(p.base_currency, '')) <> ''
        GROUP BY UPPER(p.base_currency) ORDER BY UPPER(p.base_currency)""", params,
    ).fetchall()
    result = dict(row)
    result.update(
        contract_version=1, has_ever_paid_key=result['paid_key_count'] > 0,
        last_payment_at=iso_utc(result['last_payment_at']),
        amounts_by_currency={
            value['currency']: {key: int(value[key]) for key in (
                'nominal_amount_minor', 'payable_amount_minor', 'balance_deduct_minor',
            )} for value in amounts
        },
    )
    return result


def get_extension_user_payment_summary(user_id: int, **filters) -> dict[str, Any]:
    with get_db() as conn:
        return summary_with_conn(conn, user_id, **filters)
