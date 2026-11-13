"""PayTabs hosted Sale; the provider owns the result page (no POST return URL)."""
from core.payment_provider_specs import PAYTABS_ENDPOINTS
from .common import invoice, request, same


async def create(expected, settings, description, return_url):
    same(expected.currency, 'USD', 'currency')
    result = await request('paytabs', 'create', expected.order_id, 'POST',
        PAYTABS_ENDPOINTS[settings['region']] + '/payment/request',
        headers={'Authorization': settings['server_key']}, payload={
            'profile_id': int(settings['profile_id']), 'tran_type': 'sale', 'tran_class': 'ecom',
            'cart_id': expected.order_id, 'cart_description': description[:128],
            'cart_currency': expected.currency, 'cart_amount': expected.amount,
        })
    same(result.get('cart_id'), expected.order_id, 'cart id')
    expected.money(result.get('cart_amount'), result.get('cart_currency'))
    return invoice(result.get('tran_ref'), result.get('redirect_url'),
                   region=settings['region'], profile_id=settings['profile_id'])


async def check(expected, settings, metadata):
    result = await request('paytabs', 'check', expected.order_id, 'POST',
        PAYTABS_ENDPOINTS[settings['region']] + '/payment/query', retry=True,
        headers={'Authorization': settings['server_key']},
        payload={'profile_id': int(settings['profile_id']), 'tran_ref': expected.external_id})
    same(result.get('tran_ref'), expected.external_id, 'transaction id')
    same(result.get('cart_id'), expected.order_id, 'cart id')
    expected.money(result.get('cart_amount'), result.get('cart_currency'))
    same(str(result.get('tran_type') or '').casefold(), 'sale', 'transaction type')
    status = (result.get('payment_result') or {}).get('response_status')
    if status == 'A':
        return 'succeeded'
    return 'canceled' if status in {'D', 'E', 'V'} else 'pending'
