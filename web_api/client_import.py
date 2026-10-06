"""Permanent app-handoff page, independent of the cabinet's UI publication."""
from pathlib import Path

from aiohttp import web

from core.client_import import resolve_import
from core.results import CoreError
from web_api.app import SETTINGS_KEY
from web_api.auth import _body

_ASSETS = {
    '/open-client': ('index.html', 'text/html'),
    '/open-client/app.js': ('app.js', 'text/javascript'),
    '/open-client/style.css': ('style.css', 'text/css'),
}


async def page(request):
    filename, content_type = _ASSETS[request.path]
    return web.Response(body=(Path(__file__).with_name('client_import_assets') / filename).read_bytes(),
        content_type=content_type, charset='utf-8', headers={
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        })


async def resolve(request):
    body = await _body(request)
    try:
        result = resolve_import(body['token'], body['client'], request.app[SETTINGS_KEY].public_origin)
    except ValueError:
        raise CoreError('client_import_invalid') from None
    return web.json_response(result)


def add_routes(app):
    for path in _ASSETS:
        app.router.add_get(path, page)
    app.router.add_post('/api/v1/client-import/resolve', resolve)
