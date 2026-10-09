"""Request-scoped, secret-free diagnostics for the Mini App editor only."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from core.results import CoreError

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_current: ContextVar[EditorDiagnostic | None] = ContextVar('web_editor_diagnostic', default=None)
_ROUTES = {'': 'state', '/turns': 'turns', '/uploads': 'uploads', '/resume': 'resume',
           '/cancel': 'cancel', '/new-chat': 'new-chat', '/preview': 'preview', '/apply': 'apply'}
_STAGES = {'capabilities': 'capabilities', 'process': 'admission', 'upload': 'admission',
           'upload_batch': 'admission', 'poll': 'poll', 'status': 'status', 'latest': 'latest',
           'tool_result': 'tool_result', 'new_chat': 'new_chat', 'cancel': 'cancel'}


def route_operation(path):
    prefix = '/api/v1/admin/ui/editor'
    return _ROUTES.get(path[len(prefix):]) if path.startswith(prefix) else None


@dataclass
class EditorDiagnostic:
    operation: str
    topic: int = 1004
    diagnostic_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    stage: str = 'authorization'
    task_id: str | None = None
    request_id: int | None = None
    admission: str = 'not_attempted'
    method: str | None = None
    endpoint: str | None = None
    origin: str | None = None
    attempt: int | None = None
    hub_http_status: int | None = None
    hub_status: str | None = None
    hub_error_code: str | None = None
    reason: str | None = None
    description: str | None = None
    reported: bool = False
    redactions: list[str] = field(default_factory=list, repr=False)

    def text(self, value):
        """Select one bounded diagnostic field, never an arbitrary response dump."""
        if not isinstance(value, str):
            return None
        for secret in sorted(self.redactions, key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        value = re.sub(r'https?://\S+', '[url]', value, flags=re.I)
        value = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|cookie|password|init_data|initData)\s*[:=]\s*)\S+',
                       r'\1[redacted]', value)
        value = re.sub(r'[A-Za-z0-9_+/=-]{32,}', '[redacted]', value)
        return ''.join(char if char >= ' ' and char != '\x7f' else ' ' for char in value)[:1024]

    def code(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', value):
            return None
        return self.text(value)


@contextmanager
def scope(operation):
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    diagnostic = EditorDiagnostic(operation)
    token = _current.set(diagnostic)
    try:
        yield diagnostic
    finally:
        _current.reset(token)


def current():
    return _current.get()


def observed(operation):
    """Cover direct service callers as well as the authenticated HTTP adapter."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with scope(operation):
                try:
                    return await function(*args, **kwargs)
                except Exception as error:
                    report(error)
                    raise
        return wrapped
    return decorate


def update(**values):
    diagnostic = current()
    if diagnostic is not None:
        for name, value in values.items():
            setattr(diagnostic, name, value)


def redact(*values):
    diagnostic = current()
    if diagnostic is not None:
        for value in values:
            if isinstance(value, str) and value and value not in diagnostic.redactions:
                diagnostic.redactions.append(value)


def request_started(method, path, origin, attempt, api_key):
    diagnostic = current()
    if diagnostic is None:
        return
    redact(api_key)
    endpoint = urlsplit(path).path
    stage = _STAGES.get(endpoint.rsplit('/', 1)[-1], 'hub_request')
    update(stage=stage, method=method, endpoint=endpoint, origin=urlsplit(origin).netloc.rsplit('@', 1)[-1],
           attempt=attempt, hub_http_status=None, hub_status=None, hub_error_code=None,
           reason=None, description=None)
    if stage == 'admission':
        diagnostic.admission = 'unknown'


