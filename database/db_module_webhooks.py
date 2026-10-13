"""Atomically accept verified webhook events and queue their durable references."""
import json
import time
from uuid import uuid4

from core.results import CoreError
from .connection import get_db

__all__ = ['bind_module_webhook_reference', 'accept_module_webhook', 'get_module_webhook']


def bind_module_webhook_reference(module_id, name, reference, user_id):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT user_id FROM module_webhook_bindings WHERE module_id=? AND name=? AND reference=?',
                           (module_id, name, reference)).fetchone()
        if old and old['user_id'] != user_id:
            raise CoreError('webhook_reference_conflict')
        conn.execute('INSERT OR IGNORE INTO module_webhook_bindings(module_id,name,reference,user_id) VALUES(?,?,?,?)',
                     (module_id, name, reference, user_id))


def accept_module_webhook(module_id, name, version, handler_name, event_id, fingerprint, payload, reference, *, enabled):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM module_webhooks WHERE module_id=? AND name=? AND event_id=?',
                           (module_id, name, event_id)).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            return {'accepted': True, 'event_id': old['id'], 'duplicate': True}
        if not enabled:
            raise CoreError('module_unavailable', retryable=True)
        user_id = telegram_id = None
        if reference is not None:
            owner = conn.execute('SELECT u.id,u.telegram_id FROM module_webhook_bindings b JOIN users u ON u.id=b.user_id '
                'WHERE b.module_id=? AND b.name=? AND b.reference=?', (module_id, name, reference)).fetchone()
            if owner is None:
                raise CoreError('webhook_reference_not_found')
            user_id, telegram_id = owner['id'], owner['telegram_id']
        event_key, task_id = 'event_' + uuid4().hex, 'task_' + uuid4().hex
        conn.execute('INSERT INTO module_webhooks(id,module_id,name,version,event_id,fingerprint,payload_json,task_id,created_at) '
            'VALUES(?,?,?,?,?,?,?,?,?)', (event_key, module_id, name, version, event_id, fingerprint,
            json.dumps(payload, ensure_ascii=False, allow_nan=False), task_id, int(time.time())))
        conn.execute('INSERT INTO extension_scheduled_tasks(task_id,extension_id,handler_name,user_id,telegram_id,payload,'
            'created_at,run_at,next_attempt_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',
            (task_id, module_id, handler_name, user_id, telegram_id, json.dumps({'webhook_id': event_key})))
        return {'accepted': True, 'event_id': event_key, 'duplicate': False}


def get_module_webhook(module_id, event_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM module_webhooks WHERE module_id=? AND id=?', (module_id, event_id)).fetchone()
        return dict(row) if row else None

