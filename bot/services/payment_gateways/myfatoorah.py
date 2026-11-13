"""MyFatoorah v3 hosted payment and invoice lookup in the account currency."""
from bot.services.payment_api import PaymentApiResponseError
from core.payment_provider_specs import MYFATOORAH_ENDPOINTS
from .common import idempotency_key, invoice, path_id, request, same


def _data(result):
    if result.get('IsSuccess') not in (True, 'true') or not isinstance(result.get('Data'), dict):
        raise PaymentApiResponseError('MyFatoorah request was not successful')
    return result['Data']


async def create(expected, settings, description, return_url):
    headers = {'Authorization': 'Bearer ' + settings['api_token']}
    idempotent = settings['region'] in {'KW', 'SA'}
    if idempotent:
        headers['Idempotency-Key'] = idempotency_key('myfatoorah', 'create', expected.creation_key or expected.order_id)
    result = await request('myfatoorah', 'create', expected.order_id, 'POST',
        MYFATOORAH_ENDPOINTS[settings['region']] + '/v3/payments', retry=idempotent,
        headers=headers, payload={
            'Order': {'Amount': expected.amount, 'Currency': expected.currency,
                      'ExternalIdentifier': expected.order_id},
            'OperationType': 'PAY', 'NotificationOption': 'LINK',
            'IntegrationUrls': {'Redirection': return_url},
        })
    data = _data(result)
    return invoice(data.get('InvoiceId'), data.get('PaymentURL'), region=settings['region'])


async def check(expected, settings, metadata):
    result = await request('myfatoorah', 'check', expected.order_id, 'GET',
        MYFATOORAH_ENDPOINTS[settings['region']] + '/v3/invoices/' + path_id(expected.external_id),
        retry=True, headers={'Authorization': 'Bearer ' + settings['api_token']})
    # The documented invoice endpoint may return this until the first transaction.
    if result.get('IsSuccess') in (False, 'false') and str(result.get('Message') or '').strip() == 'No invoices match this InvoiceId':
        return 'pending'
    data = _data(result)
    details = data.get('Invoice') or {}
    same(details.get('Id'), expected.external_id, 'invoice id')
    same(details.get('ExternalIdentifier'), expected.order_id, 'order id')
    amount = data.get('Amount') or {}
    expected.money(amount.get('ValueInBaseCurrency'), amount.get('BaseCurrency'))
    transactions = data.get('Transactions') or []
    if not isinstance(transactions, list):
        raise PaymentApiResponseError('Invalid MyFatoorah transactions')
    if details.get('Status') == 'PAID' and any(
        item.get('Status') == 'SUCCESS' and item.get('PaymentId') and item.get('Id')
        for item in transactions if isinstance(item, dict)
    ):
        return 'succeeded'
    return 'canceled' if details.get('Status') in {'CANCELED', 'CANCELLED', 'EXPIRED'} else 'pending'
