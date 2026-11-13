"""Exact-money transport and verification shared by hosted adapters."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse, quote
from weakref import WeakValueDictionary

import aiohttp
from bot.services.payment_api import (
    PaymentApiResponseError, PaymentApiTransientError, PaymentApiRateLimitError,
    payment_client_timeout, payment_retry_after, run_payment_api_operation,
)


class HttpError(PaymentApiResponseError):
    def __init__(self, status):
        self.status = status
        # Provider error payloads can echo credentials or customer data.
        super().__init__(f'HTTP {status}')


def decimal_json(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite payment amount')
        return format(value, 'f')
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + ':' + decimal_json(v) for k, v in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(decimal_json(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


async def request(provider, operation, order_id, method, url, *, retry=False,
                  headers=None, payload=None, data=None, params=None):
    async def once():
        request_headers = dict(headers or {})
        body = data
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
            body = decimal_json(payload)
        async with aiohttp.ClientSession(timeout=payment_client_timeout()) as session:
            async with session.request(method, url, headers=request_headers, data=body,
                                       params=params, allow_redirects=False) as response:
                retry_after = payment_retry_after(response.headers.get('Retry-After'))
                if response.status >= 500:
                    raise PaymentApiTransientError(f'HTTP {response.status}', retry_after_seconds=retry_after)
                if response.status == 429:
                    raise PaymentApiRateLimitError('HTTP 429', retry_after_seconds=retry_after)
                if not 200 <= response.status < 300:
                    raise HttpError(response.status)
                try:
                    result = await response.json(content_type=None, loads=lambda s: json.loads(s, parse_float=Decimal))
                except (ValueError, TypeError) as exc:
                    raise PaymentApiResponseError('Invalid provider JSON') from exc
                if not isinstance(result, dict):
                    raise PaymentApiResponseError('Invalid provider response')
                return result
    return await run_payment_api_operation(provider=provider, operation=operation,
                                           order_id=order_id, call=once, retry=retry)


def identifier(value):
    value = str(value or '')
    if not value or len(value) > 255 or any(ord(c) < 33 for c in value):
        raise PaymentApiResponseError('Missing or invalid provider identifier')
    return value


def path_id(value):
    return quote(identifier(value), safe='')


def same(actual, expected, field):
    if str(actual or '') != str(expected):
        raise PaymentApiResponseError(f'Provider {field} mismatch')


def amount_matches(actual, expected):
    try:
        value = Decimal(str(actual))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise PaymentApiResponseError('Invalid provider amount') from exc
    if not value.is_finite() or value != expected:
        raise PaymentApiResponseError('Provider amount mismatch')


@dataclass(frozen=True)
class ExpectedPayment:
    order_id: str
    external_id: str
    amount: Decimal
    currency: str
    creation_key: str = ''

    @classmethod
    def from_binding(cls, intent, binding):
        external_id = identifier(binding.get('provider_payment_id'))
        if intent.charge_amount is None or not intent.charge_amount.is_finite() or intent.charge_amount <= 0:
            raise PaymentApiResponseError('Missing immutable payment amount')
        amount_matches(binding.get('charge_amount'), intent.charge_amount)
        same(binding.get('charge_currency'), intent.charge_currency, 'stored currency')
        same(binding.get('payment_type'), intent.payment_type, 'stored type')
        same(binding.get('purpose'), intent.purpose, 'stored purpose')
        return cls(intent.order_id, external_id, intent.charge_amount, intent.charge_currency,
                   str((binding.get('metadata') or {}).get('creation_key') or ''))

    def money(self, amount, currency):
        amount_matches(amount, self.amount)
        same(str(currency or '').upper(), self.currency, 'currency')


def invoice(external_id, url, **metadata):
    external_id = identifier(external_id)
    parsed = urlparse(str(url or ''))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise PaymentApiResponseError('Invalid hosted payment URL')
    return {'provider_payment_id': external_id, 'payment_url': url, 'status': 'pending', 'metadata': metadata}


def idempotency_key(provider, operation, order_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'yadrenovpn:{provider}:{operation}:{order_id}'))


_tokens = {}
_token_locks = WeakValueDictionary()


async def access_token(provider, credentials, loader, *, rejected=None):
    """Single-flight refresh; cache only by credential digest, never log credentials."""
    key = (provider, hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest())
    lock = _token_locks.setdefault(key, asyncio.Lock())
    async with lock:
        token, deadline = _tokens.get(key, ('', 0))
        if token and time.monotonic() < deadline and token != rejected:
            return token
        token, lifetime = await loader()
        if not isinstance(token, str) or not token or any(ord(c) < 33 for c in token):
            raise PaymentApiResponseError('Invalid provider access token')
        if len(_tokens) >= 16:
            _tokens.clear()
        _tokens[key] = (token, time.monotonic() + max(1, float(lifetime) - 30))
        return token
