"""Public unittest fixtures; usable only inside extension_tools.check."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys
import time
import traceback
import unittest


class EnvironmentUnavailable(RuntimeError):
    """A scenario prerequisite is absent; this is not an extension defect."""


class RegistrationFailed(RuntimeError):
    """The installed loader rejected a selected extension."""


@dataclass(frozen=True)
class Response:
    status: int
    body: object


@dataclass
class LocalHTTP:
    url: str
    requests: list


def diagnostic(error, *, source='unknown', stage=None, operation=None):
    frames = traceback.extract_tb(error.__traceback__)
    selected = [frame for frame in frames if frame.filename.startswith(
        ('/project/custom_extensions/', '/project/scenarios/'))]
    location = (selected or frames)[-1] if frames else None
    filename = getattr(error, 'filename', None) if isinstance(error, SyntaxError) else None
    line = getattr(error, 'lineno', None) if isinstance(error, SyntaxError) else None
    return {'source': source, 'stage': stage, 'operation': operation,
            'type': type(error).__name__, 'message': str(error)[:2000],
            'file': filename or (location.filename if location else None),
            'line': line or (location.lineno if location else None),
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))[-16000:]}


class _LoadLog(logging.Handler):
    def __init__(self, fixture):
        super().__init__()
        self.fixture = fixture

    def emit(self, record):
        if record.exc_info:
            error = record.exc_info[1]
            source = 'environment' if isinstance(error, (ImportError, EnvironmentUnavailable)) else 'extension'
            self.fixture.evidence['diagnostics'].append(diagnostic(error, source=source, stage='registration'))


class Fixture:
    """One case's real schema, accounts, registrations and HTTP server."""

    def __init__(self):
        from .guard import verify
        verify()
        self.evidence = {'stages': [], 'diagnostics': [], 'operations': [], 'http': [],
                         'registration': None, 'environment_gaps': []}
        self.servers, self.sessions, self.pending_settings = [], {}, []
        self.state = Path('/work/state') / ('case-' + str(__import__('os').getpid()))
        self.state.mkdir()
        self.stage = None
        self.loaded = False
        self.allowed_ports = set()
        self.monitor_network = True
        sys.addaudithook(self._network_event)

    def _network_event(self, event, args):
        # Observe real socket use, including errors handled by extension code.
        # The OS namespace enforces isolation; this observer never changes IO.
        if not self.monitor_network:
            return
        address = args[1] if event == 'socket.connect' else args[:2] if event == 'socket.getaddrinfo' else None
        if not isinstance(address, (tuple, list)) or len(address) < 2:
            return
        host, port = address[:2]
        if host in ('127.0.0.1', 'localhost', '::1') and port in self.allowed_ports:
            return
        gap = {'host': str(host), 'port': port, 'reason': 'No prepared local network service'}
        if gap not in self.evidence['environment_gaps']:
            self.evidence['environment_gaps'].append(gap)

    def mark(self, stage, started):
        self.evidence['stages'].append({'stage': stage, 'duration_seconds': time.monotonic() - started})
        Path('/work/files/progress.json').write_text(json.dumps(self.evidence, default=str), encoding='utf-8')

    def run_method(self, call, method):
        self.evidence['scenario_started'] = True
        started = time.monotonic()
        try:
            return call(method)
        finally:
            self.mark('scenario', started)

    async def prepare(self):
        from database import connection, requests as db
        from database.migrations import run_migrations
        from core import auth
        started = time.monotonic()
        self.stage = 'database'
        connection.DB_PATH = self.state / 'synthetic.db'
        run_migrations()
        self.mark('database', started)
        started = time.monotonic()
        self.stage = 'fixtures'
        owner, _ = db.get_or_create_user(91001, first_name='Synthetic owner')
        other, _ = db.get_or_create_user(91003, first_name='Synthetic other')
        site = db.create_account_credentials(phone='+12125550123', password_hash='synthetic-unused',
                                             now=int(time.time()), verified=False)
        self.actors = {'owner': owner['id'], 'other': other['id'], 'site_only': site['id']}
        self.sessions = {name: auth.create_session(uid, 'site',
            (db.get_account_credentials(uid) or {}).get('version', 0)) for name, uid in self.actors.items()}
        db.set_setting('web_public_origin', 'https://extension-check.invalid')
        self.mark('fixtures', started)

    async def load(self):
        from bot.utils import custom_extensions
        from core.extensions import registry
        from bot.utils.extension_settings import save_extension_setting
        started = time.monotonic()
        self.stage = 'registration'
        handler = _LoadLog(self)
        logger = logging.getLogger('bot.utils.custom_extensions')
        logger.addHandler(handler)
        try:
            result = custom_extensions.load_custom_extensions('/project/custom_extensions', enabled=True)
        finally:
            logger.removeHandler(handler)
        self.evidence['registration'] = {'loaded': result.loaded, 'failed': result.failed,
                                         'modules': registry.inspect_modules()}
        self.mark('registration', started)
        if result.failed:
            if any(item['source'] == 'environment' for item in self.evidence['diagnostics']):
                raise EnvironmentUnavailable(json.dumps(result.failed, ensure_ascii=False))
            raise RegistrationFailed(json.dumps(result.failed, ensure_ascii=False))
        expected = json.loads(Path('/project/check-input.json').read_text())['extensions']
        if len(result.loaded) != len(expected):
            raise EnvironmentUnavailable('Some selected files were not loaded: ' + ', '.join(expected))
        unavailable = [item['module_id'] for item in registry.inspect_modules() if item['state'] != 'available']
        if unavailable:
            raise EnvironmentUnavailable('Selected modules are unavailable; include their dependencies: ' + ', '.join(unavailable))
        for module, key, value in self.pending_settings:
            save_extension_setting(module, key, value)
        self.loaded = True
        # Observe the real execution once, including serializer failures swallowed
        # by the public adapter. Never repeat a handler or change its response.
        from core.extensions import operations
        self.original_execute = operations._execute

        async def observed(policy, context, parsed):
            try:
                return await self.original_execute(policy, context, parsed)
            except Exception as error:
                key = next((key for key, value in registry.POLICIES.items() if value is policy), None)
                operation = ({'module_id': key[1], 'name': key[2],
                              'kind': policy.get('operation_kind', 'command')} if key else None)
                self.evidence['diagnostics'].append(diagnostic(error, stage='operation',
                    operation=operation))
                raise

        operations._execute = observed
        self.stage = 'http_startup'
        started = time.monotonic()
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from web_api.app import SETTINGS_KEY, activation_middleware
        from web_api.auth import security_middleware
        from web_api.modules import add_routes
        from web_api.settings import WebSettings
        app = web.Application(middlewares=[activation_middleware, security_middleware], client_max_size=65536)
        app[SETTINGS_KEY] = WebSettings(True, 18764, 'https://extension-check.invalid')
        add_routes(app)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.allowed_ports.add(self.client.port)
        self.mark('http_startup', started)
        self.stage = 'scenario'

    async def close(self):
        self.monitor_network = False
        started = time.monotonic()
        if hasattr(self, 'client'):
            await self.client.close()
        for runner in reversed(self.servers):
            await runner.cleanup()
        if hasattr(self, 'original_execute'):
            from core.extensions import operations
            operations._execute = self.original_execute
        self.mark('cleanup', started)

    def set_settings(self, module_id, values):
        from database.requests import get_extension_storage
        from bot.utils.extension_settings import save_extension_setting
        for key, value in values.items():
            if self.loaded:
                save_extension_setting(module_id, key, value)
            else:
                get_extension_storage(module_id).set('settings.' + key, value)
                self.pending_settings.append((module_id, key, value))

    def storage(self, module_id):
        from database.requests import get_extension_storage
        return get_extension_storage(module_id)

    async def http_service(self, app):
        from aiohttp import web
        journal = []

        @web.middleware
        async def record(request, handler):
            body = await request.read()
            item = {'method': request.method, 'path': request.path_qs,
                    'headers': dict(request.headers), 'body': body.decode('utf-8', errors='replace')[:65536]}
            journal.append(item)
            try:
                response = await handler(request)
                item['status'] = response.status
                return response
            except BaseException as error:
                item['error'] = type(error).__name__
                raise

        app.middlewares.insert(0, record)
        runner = web.AppRunner(app)
        await runner.setup()
        self.servers.append(runner)
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = runner.addresses[0][1]
        self.allowed_ports.add(port)
        self.evidence['http'].append({'port': port, 'requests': journal})
        return LocalHTTP(f'http://127.0.0.1:{port}', journal)

    async def call(self, kind, module_id, operation, inputs, actor, idempotency_key=None):
        from web_api.auth import SESSION_COOKIE
        if actor not in self.sessions:
            raise ValueError('actor must be owner, other or site_only')
        session = self.sessions[actor]
        headers = {'Cookie': SESSION_COOKIE + '=' + session['token'],
                   'Origin': 'https://extension-check.invalid', 'X-CSRF-Token': session['csrf']}
        path = f'/api/v1/modules/{module_id}/' + ('queries/' if kind == 'query' else 'operations/') + operation
        operation_info = {'module_id': module_id, 'name': operation, 'kind': kind}
        started = time.monotonic()
        if kind == 'query':
            response = await self.client.get(path, params={'inputs': json.dumps(inputs)}, headers=headers)
        else:
            headers['Idempotency-Key'] = idempotency_key
            response = await self.client.post(path, json=inputs, headers=headers)
        body = await response.json()
        self.evidence['operations'].append({**operation_info, 'actor': actor,
            'status': response.status, 'body': body, 'duration_seconds': time.monotonic() - started})
        return Response(response.status, body)


