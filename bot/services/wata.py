"""Persisted, serialized verification of WATA transaction evidence."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID
from weakref import WeakValueDictionary

from database.requests import claim_wata_payment_check, save_wata_payment_check
from bot.services.payment_api import PaymentApiError, PaymentApiRateLimitError, PaymentApiResponseError

WATA_CHECK_INTERVAL_SECONDS = 45
_locks: WeakValueDictionary = WeakValueDictionary()


class WataCheckDeferred(PaymentApiRateLimitError):
    """The persisted gate prevented an HTTP request."""


def wata_order_lock(order_id: str) -> asyncio.Lock:
    lock = _locks.get(order_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[order_id] = lock
    return lock


def _amount(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation) as error:
        raise PaymentApiResponseError('Invalid WATA amount') from error
    if not amount.is_finite() or amount <= 0:
        raise PaymentApiResponseError('Invalid WATA amount')
    return amount


def _uuid(value) -> str:
    if not isinstance(value, str):
        raise PaymentApiResponseError('Invalid WATA transaction/cursor id')
    try:
        UUID(value)
    except ValueError as error:
        raise PaymentApiResponseError('Invalid WATA transaction/cursor id') from error
    return value


def _page(data, *, order_id, amount, currency, previous_cursor):
    if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
            or type(data.get('hasNextPage')) is not bool):
        raise PaymentApiResponseError('Incomplete WATA transactions page')
    for item in data['items']:
        if (not isinstance(item, dict)
                or item.get('kind') not in {'Payment', 'Refund'}
                or item.get('status') not in {'Created', 'Pending', 'Paid', 'Declined'}
                or not isinstance(item.get('currency'), str)
                or 'orderId' not in item):
            raise PaymentApiResponseError('Incomplete WATA transaction')
        transaction_id = _uuid(item.get('id'))
        received_amount = _amount(item.get('amount'))
        if (item['kind'] == 'Payment' and item['status'] == 'Paid'
                and item['orderId'] == order_id and received_amount == amount
                and item['currency'] == currency):
            return transaction_id, None
    cursor = None
    if data['hasNextPage']:
        cursor = {'CursorId': _uuid(data.get('nextCursorId')), 'CursorDate': data.get('nextCursorDate')}
        try:
            datetime.fromisoformat(cursor['CursorDate'].replace('Z', '+00:00'))
        except (AttributeError, TypeError, ValueError) as error:
            raise PaymentApiResponseError('Incomplete WATA pagination cursor') from error
        if cursor == previous_cursor or not data['items']:
            raise PaymentApiResponseError('WATA pagination did not advance')
    return None, cursor


async def check_wata_invoice(order_id: str) -> str:
    """Check one page per allowed request; only exact Paid evidence settles money."""
    from bot.services.billing import fetch_wata_transactions
    from bot.services.payment_intents import load_payment_intent

    async with wata_order_lock(order_id):
        order, claimed = claim_wata_payment_check(
            order_id, now=time.time(), interval=WATA_CHECK_INTERVAL_SECONDS,
        )
        if order['status'] != 'pending':
            return order['status']
        check = order['metadata'].get('wata_check') or {}
        if not claimed:
            raise WataCheckDeferred(
                'WATA check is deferred',
                retry_after_seconds=max(1, math.ceil(check['not_before'] - time.time())),
            )
        intent = load_payment_intent(order_id)
        amount = _amount(order.get('charge_amount'))
        currency = order.get('charge_currency')
        if (intent is None or intent.charge_amount != amount or intent.charge_currency != currency
                or currency not in {'RUB', 'USD', 'EUR', 'GBP', 'BYN'}
                or order.get('purpose') != intent.purpose or order.get('payment_type') != 'wata'):
            raise PaymentApiResponseError('WATA immutable invoice snapshot is inconsistent')
        cursor = check.get('cursor')
        try:
            data = await fetch_wata_transactions(order_id, cursor=cursor)
        except PaymentApiError as error:
            if isinstance(error, PaymentApiRateLimitError) or error.retry_after_seconds:
                delay = max(WATA_CHECK_INTERVAL_SECONDS, error.retry_after_seconds or 0)
                save_wata_payment_check(order_id, not_before=time.time() + delay)
                error.retry_after_seconds = delay
            raise
        transaction_id, next_cursor = _page(
            data, order_id=order_id, amount=amount, currency=currency, previous_cursor=cursor,
        )
        saved = save_wata_payment_check(order_id, cursor=next_cursor, transaction_id=transaction_id)
        return saved['status']
