"""Thin HTTP transport for common offer and order operations."""
from aiohttp import web

from core.auth import session_context
from core.payment_offers import create_offer, create_order
from core import payments
from core.results import CoreError
from web_api.auth import SESSION_KEY, _body, _text


def account(request):
    return session_context(request[SESSION_KEY])


async def quote(request):
    return web.json_response(await create_offer(account(request), await _body(request)))


async def create(request):
    body = await _body(request)
    if set(body) != {'quote_id'}:
        raise CoreError('invalid_request')
    owner = account(request)
    order_id = await create_order(owner, _text(body, 'quote_id', max_length=128), request.headers.get('Idempotency-Key'))
    return web.json_response(payments.order_status(owner, order_id))


async def status(request):
    return web.json_response(payments.order_status(account(request), request.match_info['id']))


async def method(request):
    body = await _body(request)
    if set(body) != {'payment_type'}:
        raise CoreError('invalid_request')
    return web.json_response(await payments.select_method(account(request), request.match_info['id'],
                                                          _text(body, 'payment_type', max_length=128)))


async def check(request):
    if await _body(request):
        raise CoreError('invalid_request')
    return web.json_response(await payments.check_order(account(request), request.match_info['id']))


async def cancel(request):
    if await _body(request):
        raise CoreError('invalid_request')
    return web.json_response(await payments.cancel_order(account(request), request.match_info['id']))


def add_routes(app):
    app.router.add_post('/api/v1/quotes', quote)
    app.router.add_post('/api/v1/orders', create)
    app.router.add_get('/api/v1/orders/{id}', status)
    app.router.add_post('/api/v1/orders/{id}/method', method)
    app.router.add_post('/api/v1/orders/{id}/check', check)
    app.router.add_post('/api/v1/orders/{id}/cancel', cancel)
