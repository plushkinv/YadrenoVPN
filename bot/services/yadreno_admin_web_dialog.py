"""Mini App control of the existing Satellite lane, without another task queue."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from bot.services import yadreno_admin as satellite
from bot.services import yadreno_admin_web_diagnostics as diagnostics
from bot.services.yadreno_admin_web_binding import WebEditorBinding, WEB_LANE_ACTOR, WEB_TOPIC_ID
from core.results import CoreError
from core.web_ui import require_administrator, require_preview_administrator
from database.connection import request_connection_scope
from web_tools.publication import read_pointer

def authorize(session):
    """The current session and configured installation key are never client fields."""
    require_preview_administrator(session)
    return authorize_administrator(session['telegram_id'])


def authorize_administrator(telegram_id):
    """Share installation authority without fabricating a Mini App session."""
    require_administrator(telegram_id)
    value = satellite.get_yadreno_admin_api_key()
    if not value:
        raise CoreError('action_unavailable', details={'reason': 'customizer_not_configured'})
    try:
        return satellite.normalize_yadreno_admin_api_key(value)
    except ValueError:
        raise CoreError('action_unavailable', details={'reason': 'customizer_not_configured'}) from None


def public_error(error):
    """Keep technical Hub refusals in diagnostics, separate from Web copy."""
    details = {'reason': error.kind}
    current = diagnostics.current()
    operation = current.operation if current is not None else 'turns'
    details['message'] = {
        'turns': 'Ошибка при отправке запроса', 'uploads': 'Ошибка при отправке запроса',
        'state': 'Ошибка при обновлении состояния запроса',
        'resume': 'Ошибка при возобновлении запроса', 'cancel': 'Ошибка при отмене запроса',
        'new-chat': 'Ошибка при создании нового чата',
    }.get(operation, 'Ошибка редактора')
    if error.cancel_button_text:
        details['cancel_button_text'] = error.cancel_button_text
    public = CoreError('action_unavailable', details=details,
                       retryable=error.kind in {'transport', 'service_unavailable', 'maintenance'})
    diagnostics.report(error, public)
    return public


class WebEditorDialog:
    """Hold HTTP-independent poll coroutines; admission/history remain Hub-owned."""

    def __init__(self):
        self._workers = set()

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
        return task

    async def close(self):
        # Stop local polling only. Accepted Hub request/binding remains recoverable.
        tasks = tuple(self._workers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _binding(self, api_key):
        request_id = (satellite.get_active_request_id(WEB_LANE_ACTOR, WEB_TOPIC_ID)
                      or satellite.get_last_request_id(WEB_LANE_ACTOR, WEB_TOPIC_ID))
        if request_id is None:
            return None, None
        return request_id, self._request_binding(request_id, api_key)

    @staticmethod
    def _request_binding(request_id, api_key):
        diagnostics.update(stage='read_binding', request_id=request_id)
        try:
            return WebEditorBinding.for_request(satellite.PROJECT_ROOT, request_id, api_key)
        except (ValueError, OSError) as error:
            raise CoreError('action_unavailable', details={'reason': 'editor_binding_unavailable'}) from error

    @diagnostics.observed('turns')
    async def start(self, session, message, viewed, *, uploads=None):
        diagnostics.update(operation='uploads' if uploads else 'turns', stage='authorization')
        diagnostics.redact(message)
        api_key = authorize(session)
        diagnostics.redact(api_key)
        diagnostics.update(stage='prepare_binding')
        uploads = list(uploads or [])
        if not isinstance(message, str) or (not message.strip() and not uploads) or len(message) > 8192:
            raise CoreError('invalid_request')
        previous = None
        if isinstance(viewed, dict) and viewed.get('ui_version') != read_pointer(satellite.PROJECT_ROOT / 'web_runtime')['current']:
            _, previous = self._binding(api_key)
        diagnostics.update(stage='prepare_binding')
        binding = WebEditorBinding.prepare(satellite.PROJECT_ROOT, session, viewed, api_key, previous=previous)
        diagnostics.update(task_id=binding.task_id, stage='prepare_request')
        accepted = asyncio.get_running_loop().create_future()
        # A disconnected HTTP waiter must not leave an unobserved future exception.
        accepted.add_done_callback(lambda future: None if future.cancelled() else future.exception())

        async def admitted():
            request_id = satellite.get_active_request_id(WEB_LANE_ACTOR, WEB_TOPIC_ID)
            diagnostics.update(stage='bind_request', request_id=request_id, admission='accepted')
            actual = WebEditorBinding.for_request(satellite.PROJECT_ROOT, request_id, api_key)
            if actual.task_id != binding.task_id:
                raise CoreError('conflict')
            accepted.set_result({'status': 'accepted', 'request_id': request_id, 'task_id': binding.task_id})
            diagnostics.update(stage='poll')

        async def run():
            with request_connection_scope():
                try:
                    options = {'topic_id': WEB_TOPIC_ID, 'web_binding': binding, 'accepted_callback': admitted}
                    if not uploads:
                        await satellite.run_dialog(WEB_LANE_ACTOR, api_key, message, **options)
                    else:
                        await satellite.run_dialog_with_uploads(WEB_LANE_ACTOR, api_key, message, uploads, **options)
                    if not accepted.done():
                        raise CoreError('temporarily_unavailable', details={'reason': 'admission_not_confirmed'})
                except satellite.YadrenoAdminRequestStopped:
                    if not accepted.done():
                        error = CoreError('temporarily_unavailable', details={'reason': 'admission_not_confirmed'})
                        diagnostics.report(error)
                        accepted.set_exception(error)
                except asyncio.CancelledError:
                    if not accepted.done():
                        error = CoreError('temporarily_unavailable', details={'reason': 'runtime_stopping'})
                        diagnostics.report(error)
                        accepted.set_exception(error)
                    raise
                except Exception as error:
                    public = (public_error(error) if isinstance(error, satellite.YadrenoAdminError)
                              else error if isinstance(error, CoreError)
                              else CoreError('temporarily_unavailable'))
                    diagnostics.report(error, public)
                    if not accepted.done():
                        accepted.set_exception(public)

        self._track(run())
        # Hub admission is never resubmitted because the browser disconnected.
        return await asyncio.shield(accepted)

    @diagnostics.observed('state')
    async def state(self, session):
        api_key = authorize(session)
        request_id, binding = self._binding(api_key)
        if request_id is None:
            return {'task': None, 'latest': None, 'local_polling': False}
        diagnostics.update(task_id=binding.task_id, request_id=request_id, admission='accepted', stage='state')
        try:
            latest = await satellite.fetch_latest_dialog_event(WEB_LANE_ACTOR, api_key, topic_id=WEB_TOPIC_ID)
        except satellite.YadrenoAdminError as error:
            raise public_error(error) from None
        # Recheck the requesting administrator before returning private content.
        if authorize(session) != api_key:
            raise CoreError('conflict', details={'reason': 'customizer_key_changed'})
        if latest is not None and latest.request_id != request_id:
            # A concurrent administrator may have started the next shared turn.
            # Never combine its content with an older view or expose an unbound ID.
            binding = self._request_binding(latest.request_id, api_key)
        return {'task': {'task_id': binding.task_id, 'viewed': binding.runtime_context(api_key)['web_editor']['viewed']},
                'latest': asdict(latest) if latest else None,
                'local_polling': satellite.is_local_request_active(WEB_LANE_ACTOR, WEB_TOPIC_ID)}

    @diagnostics.observed('resume')
    async def resume(self, session):
        api_key = authorize(session)
        request_id, binding = self._binding(api_key)
        if request_id is None:
            return {'resuming': False}
        diagnostics.update(task_id=binding.task_id, request_id=request_id, admission='accepted', stage='resume')

        async def run():
            with request_connection_scope():
                try:
                    await satellite.resume_active_dialog(WEB_LANE_ACTOR, api_key, topic_id=WEB_TOPIC_ID)
                except satellite.YadrenoAdminRequestStopped:
                    pass
                except Exception as error:
                    diagnostics.report(error)

        self._track(run())
        return {'resuming': True}

    @diagnostics.observed('cancel')
    async def cancel(self, session):
        api_key = authorize(session)
        request_id, binding = self._binding(api_key)
        if request_id is None:
            return {'status': 'idle', 'response_text': '', 'cancel_button_text': None,
                    'request_id': None, 'retry_after_sec': None}
        diagnostics.update(task_id=binding.task_id, request_id=request_id, admission='accepted', stage='cancel')
        try:
            # Cancel the verified request, never a browser-supplied or discovered ID.
            return asdict(await satellite._cancel_request(WEB_LANE_ACTOR, api_key,
                                                         topic_id=WEB_TOPIC_ID, request_id=request_id))
        except satellite.YadrenoAdminError as error:
            raise public_error(error) from None

    @diagnostics.observed('new-chat')
    async def new_chat(self, session):
        api_key = authorize(session)
        request_id = (satellite.get_active_request_id(WEB_LANE_ACTOR, WEB_TOPIC_ID)
                      or satellite.get_last_request_id(WEB_LANE_ACTOR, WEB_TOPIC_ID))
        diagnostics.update(stage='new_chat', request_id=request_id)
        try:
            result = await satellite.start_new_chat(WEB_LANE_ACTOR, api_key, topic_id=WEB_TOPIC_ID)
            if result.status != 'ok':
                diagnostics.failure_reason('hub_rejection')
                raise satellite.YadrenoAdminError('Hub did not open a new chat',
                    kind='maintenance' if result.status == 'maintenance' else 'hub_rejection')
            return asdict(result)
        except satellite.YadrenoAdminError as error:
            raise public_error(error) from None
