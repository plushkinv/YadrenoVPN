"""Public immutable UI assets and manifests; no arbitrary file or build endpoint."""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path

from aiohttp import web

from core.results import CoreError
from web_tools.paths import PROJECT_ROOT, canonical, local_path
from web_tools.package import digest, verify_manifest
from web_tools.publication import manifest_path, read_pointer

RUNTIME_KEY = web.AppKey('ui_runtime', Path)


def current_manifest(runtime):
    try:
        current = read_pointer(runtime)['current']
        if current is None:
            raise FileNotFoundError()
        signed = json.loads(manifest_path(runtime, current).read_bytes())
        verify_manifest(signed, json.loads(local_path(runtime, 'identity.json').read_bytes()))
        return signed
    except (OSError, ValueError, KeyError, TypeError):
        raise CoreError('ui_package_unavailable', retryable=True) from None


async def manifest(request):
    signed = current_manifest(request.app[RUNTIME_KEY])
    etag = '"' + digest(canonical(signed)) + '"'
    if request.headers.get('If-None-Match') == etag:
        return web.Response(status=304, headers={'ETag': etag})
    return web.json_response(signed, headers={'ETag': etag})


async def package(request):
    content_hash = request.match_info['content_hash']
    if not re.fullmatch(r'[a-f0-9]{64}', content_hash):
        raise CoreError('ui_package_not_found')
    path = local_path(request.app[RUNTIME_KEY], 'packages/' + content_hash + '.zip')
    if not path.is_file():
        raise CoreError('ui_package_not_found')
    return web.FileResponse(path, headers={'Content-Type': 'application/zip', 'ETag': '"' + content_hash + '"'})


async def bot_avatar(request):
    from core.bot_profile import avatar
    content_hash = request.match_info['content_hash']
    body = avatar(content_hash)
    if body is None:
        raise web.HTTPNotFound()
    headers = {'ETag': '"' + content_hash + '"', 'Cache-Control': 'no-cache',
               'Access-Control-Allow-Origin': '*', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer'}
    if request.headers.get('If-None-Match') == headers['ETag']:
        return web.Response(status=304, headers=headers)
    return web.Response(body=body, content_type='image/jpeg', headers=headers)


async def asset(request):
    runtime = request.app[RUNTIME_KEY]
    build_id = request.match_info.get('build_id')
    name = request.match_info.get('name')
    try:
        if build_id:
            signed = json.loads(manifest_path(runtime, build_id).read_bytes())
        else:
            signed = current_manifest(runtime)
            build_id = signed['manifest']['build_id']
            if request.path.startswith('/ui/assets/'):
                name = 'assets/' + name
        name = name or ('sw.js' if request.path == '/sw.js' else 'index.html')
        if name not in signed['manifest']['files']:
            raise ValueError('unknown asset')
        path = local_path(runtime, 'publications/' + build_id + '/files/' + name)
        if not path.is_file():
            raise ValueError('missing asset')
    except (OSError, ValueError, KeyError, TypeError, CoreError):
        raise web.HTTPNotFound() from None
    preview = name == 'preview.html'
    headers = {'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
        'Cache-Control': 'no-cache' if name.endswith('.html') or name == 'sw.js' else 'public, max-age=31536000, immutable',
        'Access-Control-Allow-Origin': '*'}
    if name.endswith('.html'):
        headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'self'" + ('' if preview else ' https://telegram.org')
            + "; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src "
            + ("'none'" if preview else "'self'; media-src blob:") + "; frame-src 'self'; worker-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
            + ('' if preview else ' https://web.telegram.org'))
    if name == 'sw.js':
        headers['Service-Worker-Allowed'] = '/'
    return web.FileResponse(path, headers=headers)


def add_routes(app, runtime=None):
    app[RUNTIME_KEY] = Path(runtime) if runtime is not None else PROJECT_ROOT / 'web_runtime'
    app.router.add_get('/api/v1/ui/manifest', manifest)
    app.router.add_get('/api/v1/ui/packages/{content_hash}', package)
    app.router.add_get('/', asset)
    app.router.add_get('/orders/{order_id}', asset)
    app.router.add_get('/sw.js', asset)
    app.router.add_get('/ui/assets/{name:.*}', asset)
    app.router.add_get('/ui/bot-avatar/{content_hash}.jpg', bot_avatar)
    app.router.add_get('/ui/versions/{build_id}/{name:.*}', asset)