def response_received(status, data=None):
    diagnostic = current()
    if diagnostic is None:
        return
    diagnostic.hub_http_status = status
    if diagnostic.stage == 'admission' and status in {401, 403, 404, 413, 422}:
        diagnostic.admission = 'rejected'
    if isinstance(data, str) and len(data) <= 65536:
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            data = None
    if not isinstance(data, dict):
        return
    diagnostic.hub_status = diagnostic.code(data.get('status'))
    diagnostic.hub_error_code = diagnostic.code(data.get('error_code'))
    hub_status = data.get('status')
    if isinstance(hub_status, str) and hub_status not in {'accepted', 'available', 'ok'}:
        # Hub prose can echo a task or an ASR transcript unavailable to this client.
        # Machine fields are authoritative; arbitrary response_text is not log material.
        diagnostic.description = 'Hub reported a non-success status; see hub_status and hub_error_code.'
    if diagnostic.stage == 'admission' and status < 400:
        if data.get('status') == 'accepted':
            diagnostic.admission = 'accepted'
            request_id = data.get('request_id')
            if type(request_id) is int and request_id > 0:
                diagnostic.request_id = request_id
        elif isinstance(data.get('status'), str) and data['status']:
            diagnostic.admission = 'rejected'


def failure_reason(reason, description=None):
    diagnostic = current()
    if diagnostic is not None:
        diagnostic.reason = reason
        if description is not None:
            diagnostic.description = diagnostic.text(description)


def _causes(error):
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def _trace(error):
    """Keep locations and exception classes, excluding source lines and locals."""
    frames, tb = [], error.__traceback__
    while tb is not None and len(frames) < 64:
        code = tb.tb_frame.f_code
        path = Path(code.co_filename)
        try:
            filename = path.relative_to(_ROOT).as_posix()
        except ValueError:
            filename = path.name
        frames.append({'file': filename, 'function': code.co_name, 'line': tb.tb_lineno})
        tb = tb.tb_next
    return frames


def report_recoverable(error):
    """Record a local tool failure without hiding a later failure of the worker."""
    diagnostic = current()
    if diagnostic is None:
        return None
    occurrence = replace(diagnostic, diagnostic_id=uuid.uuid4().hex[:16], reported=False,
                         reason=None, description=None)
    token = _current.set(occurrence)
    try:
        return report(error)
    finally:
        _current.reset(token)


def report(error, public_error=None):
    """Log once at the owning boundary and attach the same ID to its HTTP error."""
    diagnostic = current()
    if diagnostic is None:
        return None
    public = public_error if public_error is not None else error
    if isinstance(public, CoreError):
        existing = public.details.get('diagnostic_id')
        if isinstance(existing, str) and re.fullmatch('[a-f0-9]{16}', existing) and not diagnostic.reported:
            diagnostic.diagnostic_id = existing
        public.details.setdefault('diagnostic_id', diagnostic.diagnostic_id)
        if diagnostic.admission == 'unknown':
            public.details['outcome_unknown'] = True
    if diagnostic.reported:
        return diagnostic.diagnostic_id
    diagnostic.reported = True
    causes = list(_causes(error))
    category = next((getattr(cause, 'kind') for cause in causes if hasattr(cause, 'kind')), None)
    category = category or ('validation' if isinstance(public, CoreError)
                           and public.code not in {'internal_error', 'temporarily_unavailable'} else 'internal')
    internal = category in {'internal', 'local', 'protocol'} or (
        isinstance(error, CoreError) and any(not isinstance(cause, CoreError) for cause in causes))
    core_reason = error.details.get('reason') if isinstance(error, CoreError) else None
    record = {name: getattr(diagnostic, name) for name in (
        'diagnostic_id', 'operation', 'stage', 'topic', 'task_id', 'request_id', 'admission',
        'method', 'endpoint', 'origin', 'attempt', 'hub_http_status', 'hub_status', 'hub_error_code')}
    record.update(category=category, reason=diagnostic.reason or diagnostic.code(core_reason)
                  or diagnostic.code(getattr(error, 'code', None))
                  or (public.code if isinstance(public, CoreError) else 'exception'),
                  description=diagnostic.description, exception=type(error).__name__,
                  causes=[{'type': type(cause).__name__,
                           'errno': cause.errno if isinstance(cause, OSError) and type(cause.errno) is int else None,
                           'traceback': _trace(cause) if internal else []} for cause in causes])
    # The formatter prints message text, so fields must not exist only in LogRecord.extra.
    level = logging.WARNING if category in {'validation', 'hub_rejection', 'maintenance', 'configuration'} else logging.ERROR
    logger.log(level, 'web_editor_failed %s', json.dumps(record, ensure_ascii=False, separators=(',', ':')))
    return diagnostic.diagnostic_id
