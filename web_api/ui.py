"""Presentation transport. Preview never executes an account's business action."""
from aiohttp import web

from core import web_ui
from web_api.auth import SESSION_KEY, _body


async def settings(request):
    return web.json_response(web_ui.public_settings())


async def preview(request):
    return web.json_response(web_ui.preview_snapshot(request[SESSION_KEY]))


async def preset(request):
    web_ui.require_preview_administrator(request[SESSION_KEY])
    body = await _body(request)
    return web.json_response(web_ui.set_presentation_setting('preset', body['preset']))


def add_routes(app):
    app.router.add_get('/api/v1/ui/settings', settings)
    app.router.add_get('/api/v1/admin/ui/preview', preview)
    app.router.add_post('/api/v1/admin/ui/preset', preset)
