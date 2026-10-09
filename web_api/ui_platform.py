"""Trusted HTML delivery around an independently signed user application."""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from web_tools.application import read_application
from web_tools.package import digest
from web_tools.paths import canonical, local_path
from web_tools.platform_assets import current_platform, platform_resource


def descriptor(runtime, signed, files=None, *, prefix=None, mode=None, platform_version=None):
    manifest = signed['manifest']
    build = manifest['build_id']
    if files is None:
        folder = local_path(runtime, 'publications/' + build + '/files')
        content = local_path(folder, 'application.json').read_bytes()
        if digest(content) != manifest['files']['application.json']['sha256']:
            raise ValueError('application descriptor changed')
        files = {name: b'' for name in manifest['files']}
        files['application.json'] = content
    application = read_application(files)
    base = prefix or '/ui/versions/' + build + '/'
    url = lambda name: base + quote(name, safe='/')
    version = platform_version or current_platform(runtime)['version']
    if not re.fullmatch(r'[a-f0-9]{64}', version):
        raise ValueError('invalid system version')
    platform_resource(runtime, version, 'frame.html')
    return {'entry': url(application['entry']), 'styles': [url(name) for name in application['styles']],
            'pages': application['pages'], 'build_version': build, 'version': manifest['customization_version'],
            'instance_id': manifest['instance_id'], 'asset_base': base, 'platform_version': version,
            'frame_url': url('frame.html') + '?platform=' + version, **({'mode': mode} if mode else {})}


def html(runtime, application, *, frame=False):
    name = 'frame.html' if frame else 'index.html'
    template = platform_resource(runtime, application['platform_version'], name)
    # Non-executable JSON; no account or launch credentials are included in HTML.
    data = canonical(application).replace(b'<', b'\\u003c').replace(b'&', b'\\u0026')
    return template.replace(b'__APPLICATION_DESCRIPTOR__', data)


def html_headers(*, frame=False):
    policy = ("default-src 'none'; script-src 'self'" + ('' if frame else ' https://telegram.org https://oauth.telegram.org')
              + "; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src "
              + ("'none'; frame-src 'none'; worker-src 'none'; sandbox allow-scripts" if frame else "'self'; media-src blob:; frame-src 'self'; worker-src 'self'")
              + "; form-action 'none'; base-uri 'none'; frame-ancestors 'self' https://web.telegram.org")
    return {'Content-Type': 'text/html', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': policy}


async def system_asset(request):
    from web_api.ui_publications import RUNTIME_KEY
    version, name = request.match_info['version'], request.match_info['name']
    try:
        if not re.fullmatch(r'[a-f0-9]{64}', version) or name.endswith('.html'):
            raise ValueError('not a public system asset')
        content = platform_resource(request.app[RUNTIME_KEY], version, name)
    except (ValueError, OSError, KeyError, TypeError):
        raise web.HTTPNotFound() from None
    return web.Response(body=content, headers={'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
        'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})


async def service_worker(request):
    from web_api.ui_publications import RUNTIME_KEY, current_manifest
    runtime = request.app[RUNTIME_KEY]
    signed = current_manifest(runtime)
    manifest = signed['manifest']
    platform = current_platform(runtime)
    application = ['/ui/versions/' + manifest['build_id'] + '/' + quote(name, safe='/') for name in manifest['files']]
    system = ['/ui/platform/' + platform['version'] + '/' + quote(name, safe='/') for name in platform['files'] if not name.endswith('.html')]
    worker = Path(__file__).resolve().parent.parent / 'web_tools/service_worker.js'
    content = worker.read_text(encoding='utf-8').replace('__PLATFORM_CACHE__', json.dumps('yadreno.platform.' + manifest['instance_id'] + '.' + platform['version']))
    content = content.replace('__APPLICATION_CACHE__', json.dumps('yadreno.application.' + manifest['instance_id'] + '.' + manifest['build_id']))
    content = content.replace('__SHELL_CACHE__', json.dumps('yadreno.shell.' + manifest['instance_id'] + '.' + platform['version'] + '.' + manifest['build_id']))
    content = content.replace('__FRAME_URL__', json.dumps(descriptor(runtime, signed)['frame_url']))
    content = content.replace('__SYSTEM_ASSETS__', json.dumps(system)).replace('__APPLICATION_ASSETS__', json.dumps(application))
    return web.Response(text=content, content_type='application/javascript', headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})
