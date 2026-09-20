"""Account HTTP routes delegate ownership and business behavior to shared services."""
from aiohttp import web

from core import catalog, composition, devices, profile, promotions, subscriptions, trials
from core.auth import public_auth_settings
from core.key_operations import mutate_key
from core.results import CoreError
from database import requests as db
from web_api.auth import _body, _text
from web_api.payments import account


def _id(request, name='id'):
    value = request.match_info[name]
    if not value.isascii() or not value.isdecimal() or len(value) > 16 or int(value) <= 0:
        raise CoreError('invalid_request', details={'field': name})
    return int(value)


def _paging(request):
    try:
        if set(request.query) - {'limit', 'offset'}:
            raise ValueError()
        return subscriptions.pagination(int(request.query.get('limit', 50)), int(request.query.get('offset', 0)))
    except (TypeError, ValueError):
        raise CoreError('invalid_request') from None


async def bootstrap(request):
    from core.payment_offers import balance_spending_enabled
    from web_api.auth import SESSION_KEY
    offers = (await trials.list_offers(account(request)) if SESSION_KEY in request else
              [db.get_account_trial_eligibility(None, offer['offer_id'])
               for offer in db.get_all_trial_offers() if offer['is_enabled']])
    trial_available = any(offer['eligible'] for offer in offers)
    return web.json_response({'api_version': 1, 'auth': public_auth_settings(),
            'features': {'subscriptions': True, 'trial': trial_available, 'promotions': db.has_available_promo_codes(),
                         'referrals': db.is_referral_enabled(), 'balance': balance_spending_enabled(),
                         'subscription_import': True, 'support_chat': False}})


async def me(request):
    return web.json_response(profile.profile(account(request)))


async def tariff_catalog(request):
    if set(request.query) - {'subscription_id'}:
        raise CoreError('invalid_request')
    raw = request.query.get('subscription_id')
    if raw is not None and (not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**52):
        raise CoreError('invalid_request')
    return web.json_response(await catalog.catalog(account(request), key_id=int(raw) if raw else None))


async def trial_offers(request):
    return web.json_response({'offers': await trials.list_offers(account(request))})


async def activate_trial(request):
    if await _body(request):
        raise CoreError('invalid_request')
    return web.json_response(await trials.activate(account(request), _id(request), request.headers.get('Idempotency-Key')))


async def subscription_list(request):
    return web.json_response(await subscriptions.list_subscriptions(account(request), **_paging(request)))


async def subscription(request):
    return web.json_response(await subscriptions.summary(account(request), _id(request)))


async def subscription_history(request):
    return web.json_response(subscriptions.history(account(request), _id(request), **_paging(request)))


async def access(request):
    return web.json_response(await subscriptions.access(account(request), _id(request)))


async def key_mutation(request):
    body = await _body(request)
    if 'key_id' in body:
        raise CoreError('invalid_request')
    result = await mutate_key(account(request), request.match_info['action'], {'key_id': _id(request), **body},
                              request.headers.get('Idempotency-Key'))
    return web.json_response(result)


async def operation(request):
    return web.json_response(subscriptions.operation(account(request), request.match_info['id']))


async def device_list(request):
    return web.json_response({'devices': await devices.list_devices(account(request), _id(request))})


async def delete_device(request):
    if await _body(request):
        raise CoreError('invalid_request')
    return web.json_response(await devices.delete_device(account(request), _id(request), request.match_info['device'],
                                                         request.headers.get('Idempotency-Key')))


async def hosts(request):
    return web.json_response({'hosts': composition.candidates(account(request), _id(request))})


async def bind_host(request):
    body = await _body(request)
    if set(body) != {'host_id'}:
        raise CoreError('invalid_request')
    return web.json_response(await composition.bind(account(request), _id(request), body['host_id'],
                                                    request.headers.get('Idempotency-Key')))


async def promo(request):
    body = await _body(request)
    action = request.match_info['action']
    if action == 'check':
        if set(body) != {'code'}:
            raise CoreError('invalid_request')
        result = promotions.check(account(request), _text(body, 'code', max_length=128))
    else:
        result = promotions.mutate(account(request), action, body, request.headers.get('Idempotency-Key'))
    return web.json_response(result)


async def balance(request):
    return web.json_response(profile.balance(account(request), **_paging(request)))


async def payments(request):
    return web.json_response(profile.payments(account(request), **_paging(request)))


async def referrals(request):
    return web.json_response(profile.referrals(account(request)))


async def attribute_referral(request):
    body = await _body(request)
    if set(body) != {'code'}:
        raise CoreError('invalid_request')
    return web.json_response(profile.attribute_referral(account(request), _text(body, 'code', max_length=128)))


async def help_page(request):
    from core.help import help_content
    return web.json_response(help_content())


async def topup(request):
    body = await _body(request)
    if set(body) != {'quote_id'}:
        raise CoreError('invalid_request')
    actor = account(request)
    quote_id = _text(body, 'quote_id', max_length=128)
    offer = db.get_payment_offer(quote_id, actor.account_id)
    if not offer or offer['payload']['purpose'] != 'balance_topup':
        raise CoreError('quote_not_found')
    from core.payment_offers import create_order
    from core.payments import order_status
    return web.json_response(order_status(actor, await create_order(actor, quote_id, request.headers.get('Idempotency-Key'))))


def add_routes(app):
    for path, handler in (('bootstrap', bootstrap), ('me', me), ('catalog', tariff_catalog), ('trial-offers', trial_offers),
            ('subscriptions', subscription_list), ('subscriptions/{id}', subscription), ('subscriptions/{id}/access', access),
            ('subscriptions/{id}/history', subscription_history),
            ('subscriptions/{id}/devices', device_list), ('subscriptions/{id}/host-candidates', hosts),
            ('key-operations/{id}', operation), ('balance', balance), ('payments', payments), ('referrals', referrals), ('help', help_page)):
        app.router.add_get('/api/v1/' + path, handler)
    for path, handler in (('trials/{id}/activate', activate_trial),
            ('subscriptions/{id}/{action:configure|replace|rename|delete}', key_mutation),
            ('subscriptions/{id}/devices/{device}/delete', delete_device), ('subscriptions/{id}/host', bind_host),
            ('promotions/{action:check|activate|clear}', promo), ('balance/topups', topup),
            ('referrals/attribute', attribute_referral)):
        app.router.add_post('/api/v1/' + path, handler)
