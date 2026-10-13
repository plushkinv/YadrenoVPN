"""Own-account support surface for installed modules; no administrative methods."""
from core.accounts import require_account
from core.content import safe_html
from core.operations import operation_fingerprint, positive_id
from core.results import CoreError
from core.subscriptions import pagination
from database import requests as db

def _authorize(account, module_id, *, settlement=False):
    require_account(account)
    from core.extensions.registry import module_state
    if module_state(module_id, settlement=settlement) != 'available':
        raise CoreError('module_unavailable', retryable=True)


def _thread(row):
    return {name: row.get(name) for name in ('id', 'status', 'channel', 'created_at', 'updated_at', 'last_message_at', 'message_count')}


def list_threads(account, module_id, *, limit=20, offset=0):
    _authorize(account, module_id)
    pagination(limit, offset)
    result = db.list_support_ticket_sessions(user_id=account.account_id, limit=limit, offset=offset)
    return {'items': [_thread(row) for row in result['items']], 'total': result['total'], 'limit': limit, 'offset': offset}


def history(account, module_id, thread_id, *, limit=50, before_message_id=None):
    _authorize(account, module_id)
    positive_id(thread_id, 'thread_id')
    pagination(limit, 0)
    if before_message_id is not None:
        positive_id(before_message_id, 'before_message_id')
    thread = db.get_support_thread(thread_id)
    if not thread or thread['user_id'] != account.account_id:
        raise CoreError('support_not_found')
    result = db.get_support_ticket_history(thread_id, limit=limit, before_message_id=before_message_id)
    messages = result['messages'][:limit]
    return {'thread': _thread(result['thread']), 'messages': [
        {'id': row['id'], 'sender': row['sender_type'], 'content_html': safe_html(row['text_html']),
         'created_at': row['created_at'], 'attachment': (
            {'type': row['media_type'], 'url': f"/api/v1/modules/{module_id}/support/attachments/{row['id']}"}
            if row['media_file_id'] or row.get('uploaded_file_id') else None)} for row in messages],
        'next_before_message_id': messages[-1]['id'] if len(result['messages']) > limit else None}


async def write_message(account, module_id, text, idempotency_key, *, thread_id=None, file_id=None):
    from runtime.readiness import require_active
    require_active()
    require_account(account)
    if not isinstance(text, str) or (not text.strip() and file_id is None) or len(text) > 4000 or '\x00' in text:
        raise CoreError('invalid_request', details={'field': 'text'})
    if thread_id is not None:
        positive_id(thread_id, 'thread_id')
    inputs = {'thread_id': thread_id, 'text': text}
    if file_id is not None:
        if not isinstance(file_id, str):
            raise CoreError('invalid_request', details={'field': 'file_id'})
        inputs['file_id'] = file_id
    fingerprint = operation_fingerprint(idempotency_key, inputs)
    kind = 'support.' + module_id + ('.reply' if thread_id is not None else '.create')
    previous = db.get_module_operation(account.account_id, kind, idempotency_key)
    _authorize(account, module_id, settlement=previous is not None)
    if file_id is not None and previous is None:
        from core.uploads import record, read_record
        uploaded = record(account, module_id, file_id)
        if not uploaded['telegram_file_id']:
            read_record(uploaded)
    from bot.utils.text import escape_html
    result = db.write_account_support_once(account.account_id, module_id, thread_id, escape_html(text),
                                           idempotency_key, fingerprint, account.source, file_id=file_id)
    # The durable message is independent of the optional Telegram notification.
    from runtime.delivery import dispatch
    async def notify(bot):
        from bot.services.support import send_generated_user_message_to_admins
        await send_generated_user_message_to_admins(
            bot, thread=db.get_support_thread(result['thread_id']), user=db.get_user_by_id(account.account_id),
            support_message_id=result['message_id'], text_html=escape_html(text), _respect_assignment=True)
    dispatch(notify)
    return result


def attachment(account, module_id, message_id):
    _authorize(account, module_id)
    positive_id(message_id, 'message_id')
    row = db.get_account_support_attachment(account.account_id, message_id)
    if row is None:
        raise CoreError('support_not_found')
    return row
