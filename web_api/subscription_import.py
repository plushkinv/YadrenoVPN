"""Authenticated import routes; URLs are identifiers, never fetch destinations."""
from aiohttp import web

from core.auth import session_context
from core.subscription_import import import_subscription
from web_api.auth import SESSION_KEY, _body, _text


async def create(request):
    body = await _body(request)
    result = await import_subscription(session_context(request[SESSION_KEY]),
                                       _text(body, 'url', max_length=2048), body.get('group_id'))
    return web.json_response(result)


def add_routes(app):
    app.router.add_post('/api/v1/subscription-imports', create)
