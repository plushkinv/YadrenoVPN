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
        """Pin the viewed publication before admission; navigation cannot retarget it."""
        from web_tools.editor_publication import recover
        recover(project_root)
        seed, revision, fork_source_revision = None, None, None
        try:
            context = capture_view_context(session, viewed, root=project_root)
        except CoreError as error:
            if previous is None or error.details.get('reason') != 'ui_publication_changed':
                raise
            require_preview_administrator(session)
            viewed = _validate_viewed_context(viewed)
            context = previous.runtime_context(api_key)['web_editor']
            context.pop('task_id')
            seed = previous.workspace(api_key)
            revision = seed.inspect()['revision']
            candidate, signed, _, _ = seed.candidate_bundle(expected_revision=revision)
            if (viewed['ui_version'] != candidate['build_id']
                    or viewed['customization_version'] != candidate['customization_version']):
                raise CoreError('conflict', details={'reason': 'ui_publication_changed'})
            if read_pointer(project_root / 'web_runtime')['current'] != context['publication']['build_id']:
                from web_tools.editor_publication import inspect
                from web_tools.compatibility import current_capabilities
                from web_tools.release import publication
                actual = inspect(seed)
                if actual['task_operation'] != 'publish' or not actual['task_is_current']:
                    raise CoreError('conflict', details={'reason': 'ui_publication_changed'})
                runtime = project_root / 'web_runtime'
                current, _, _ = publication(runtime, actual['current_build_id'], current_capabilities())
                live_proof = source_provenance(local_path(runtime, 'publications/' + actual['current_build_id']), current)
                if live_proof is None or live_proof['format_version'] != 2:
                    raise CoreError('conflict', details={'reason': 'ui_source_unavailable'})
                # Capture the exact publication made by this parent task. The
                # viewed draft may contain a page absent from that publication.
                context = capture_view_context(session, {
                    **viewed, 'ui_version': actual['current_build_id'],
                    'customization_version': current['manifest']['customization_version'],
                    'route': live_proof['inventory']['pages'][0]['id'],
                    'scenario': live_proof['inventory']['scenarios'][0],
                }, root=project_root)
                fork_source_revision = context['source_revision']
            proof = source_provenance(local_path(project_root / 'web_runtime', 'staging/' + candidate['build_id']), signed)
            if proof is None or proof['format_version'] != 2:
                raise CoreError('conflict', details={'reason': 'ui_source_unavailable'})
            inventory = proof['inventory']
            page = next((item for item in inventory['pages'] if item['id'] == viewed['route'].split('/')[0]), None)
            if page is None or viewed['scenario'] not in inventory['scenarios']:
                raise CoreError('invalid_request')
            context['viewed'] = viewed
            context['effective_source'] = {'page': page, 'components': inventory['components'], 'styles': inventory['styles']}
        key_hash = _key_hash(api_key)
        binding = cls(project_root, uuid.uuid4().hex)
        with directory_handle(binding.project_root, 'web_runtime/editor_tasks/' + binding.task_id, create=True):
            pass
        checked_directory(binding.store, private=True)
        checked_directory(binding.task_root, private=True)
        if seed is None:
            EditorWorkspace.create(binding.project_root, binding.task_root,
                                   expected_source_revision=context['source_revision'])
        else:
            seed.fork(binding.task_root, expected_revision=revision, source_revision=fork_source_revision)
        data = canonical({'format_version': 1, 'task_id': binding.task_id,
                          'telegram_id': session['telegram_id'], 'api_key_hash': key_hash,
                          'view_context': context})
        if len(data) > _MAX_BINDING_BYTES:
            raise CoreError('invalid_request')
        write_regular(binding.task_root, 'binding.json', data)
        return binding

    @classmethod
    def defer(cls, project_root: Path, telegram_id: int, api_key: str,
              *, previous_request_id: int | None = None) -> WebEditorBinding:
        """Remember a trusted Telegram turn without touching UI sources or compiler."""
        require_administrator(telegram_id)
        if previous_request_id is not None:
            _request_name(previous_request_id)
        binding = cls(project_root, uuid.uuid4().hex)
        with directory_handle(binding.project_root, 'web_runtime/editor_tasks/' + binding.task_id, create=True):
            pass
        checked_directory(binding.store, private=True)
        checked_directory(binding.task_root, private=True)
        write_regular(binding.task_root, 'binding.json', canonical({
            'format_version': 1, 'task_id': binding.task_id, 'telegram_id': telegram_id,
            'api_key_hash': _key_hash(api_key), 'view_context': None,
            'previous_request_id': previous_request_id,
        }))
        return binding

    def _materialize(self, api_key: str) -> None:
        """Capture/fork only on the first Web operation, including after recovery."""
        with publication_lock(self.task_root):
            value = self.authority(api_key)
            if value['view_context'] is not None:
                return
            previous, seen = value.get('previous_request_id'), set()
            seed, parent_context = None, None
            while previous is not None:
                if previous in seen:
                    raise CoreError('conflict')
                seen.add(previous)
                try:
                    parent = self.for_request(self.project_root, previous, api_key)
                except FileNotFoundError:
                    break
                parent_data = parent.authority(api_key)
                if parent_data['view_context'] is not None:
                    seed = parent.workspace(api_key)
                    parent_context = parent_data['view_context']
                    break
                previous = parent_data.get('previous_request_id')
            from web_tools.editor_publication import recover
            recover(self.project_root)
            context = capture_editor_context(value['telegram_id'], root=self.project_root)
            if seed is not None:
                from web_tools.editor_publication import inspect
                status = inspect(seed)
                if status['task_is_current'] and status['task_operation'] == 'rollback':
                    seed = None
                elif (context['publication']['build_id'] != parent_context['publication']['build_id']
                      and not (status['task_is_current'] and status['task_operation'] == 'publish')):
                    raise CoreError('conflict', details={'reason': 'ui_publication_changed'})
            if seed is None:
                EditorWorkspace.create(self.project_root, self.task_root,
                                       expected_source_revision=context['source_revision'])
            else:
                seed.fork(self.task_root, expected_revision=seed.inspect()['revision'],
                          source_revision=context['source_revision'])
            value['view_context'] = context
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
        value = self.authority(api_key)
        if value['view_context'] is None:
            return value
        workspace = EditorWorkspace(self.project_root, self.task_root)
        if value['view_context'].get('source_revision') != workspace.source_revision():
            raise CoreError('conflict', details={'reason': 'editor_binding_changed'})
        return value

    def runtime_context(self, api_key: str) -> dict:
        """Source facts only; attachment references belong to their upload message."""
        value = self._read(api_key)
        if value['view_context'] is None:
            return {}
        return {'web_editor': {'task_id': self.task_id, **{
            name: copy.deepcopy(value['view_context'][name])
            for name in ('viewed', 'publication', 'source_revision', 'effective_source')
        }}}

    def workspace(self, api_key: str) -> EditorWorkspace:
        self._materialize(api_key)
        self._read(api_key)
        # Each actual draft operation validates its snapshot under the task lock.
        # Admission/status must not compete with a running compiler for that lock.
        return EditorWorkspace(self.project_root, self.task_root)

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
