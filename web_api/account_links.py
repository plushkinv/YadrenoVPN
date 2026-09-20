"""Account-link adapters; trusted sessions are supplied by the API middleware."""
from aiohttp import web

from core import account_links
from web_api.auth import SESSION_KEY, _body, _text


async def start(request):
    return web.json_response(account_links.start_link(request[SESSION_KEY]))


async def status(request):
    body = await _body(request)
    return web.json_response(account_links.link_status(request[SESSION_KEY], _text(body, 'token', max_length=64)))


async def finish(request):
    body = await _body(request)
    return web.json_response(account_links.finish_link(
        request[SESSION_KEY], _text(body, 'token', max_length=64), body.get('telegram_id')))


def add_routes(app):
    # Tokens are posted in bodies, not exposed to access-log query strings.
    app.router.add_post('/api/v1/account/telegram/link', start)
    app.router.add_post('/api/v1/account/telegram/link/status', status)
    app.router.add_post('/api/v1/account/telegram/link/finish', finish)
