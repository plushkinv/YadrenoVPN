"""PayPal Orders v2: approval is followed by a server-side idempotent capture."""
from base64 import b64encode
from .common import (HttpError, access_token, idempotency_key, invoice, path_id, request, same)
from bot.services.payment_api import PaymentApiResponseError

ENDPOINTS = {'live': 'https://api-m.paypal.com', 'sandbox': 'https://api-m.sandbox.paypal.com'}


async def _token(settings, order_id, *, rejected=None):
    async def load():
        result = await request('paypal', 'auth', order_id, 'POST',
            ENDPOINTS[settings['environment']] + '/v1/oauth2/token', retry=True,
            headers={'Authorization': 'Basic ' + b64encode(
                (settings['client_id'] + ':' + settings['client_secret']).encode()).decode('ascii')},
            data={'grant_type': 'client_credentials'})
        return result.get('access_token'), result.get('expires_in', 300)
    return await access_token('paypal', settings, load, rejected=rejected)


async def _call(expected, settings, operation, method, suffix='', payload=None):
    token = await _token(settings, expected.order_id)
    for attempt in range(2):
        headers = {'Authorization': 'Bearer ' + token, 'Prefer': 'return=representation'}
        if method == 'POST':
            headers['PayPal-Request-Id'] = idempotency_key('paypal', operation, expected.creation_key or expected.order_id)
        try:
            return await request('paypal', operation, expected.order_id, method,
                ENDPOINTS[settings['environment']] + '/v2/checkout/orders' + suffix,
                headers=headers, payload=payload, retry=True)
        except HttpError as exc:
            if exc.status != 401 or attempt:
                raise
            token = await _token(settings, expected.order_id, rejected=token)


async def create(expected, settings, description, return_url):
    same(expected.currency, 'USD', 'currency')
    result = await _call(expected, settings, 'create', 'POST', payload={
        'intent': 'CAPTURE', 'purchase_units': [{
            'reference_id': expected.order_id, 'custom_id': expected.order_id,
            'description': description[:127],
            'amount': {'currency_code': 'USD', 'value': f'{expected.amount:.2f}'},
        }], 'payment_source': {'paypal': {'experience_context': {
            'return_url': return_url, 'cancel_url': return_url,
            'user_action': 'PAY_NOW', 'shipping_preference': 'NO_SHIPPING',
        }}},
    })
    url = next((item.get('href') for item in result.get('links', [])
                if item.get('rel') in {'approve', 'payer-action'}), None)
    return invoice(result.get('id'), url, environment=settings['environment'])


def _verify(expected, result):
    same(result.get('id'), expected.external_id, 'order id')
    same(result.get('intent'), 'CAPTURE', 'intent')
    units = result.get('purchase_units')
    if not isinstance(units, list) or len(units) != 1:
        raise PaymentApiResponseError('Invalid PayPal purchase units')
    unit = units[0]
    same(unit.get('reference_id'), expected.order_id, 'reference')
    same(unit.get('custom_id'), expected.order_id, 'custom id')
    amount = unit.get('amount') or {}
    expected.money(amount.get('value'), amount.get('currency_code'))
    return unit


async def check(expected, settings, metadata):
    suffix = '/' + path_id(expected.external_id)
    result = await _call(expected, settings, 'check', 'GET', suffix)
    unit = _verify(expected, result)
    if result.get('status') == 'APPROVED':
        await _call(expected, settings, 'capture', 'POST', suffix + '/capture', payload={})
        result = await _call(expected, settings, 'check', 'GET', suffix)
        unit = _verify(expected, result)
    captures = (unit.get('payments') or {}).get('captures') or []
    for capture in captures:
        if capture.get('status') != 'COMPLETED':
            continue
        amount = capture.get('amount') or {}
        expected.money(amount.get('value'), amount.get('currency_code'))
        if result.get('status') == 'COMPLETED' and capture.get('id'):
            return 'succeeded'
    return 'canceled' if result.get('status') == 'VOIDED' else 'pending'
