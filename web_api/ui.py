"""Presentation transport. Preview never executes an account's business action."""
from aiohttp import web

from core import web_ui
from web_api.auth import SESSION_KEY


async def settings(request):
    return web.json_response(web_ui.public_settings())


async def preview(request):
    return web.json_response(web_ui.preview_snapshot(request[SESSION_KEY]))


def add_routes(app):
    app.router.add_get('/api/v1/ui/settings', settings)
    app.router.add_get('/api/v1/admin/ui/preview', preview)
