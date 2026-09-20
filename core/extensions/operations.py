"""Authenticated, declared module calls with persistent operation identities."""
import asyncio
import hashlib
import inspect
import json
import re
from dataclasses import replace

from core.accounts import require_account
from core.context import bind_account_context
from core.results import CoreError
from core.schemas import validate_value
from database import requests as db
from . import registry

_locks: dict[tuple, asyncio.Lock] = {}


async def invoke_user_operation(account, module_id, name, inputs, idempotency_key):
    from runtime.readiness import require_active
    require_active()
    require_account(account)
    if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency_key):
        raise CoreError('invalid_request', details={'field': 'idempotency_key'})
    if not all(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) for value in (module_id, name)):
        raise CoreError('operation_not_found')
    kind = 'module.' + module_id + '.' + name
    try:
        encoded = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise CoreError('invalid_request') from None
    if len(encoded.encode()) > 65536:
        raise CoreError('invalid_request')
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    lock_key = account.account_id, kind, idempotency_key
    lock = _locks.setdefault(lock_key, asyncio.Lock())
    try:
        async with lock:
            previous = db.get_module_operation(account.account_id, kind, idempotency_key)
            if previous and previous['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            if previous and previous['completed']:
                return {'operation_id': previous['id'], 'result': previous['result']}
            module = registry.MODULES.get(module_id)
            declaration = registry.POLICIES.get(('user_operation', module_id, name))
            if not module or not declaration:
                raise CoreError('module_unavailable', retryable=True)
            state = registry.module_state(module_id, settlement=previous is not None)
            if state != 'available' or (previous and previous['request']['version'] != module['version']):
                raise CoreError('module_unavailable', retryable=True)
            try:
                validate_value(inputs, declaration['input_schema'])
            except ValueError as exc:
                raise CoreError('invalid_request', details={'field': str(exc)}) from None
            operation = previous or db.begin_module_operation(account.account_id, kind, idempotency_key, fingerprint,
                                                              {'version': module['version'], 'source': account.source, 'inputs': inputs})
            actor = replace(account, source=operation['request']['source'], operation_id=operation['id'])
            try:
                with bind_account_context(actor):
                    context = registry.context_for({}, phase='execute', inputs=operation['request']['inputs'])
                    result = declaration['handler'](context)
                    if inspect.isawaitable(result):
                        result = await asyncio.wait_for(result, 12)
                    validate_value(result, declaration['result_schema'])
                db.finish_module_operation(account.account_id, operation['id'], result)
                return {'operation_id': operation['id'], 'result': result}
            except Exception:
                raise CoreError('module_operation_pending', retryable=True, operation_id=operation['id']) from None
    finally:
        # Queued calls retain the same lock; a new lock is safe after the last waiter.
        if not lock.locked() and not getattr(lock, '_waiters', None):
            _locks.pop(lock_key, None)
