"""Session-bound delivery of verified drafts into the opaque preview frame.

Only the authenticated parent opens HTML. A read-only resource handle lets the
opaque child load its package assets without receiving an editor session/CSRF
token. It grants no API operation; every load rechecks the originating session,
current administrator/key, admitted task and exact candidate. Restart revokes
all handles; reopening the authenticated preview obtains a new one.
"""
from __future__ import annotations

import asyncio
import mimetypes
import secrets
import threading
import time
from dataclasses import dataclass

from aiohttp import web

from bot.services.yadreno_admin_web_dialog import authorize
from core import auth
from core.results import CoreError
from database import requests as db
from web_api.auth import SESSION_COOKIE, SESSION_KEY
from web_tools.paths import local_path, source_provenance
from web_tools.publication import read_pointer
from web_tools import editor_publication
from web_tools.compatibility import current_capabilities
from web_tools.package import digest, verify_package
from web_tools.release import publication

PREFIX = '/ui/editor-preview/'


@dataclass(frozen=True)
class PreviewGrant:
    session_hash: str
    expires_at: int
    request_id: int
    task_id: str
    revision: str
    build_id: str
    package_sha256: str


class EditorPreviews:
    """At most one small handle per authenticated session; package bytes stay on disk."""

    def __init__(self, dialog):
        self.dialog = dialog
        self.handles = {}
        self._read_lock = threading.Lock()

    def _candidate(self, api_key, request_id=None):
        # Browsers request several chunks/fonts concurrently. Serialize these
        # bounded reads before the workspace's nonblocking interprocess lock.
        # The worker owns this lock even if its HTTP awaiter is cancelled.
        with self._read_lock:
            if request_id is None:
                request_id, binding = self.dialog._binding(api_key)
                if binding is None:
                    raise CoreError('action_unavailable', details={'reason': 'editor_candidate_unavailable'})
            else:
                binding = self.dialog._request_binding(request_id, api_key)
            workspace = binding.workspace(api_key)
            revision = workspace.inspect()['revision']
            actual = editor_publication.inspect(workspace)
            runtime = binding.project_root / 'web_runtime'
            folder = 'staging/'
            if actual['task_operation'] == 'rollback' and actual['task_is_current']:
                signed, content, trust = publication(runtime, actual['current_build_id'], current_capabilities())
                signed, files = verify_package(content, trust)
                manifest = signed['manifest']
                projection = {name: manifest[name] for name in ('build_id', 'base_build_id', 'content_hash', 'customization_version')}
                projection.update(revision=revision, package_sha256=digest(content), package_valid=True, activated=False)
                bundle = projection, signed, files, content
                folder = 'publications/'
            else:
                bundle = workspace.candidate_bundle(expected_revision=revision)
            viewed = dict(binding.runtime_context(api_key)['web_editor']['viewed'])
            proof = source_provenance(local_path(runtime, folder + bundle[0]['build_id']), bundle[1])
            if proof is None or proof['format_version'] != 2:
                raise ValueError('candidate preview inventory unavailable')
            pages = [{'id': page['id'], 'title': page.get('title')} for page in proof['inventory']['pages']]
            published = read_pointer(binding.project_root / 'web_runtime')['current'] == bundle[0]['build_id']
            return request_id, binding.task_id, viewed, bundle, pages, published

    async def open(self, session):
        api_key = authorize(session)
        try:
            request_id, task_id, viewed, bundle, pages, published = await asyncio.to_thread(self._candidate, api_key)
            candidate = bundle[0]
        except (ValueError, OSError):
            raise CoreError('conflict', details={'reason': 'editor_candidate_unavailable'}) from None
        # A logout/key/rights change during validation cannot issue a usable handle.
        current = db.get_account_session(session['token_hash'], int(time.time()))
        if current is None or authorize(current) != api_key:
            raise CoreError('access_denied')
        viewed.update(ui_version=candidate['build_id'], customization_version=candidate['customization_version'])
        grant = PreviewGrant(session['token_hash'], current['expires_at'], request_id,
                             task_id, candidate['revision'], candidate['build_id'], candidate['package_sha256'])
        for handle, previous in tuple(self.handles.items()):
            if previous == grant:
                break
            if previous.session_hash == grant.session_hash or previous.expires_at <= int(time.time()):
                del self.handles[handle]
        else:
            handle = secrets.token_urlsafe(32)
            self.handles[handle] = grant
        return {'task_id': task_id, 'candidate': candidate, 'viewed': viewed,
                'preview_url': PREFIX + handle + '/preview.html', 'pages': pages, 'published': published}

    async def asset(self, request):
        handle, name = request.match_info['handle'], request.match_info['name']
        try:
            grant = self.handles[handle]
            session = db.get_account_session(grant.session_hash, int(time.time()))
            if session is None:
                del self.handles[handle]
                raise ValueError('expired preview')
            api_key = authorize(session)
            if request.query or name in {'index.html', 'sw.js'}:
                raise ValueError('not a preview resource')
            if name.endswith('.html'):
                # A copied resource URL is not another administrator login.
                parent = auth.authenticate_session(request.cookies.get(SESSION_COOKIE))
                if parent['token_hash'] != grant.session_hash or name != 'preview.html':
                    raise ValueError('preview parent changed')
            _, task_id, _, bundle, _, _ = await asyncio.to_thread(self._candidate, api_key, grant.request_id)
            if task_id != grant.task_id:
                raise ValueError('preview task changed')
            candidate, _, files, _ = bundle
            if (candidate['revision'] != grant.revision or candidate['build_id'] != grant.build_id
                    or candidate['package_sha256'] != grant.package_sha256):
                raise ValueError('preview candidate changed')
            content = files[name]
            current = db.get_account_session(grant.session_hash, int(time.time()))
            if current is None or authorize(current) != api_key:
                raise ValueError('preview authority changed')
        except (KeyError, ValueError, OSError, CoreError):
            raise web.HTTPNotFound(headers={'Cache-Control': 'no-store'}) from None
        # Only the serving prefix changes; signed candidate bytes are never edited.
        # Relative chunk/stylesheet URLs keep working beneath the resource handle.
        if name.endswith(('.html', '.js', '.css')):
            content = content.replace(('/ui/versions/' + grant.build_id + '/').encode(),
                                      (PREFIX + handle + '/').encode())
        headers = {'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
                   'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                   'Content-Security-Policy': "default-src 'none'; sandbox",
                   'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': '*'}
        if name == 'preview.html':
            headers['Content-Security-Policy'] = (
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; "
                "worker-src 'none'; form-action 'none'; base-uri 'none'; sandbox allow-scripts; "
                "frame-ancestors 'self' https://web.telegram.org")
        return web.Response(body=content, headers=headers)


PREVIEWS_KEY = web.AppKey('web_editor_previews', EditorPreviews)


async def open_preview(request):
    return web.json_response(await request.app[PREVIEWS_KEY].open(request[SESSION_KEY]))


def add_routes(app, dialog):
    previews = app[PREVIEWS_KEY] = EditorPreviews(dialog)
    app.router.add_post('/api/v1/admin/ui/editor/preview', open_preview)
    app.router.add_get(PREFIX + '{handle}/{name:.*}', previews.asset)