class ExtensionTestCase(unittest.IsolatedAsyncioTestCase):
    """Override prepare_extension() for fixtures; write ordinary async test_* methods."""

    async def asyncSetUp(self):
        self.fixture = Fixture()
        self.addAsyncCleanup(self.fixture.close)
        await self.fixture.prepare()
        self.actors = self.fixture.actors
        started = time.monotonic()
        self.fixture.stage = 'prepare_extension'
        await self.prepare_extension()
        self.fixture.mark('prepare_extension', started)
        await self.fixture.load()

    async def prepare_extension(self):
        """Prepare synthetic data and local HTTP services before the real loader."""

    def _callTestMethod(self, method):
        return self.fixture.run_method(super()._callTestMethod, method)

    def set_settings(self, module_id, values):
        self.fixture.set_settings(module_id, values)

    def storage(self, module_id):
        return self.fixture.storage(module_id)

    async def http_service(self, app):
        return await self.fixture.http_service(app)

    async def query(self, module_id, operation, inputs=None, *, actor='owner'):
        return await self.fixture.call('query', module_id, operation, {} if inputs is None else inputs, actor)

    async def command(self, module_id, operation, inputs=None, *, actor='owner', idempotency_key):
        return await self.fixture.call('command', module_id, operation, {} if inputs is None else inputs, actor, idempotency_key)

    def require_environment(self, condition, reason):
        if not condition:
            raise EnvironmentUnavailable(reason)
