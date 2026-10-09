"""Loopback-only preview of a validated draft, with isolated synthetic read models."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from web_tools.paths import canonical, local_path
from web_tools.package import verify_package


def preview(stage, trust, port=5176, *, runtime):
    from web_api.ui_platform import descriptor, html as render_html
    from web_tools.platform_assets import platform_resource
    signed, files = verify_package(local_path(stage, 'package.zip').read_bytes(), trust)
    manifest = signed['manifest']
    base = '/ui/versions/' + manifest['build_id'] + '/'
    application = descriptor(runtime, signed, files, mode='preview')
    frame = render_html(runtime, application, frame=True)
    system_base = '/ui/platform/' + application['platform_version'] + '/'
    # No installation credentials, accounts or network mutations are available.
    message = {'type': 'yadreno.preview', 'installation': {
        'settings': {'title': 'Локальный просмотр', 'logo': None, 'preset': 'clear', 'theme': 'light', 'sync_interval_seconds': 300},
        'captured_at': 0, 'currency': 'RUB', 'features': {'subscriptions': True, 'subscription_import': True}, 'tariffs': [], 'trial_offers': [], 'modules': []},
        'context': {'contract_version': 1, 'route': 'home', 'scenario': 'active', 'preset': 'clear', 'theme': 'light',
            'ui_version': manifest['build_id'], 'customization_version': manifest['customization_version']}}
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Локальный просмотр UI</title>'
        '<p>Локальный черновик · синтетические данные · действия недоступны</p>'
        '<iframe sandbox="allow-scripts" style="width:100%;height:90vh;border:0" src="' + base + 'preview.html"></iframe>'
        '<script>const frame=document.querySelector("iframe");addEventListener("message",event=>{'
        'if(event.source===frame.contentWindow&&event.data?.type==="yadreno.preview.ready")'
        'frame.contentWindow.postMessage(' + canonical(message).decode() + ',"*");});</script></html>').encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == '/':
                content, mime = html, 'text/html'
            elif path == base + 'preview.html':
                content, mime = frame, 'text/html'
            elif path.startswith(system_base) and not path.endswith('.html'):
                try:
                    name = path[len(system_base):]
                    content, mime = platform_resource(runtime, application['platform_version'], name), mimetypes.guess_type(name)[0] or 'application/octet-stream'
                except (ValueError, KeyError, OSError):
                    self.send_error(404); return
            elif path.startswith(base) and path[len(base):] in files:
                name = path[len(base):]
                content, mime = files[name], mimetypes.guess_type(name)[0] or 'application/octet-stream'
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
            self.send_header('Cache-Control', 'no-store'); self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Referrer-Policy', 'no-referrer'); self.send_header('X-Content-Type-Options', 'nosniff')
            if path != '/':
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'")
            self.send_header('Content-Length', str(len(content))); self.end_headers(); self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(json.dumps({'preview_url': f'http://127.0.0.1:{port}', 'build_id': manifest['build_id']}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
