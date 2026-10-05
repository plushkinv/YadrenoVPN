"""Private viewed-context binding for the existing shared Satellite request.

Authenticated customizer entrypoints create a binding. The Hub request owns
execution/history; this file stores source coordinates, never an agent queue,
credentials, an alternate conversation, or authority supplied by a model.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from core.results import CoreError
from core.web_ui import require_administrator, require_preview_administrator
from core.web_ui_context import capture_editor_context, capture_view_context, _validate_viewed_context
from web_tools.editor_files import checked_directory, directory_handle, read_regular, write_regular
from web_tools.editor_workspace import EditorWorkspace
from web_tools.paths import canonical, local_path, publication_lock, source_provenance
from web_tools.publication import read_pointer

WEB_TOPIC_ID = 1004
WEB_LANE_ACTOR = 0
_MAX_BINDING_BYTES = 128 * 1024


def _key_hash(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise CoreError('access_denied')
    return hashlib.sha256(api_key.strip().encode('utf-8')).hexdigest()


def _request_name(request_id: int) -> str:
    if type(request_id) is not int or not 0 < request_id < 2**63:
        raise CoreError('invalid_request')
    return f'requests/{request_id}.json'


class WebEditorBinding:
    """Trusted local task coordinates; every read rechecks the creating admin."""

    def __init__(self, project_root: Path, task_id: str):
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise CoreError('invalid_request')
        self.project_root = checked_directory(project_root)
        self.task_id = task_id
        self.store = local_path(self.project_root, 'web_runtime/editor_tasks')
        self.task_root = local_path(self.store, task_id)

    @classmethod
    def prepare(cls, project_root: Path, session: dict, viewed: dict, api_key: str,
                *, previous: WebEditorBinding | None = None) -> WebEditorBinding:
        """Bind a viewed publication/candidate; sources are shared, never forked."""
        require_preview_administrator(session)
        from web_tools.editor_publication import recover
        recover(project_root)
        candidate_source = None
        try:
            context = capture_view_context(session, viewed, root=project_root)
        except CoreError as error:
            if previous is None or error.details.get('reason') != 'ui_publication_changed':
                raise
            viewed = _validate_viewed_context(viewed)
            workspace = previous.workspace(api_key)
            candidate, signed, _, _ = workspace.candidate_bundle()
            candidate_source = workspace
            if (candidate['build_id'] != viewed['ui_version']
                    or candidate['customization_version'] != viewed['customization_version']):
                raise CoreError('conflict', details={'reason': 'ui_publication_changed'})
            proof = source_provenance(local_path(project_root / 'web_runtime', 'staging/' + candidate['build_id']), signed)
            if proof is None:
                raise CoreError('conflict', details={'reason': 'ui_source_unavailable'})
            page = next((item for item in proof['inventory']['pages'] if item['id'] == viewed['route'].split('/')[0]), None)
            if page is None or viewed['scenario'] not in proof['inventory']['scenarios']:
                raise CoreError('invalid_request')
            context = capture_editor_context(session['telegram_id'], root=project_root)
            context['viewed'] = viewed
            context['effective_source'] = {'page': page, 'components': proof['inventory']['components'],
                                           'styles': proof['inventory']['styles']}
        binding = cls.defer(project_root, session['telegram_id'], api_key)
        value = binding.authority(api_key)
        value['view_context'] = context
        value.pop('previous_request_id', None)
        write_regular(binding.task_root, 'binding.json', canonical(value))
        if candidate_source is not None:
            write_regular(binding.task_root / 'editor', 'candidate.json',
                          read_regular(candidate_source._folder, 'candidate.json'))
        return binding

    @classmethod
    def defer(cls, project_root: Path, telegram_id: int, api_key: str,
              *, previous_request_id: int | None = None) -> WebEditorBinding:
        """Back up sources before any tool can execute, including generic file tools."""
        require_administrator(telegram_id)
        binding = cls(project_root, uuid.uuid4().hex)
        with directory_handle(binding.project_root, 'web_runtime/editor_tasks/' + binding.task_id, create=True):
            pass
        EditorWorkspace.create(binding.project_root, binding.task_root)
        write_regular(binding.task_root, 'binding.json', canonical({
            'format_version': 1, 'task_id': binding.task_id, 'telegram_id': telegram_id,
            'api_key_hash': _key_hash(api_key), 'view_context': None,
            'previous_request_id': None,
        }))
        if previous_request_id is not None:
            from web_tools.errors import WebSourceError
            try:
                prior = cls.for_request(project_root, previous_request_id, api_key).workspace(api_key)
                prior.candidate_bundle()
                write_regular(binding.task_root / 'editor', 'candidate.json', read_regular(prior._folder, 'candidate.json'))
            except (CoreError, WebSourceError, FileNotFoundError):
                pass  # New requests always keep the shared files, even without a reusable candidate.
        return binding

    def _materialize(self, api_key: str) -> None:
        with publication_lock(self.task_root):
            value = self.authority(api_key)
            if value['view_context'] is None:
                value['view_context'] = capture_editor_context(value['telegram_id'], root=self.project_root)
                value.pop('previous_request_id', None)
                write_regular(self.task_root, 'binding.json', canonical(value))

    def authority(self, api_key: str) -> dict:
        """Recheck the private admission identity without acquiring a workspace lock."""
        checked_directory(self.store, private=True)
        checked_directory(self.task_root, private=True)
        value = json.loads(read_regular(self.task_root, 'binding.json', maximum=_MAX_BINDING_BYTES))
        if (not isinstance(value, dict) or set(value) - {'previous_request_id'} != {
                'format_version', 'task_id', 'telegram_id', 'api_key_hash', 'view_context'}
                or type(value['format_version']) is not int or value['format_version'] != 1
                or value['task_id'] != self.task_id or type(value['telegram_id']) is not int
                or value['telegram_id'] <= 0 or value['api_key_hash'] != _key_hash(api_key)
                or not (isinstance(value['view_context'], dict) or value['view_context'] is None
                        and 'previous_request_id' in value)):
            raise CoreError('access_denied')
        if value.get('previous_request_id') is not None:
            _request_name(value['previous_request_id'])
        require_administrator(value['telegram_id'])
        return value

    def _read(self, api_key: str) -> dict:
        return self.authority(api_key)

    def runtime_context(self, api_key: str) -> dict:
        """Compact request facts; the addressed KB owns the workflow."""
        value = self._read(api_key)
        workspace = EditorWorkspace.open(self.project_root, self.task_root)
        state = workspace._state()
        return {'web_editor': {'task_id': self.task_id,
                'working_directory': str(workspace._custom), 'backup': state['backup'],
                **copy.deepcopy(value['view_context'] or {'viewed': None})}}

    def workspace(self, api_key: str) -> EditorWorkspace:
        self._materialize(api_key)
        self._read(api_key)
        return EditorWorkspace.open(self.project_root, self.task_root)

    def bind_request(self, request_id: int, api_key: str) -> None:
        """Attach an accepted Hub request once; repeating the same binding is safe."""
        self._read(api_key)
        name = _request_name(request_id)
        payload = canonical({'task_id': self.task_id, 'api_key_hash': _key_hash(api_key)})
        with publication_lock(self.store):
            try:
                previous = read_regular(self.store, name, maximum=4096)
            except FileNotFoundError:
                write_regular(self.store, name, payload)
            else:
                if previous != payload:
                    raise CoreError('conflict', details={'reason': 'editor_request_changed'})

    @classmethod
    def for_request(cls, project_root: Path, request_id: int, api_key: str) -> WebEditorBinding:
        """Recover only locally admitted coordinates under the same installation key."""
        project = checked_directory(project_root)
        store = checked_directory(local_path(project, 'web_runtime/editor_tasks'), private=True)
        value = json.loads(read_regular(store, _request_name(request_id), maximum=4096))
        if (not isinstance(value, dict) or set(value) != {'task_id', 'api_key_hash'}
                or value['api_key_hash'] != _key_hash(api_key)):
            raise CoreError('access_denied')
        binding = cls(project, value['task_id'])
        binding._read(api_key)
        return binding
