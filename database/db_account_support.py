"""Own-account commands over existing support threads, messages and receipts."""
import json
import secrets
import time

from core.results import CoreError
from .connection import get_db
from .db_support import record_support_message, SupportThreadClosedError

__all__ = ['write_account_support_once', 'get_account_support_attachment', 'create_account_support_thread']


def create_account_support_thread(user_id, *, admin_id):
    """Create an administrator-initiated thread for an internal account."""
    with get_db() as conn:
        user = conn.execute('SELECT telegram_id FROM users WHERE id=?', (user_id,)).fetchone()
        if user is None:
            return None
        cursor = conn.execute(
            'INSERT INTO support_threads(user_id,user_telegram_id,initiator_type,initiator_admin_id,'
            'assigned_admin_id,channel) VALUES(?,?,\'admin\',?,?,?)',
            (user_id, user['telegram_id'], admin_id, admin_id, 'telegram' if user['telegram_id'] is not None else 'web'))
        return dict(conn.execute('SELECT * FROM support_threads WHERE id=?', (cursor.lastrowid,)).fetchone())


def write_account_support_once(user_id, module_id, thread_id, text_html, idempotency_key, fingerprint, source, *, file_id=None):
    kind = 'support.' + module_id + ('.reply' if thread_id is not None else '.create')
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        owner = conn.execute('SELECT telegram_id,is_banned FROM users WHERE id=?', (user_id,)).fetchone()
        if owner is None or owner['is_banned']:
            raise CoreError('access_denied')
        previous = conn.execute('SELECT * FROM account_operations WHERE user_id=? AND kind=? AND idempotency_key=?',
                                (user_id, kind, idempotency_key)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            return {'operation_id': previous['id'], **json.loads(previous['result_json'])}
        uploaded = None
        if file_id is not None:
            uploaded = conn.execute('SELECT * FROM module_uploads WHERE id=? AND user_id=? AND module_id=?',
                                    (file_id, user_id, module_id)).fetchone()
            if uploaded is None:
                raise CoreError('file_not_found')
        if thread_id is None:
            cursor = conn.execute("INSERT INTO support_threads(user_id,user_telegram_id,initiator_type,channel) "
                                  "VALUES(?,?,'user','web')", (user_id, owner['telegram_id']))
            thread_id = int(cursor.lastrowid)
        thread = conn.execute('SELECT * FROM support_threads WHERE id=? AND user_id=?', (thread_id, user_id)).fetchone()
        if thread is None:
            raise CoreError('support_not_found')
        operation_id = secrets.token_urlsafe(24)
        try:
            message_id = record_support_message(thread_id, sender_type='user', sender_telegram_id=owner['telegram_id'],
                recipient_telegram_id=None, text_html=text_html, media_type='document' if uploaded else 'text',
                media_file_id=uploaded['telegram_file_id'] if uploaded else None,
                source_chat_id=None, source_message_id=None, origin_type='extension',
                origin_extension_id=module_id, origin_operation_key='support:' + operation_id, _conn=conn)
        except SupportThreadClosedError:
            raise CoreError('support_closed') from None
        if uploaded:
            conn.execute('UPDATE support_messages SET uploaded_file_id=? WHERE id=?', (file_id, message_id))
        result = {'thread_id': thread_id, 'message_id': message_id}
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,result_json,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?)', (operation_id, user_id, kind, idempotency_key, fingerprint,
                     json.dumps({'source': source}), json.dumps(result), int(time.time())))
        return {'operation_id': operation_id, **result}


def get_account_support_attachment(user_id, message_id):
    with get_db() as conn:
        row = conn.execute('SELECT m.media_type,m.media_file_id,m.uploaded_file_id FROM support_messages m JOIN support_threads t '
                           'ON t.id=m.thread_id WHERE m.id=? AND t.user_id=?', (message_id, user_id)).fetchone()
        return dict(row) if row and (row['media_file_id'] or row['uploaded_file_id']) else None
