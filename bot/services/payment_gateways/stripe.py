"""Stripe Checkout sessions with fixed USD quotes and idempotent creation."""
from decimal import Decimal
from .common import idempotency_key, invoice, path_id, request, same

API = 'https://api.stripe.com/v1/checkout/sessions'


def _headers(settings):
    return {'Authorization': 'Bearer ' + settings['secret_key'], 'Stripe-Version': '2026-10-28.endive'}


async def create(expected, settings, description, return_url):
    same(expected.currency, 'USD', 'currency')
    headers = _headers(settings)
    headers['Idempotency-Key'] = idempotency_key('stripe', 'create', expected.creation_key or expected.order_id)
    result = await request('stripe', 'create', expected.order_id, 'POST', API, retry=True, headers=headers, data={
        'mode': 'payment', 'success_url': return_url, 'cancel_url': return_url,
        'client_reference_id': expected.order_id, 'metadata[order_id]': expected.order_id,
        'line_items[0][price_data][currency]': 'usd',
        'line_items[0][price_data][unit_amount]': str(int(expected.amount * 100)),
        'line_items[0][price_data][product_data][name]': description[:255],
        'line_items[0][quantity]': '1', 'adaptive_pricing[enabled]': 'false',
    })
    return invoice(result.get('id'), result.get('url'))


async def check(expected, settings, metadata):
    result = await request('stripe', 'check', expected.order_id, 'GET', API + '/' + path_id(expected.external_id),
                           retry=True, headers=_headers(settings))
    same(result.get('id'), expected.external_id, 'session id')
    same(result.get('client_reference_id'), expected.order_id, 'order id')
    same((result.get('metadata') or {}).get('order_id'), expected.order_id, 'metadata order id')
    same(result.get('mode'), 'payment', 'session mode')
    expected.money(Decimal(result['amount_total']) / 100 if type(result.get('amount_total')) is int else None,
                   result.get('currency'))
    if result.get('payment_status') == 'paid':
        return 'succeeded'
    return 'canceled' if result.get('status') == 'expired' else 'pending'
