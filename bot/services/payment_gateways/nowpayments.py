"""NOWPayments hosted USD invoices, resolved through outgoing authenticated API calls."""
from decimal import Decimal, InvalidOperation

from bot.services.payment_api import PaymentApiResponseError
from .common import (HttpError, access_token, invoice, path_id, request, same)

API = 'https://api.nowpayments.io/v1'


async def create(expected, settings, description, return_url):
    same(expected.currency, 'USD', 'currency')
    result = await request('nowpayments', 'create', expected.order_id, 'POST', API + '/invoice',
        headers={'x-api-key': settings['api_key']}, payload={
            'price_amount': expected.amount, 'price_currency': 'usd',
            'order_id': expected.order_id, 'order_description': description[:255],
            'success_url': return_url, 'cancel_url': return_url,
        })
    return invoice(result.get('id'), result.get('invoice_url'))


async def _token(settings, order_id, *, rejected=None):
    async def load():
        result = await request('nowpayments', 'auth', order_id, 'POST', API + '/auth', retry=True,
                               payload={'email': settings['email'], 'password': settings['password']})
        return result.get('token'), 300
    return await access_token('nowpayments', settings, load, rejected=rejected)


async def _list(settings, expected, page):
    token = await _token(settings, expected.order_id)
    for attempt in range(2):
        try:
            return await request('nowpayments', 'list', expected.order_id, 'GET', API + '/payment/',
                retry=True, headers={'x-api-key': settings['api_key'], 'Authorization': 'Bearer ' + token},
                params={'invoiceId': expected.external_id, 'limit': 100, 'page': page,
                        'sortBy': 'created_at', 'orderBy': 'asc'})
        except HttpError as exc:
            if exc.status != 401 or attempt:
                raise
            token = await _token(settings, expected.order_id, rejected=token)


def _fully_paid(payment):
    try:
        paid, due = (Decimal(str(payment.get(name))) for name in ('actually_paid', 'pay_amount'))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaymentApiResponseError('Missing NOWPayments paid amount') from exc
    return paid.is_finite() and due.is_finite() and due > 0 and paid >= due


async def check(expected, settings, metadata):
    page = 0
    seen = set()
    while True:
        result = await _list(settings, expected, page)
        payments = result.get('data')
        if not isinstance(payments, list):
            raise PaymentApiResponseError('Invalid NOWPayments payment list')
        for item in payments:
            # API versions differ in the invoice filter spelling. Never trust the filter alone.
            if not isinstance(item, dict) or str(item.get('invoice_id') or '') != expected.external_id:
                continue
            payment_id = str(item.get('payment_id') or '')
            if payment_id in seen:
                continue
            seen.add(payment_id)
            payment = await request('nowpayments', 'check', expected.order_id, 'GET',
                API + '/payment/' + path_id(payment_id), retry=True,
                headers={'x-api-key': settings['api_key']})
            same(payment.get('payment_id'), payment_id, 'payment id')
            same(payment.get('invoice_id'), expected.external_id, 'invoice id')
            same(payment.get('order_id'), expected.order_id, 'order id')
            expected.money(payment.get('price_amount'), payment.get('price_currency'))
            if payment.get('payment_status') == 'finished' and _fully_paid(payment):
                return 'succeeded'
        try:
            pages = int(result['pagesCount'])
            current = int(result['page'])
        except (ValueError, TypeError, KeyError) as exc:
            raise PaymentApiResponseError('Invalid NOWPayments pagination') from exc
        if current != page or pages < 0:
            raise PaymentApiResponseError('Inconsistent NOWPayments pagination')
        page += 1
        if page >= pages:
            # A failed/expired coin attempt does not expire the hosted invoice;
            # the buyer may have selected a different coin or another attempt.
            return 'pending'
