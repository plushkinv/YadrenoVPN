"""Authenticated import routes; URLs are identifiers, never fetch destinations."""
from aiohttp import web

from core.auth import session_context
from core.results import CoreError
from core.subscription_import import import_subscription
from database import requests as db
from web_api.auth import SESSION_KEY, _body, _text


async def create(request):
    body = await _body(request)
    result = await import_subscription(session_context(request[SESSION_KEY]),
                                       _text(body, 'url', max_length=2048))
    return web.json_response(result)


async def status(request):
    value = request.match_info['id']
    if not value.isascii() or not value.isdecimal() or len(value) > 18:
        raise CoreError('invalid_request')
    result = db.get_subscription_import(int(value), request[SESSION_KEY]['user_id'])
    if result is None:
        raise CoreError('subscription_import_not_found')
    return web.json_response(result)


def add_routes(app):
    app.router.add_post('/api/v1/subscription-imports', create)
    app.router.add_get('/api/v1/subscription-imports/{id}', status)
