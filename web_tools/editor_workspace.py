"""Request receipts and candidates over the single installation working tree."""
from __future__ import annotations

import contextlib
import json
import re

from web_tools.build import source_version
from web_tools.editor_files import checked_directory, read_regular, scan_sources, source_name, write_regular
from web_tools.errors import WebSourceError
from web_tools.package import digest, verify_package
from web_tools.paths import canonical, local_path, publication_lock
from web_tools.source_tree import ensure, fingerprint, inventory, backup_request
from web_tools.view_inventory import build_inventory


class StaleRevision(WebSourceError):
    def __init__(self, message):
        super().__init__('web_source_changed', message,
                         next_action='Read the changed working files, then run web.build once before applying.')


class EditorWorkspace:
    """Only receipts are task-local. Every task reads the same custom_web files."""

    def __init__(self, project_root, task_root):
        self._project = checked_directory(project_root)
        self._task = checked_directory(task_root, private=True)
        self._folder = local_path(self._task, 'editor')
        self._custom = local_path(self._project, 'custom_web')

    @classmethod
    def create(cls, project_root, task_root):
        ensure(project_root)
        workspace = cls(project_root, task_root)
        workspace._folder.mkdir(mode=0o700, exist_ok=True)
        if not (workspace._folder / 'state.json').exists():
            backup = backup_request(project_root, workspace._task.name)
            revision = {'base': source_version(project_root, include_commit=False)[0],
                        'custom': backup['source_hash']}
            write_regular(workspace._folder, 'state.json', canonical({
                'format_version': 2, 'source_revision': revision, 'backup': backup,
            }))
        workspace._state()
        return workspace

    @classmethod
    def open(cls, project_root, task_root):
        workspace = cls(project_root, task_root)
        workspace._state()
        return workspace

    @contextlib.contextmanager
    def _locked(self):
        # Separate from publication locking: compilation signs under that lock.
        with publication_lock(self._project / 'web_runtime/source-lock'):
            yield

    def _state(self):
        value = json.loads(read_regular(self._folder, 'state.json', maximum=8192))
        if value.get('format_version') != 2 or set(value) != {'format_version', 'source_revision', 'backup'}:
            raise ValueError('invalid working-source request metadata')
        return value

    def source_revision(self):
        return dict(self._state()['source_revision'])

    def _snapshot(self):
        files = scan_sources(self._custom)
        return self._state(), files, fingerprint(files)

    def inspect(self):
        with self._locked():
            state, files, revision = self._snapshot()
            return {'source_revision': state['source_revision'], 'revision': revision,
                    'working_directory': str(self._custom), 'backup': state['backup'], 'files': inventory(files)}

    def read_custom(self, relative):
        name = source_name(relative)
        with self._locked():
            try:
                content = read_regular(self._custom, name)
            except FileNotFoundError:
                raise WebSourceError('web_file_missing', 'The working file does not exist.', file=name,
                                     next_action='List or search custom_web and select an existing file, or create this file.') from None
            return {'file': name, 'content': content, 'sha256': digest(content),
                    'size': len(content), 'source_kind': 'custom'}

    def inspect_targets(self):
        with self._locked():
            tree = build_inventory(self._project, self._custom)
            targets = [{'kind': kind, 'key': item['id'], 'file': item['source']['file']}
                       for group, kind in [('pages', 'web_page'), ('components', 'web_component')]
                       for item in tree[group]]
            targets.extend({'kind': 'web_style', 'key': item['file']} for item in tree['styles'])
            return {'targets': targets}

    def read_effective(self, kind, target_id):
        tree = build_inventory(self._project, self._custom)
        source = (next((item for item in tree['styles'] if item['file'] == target_id), None)
                  if kind == 'styles' else
                  next((item['source'] for item in tree[kind] if item['id'] == target_id), None))
        if source is None:
            raise WebSourceError('web_target_missing', 'This target is not registered in the working project.',
                                 next_action='Read custom_web/src/runtime/view-registry.json or search the working files.')
        return {**self.read_custom(source['file']), 'target_id': target_id, 'export': source.get('export')}

    def build_candidate(self):
        from web_tools.build import build
        from web_tools.compiler_sandbox import compile_isolated
        with self._locked():
            _, _, revision = self._snapshot()
            candidate = build(self._project, self._project / 'web_runtime', self._custom, compiler=compile_isolated)
            if fingerprint(scan_sources(self._custom)) != revision:
                raise StaleRevision('Working files changed during the build; no candidate was applied.')
            self.record_candidate(candidate, revision)
            return self._read_candidate(None, revision)[0]

    def record_candidate(self, candidate, revision):
        stage = local_path(self._project / 'web_runtime', 'staging/' + candidate['build_id'])
        signed = json.loads(read_regular(stage, 'manifest.json'))
        write_regular(self._folder, 'candidate.json', canonical({
            'format_version': 2, 'revision': revision, 'build_id': candidate['build_id'],
            'base_revision': signed['manifest']['base_build_id'],
            'package_sha256': digest(read_regular(stage, 'package.zip')),
        }))

    def candidate(self, *, expected_revision=None):
        return self.candidate_bundle(expected_revision=expected_revision)[0]

    def candidate_bundle(self, *, expected_revision=None):
        with self._locked():
            _, _, revision = self._snapshot()
            if expected_revision is not None and revision != expected_revision:
                raise StaleRevision('Working files changed after the preview was opened.')
            return self._read_candidate(None, revision)

    def _read_candidate(self, state, revision):
        from web_tools.compatibility import current_capabilities, verification_versions
        try:
            binding = json.loads(read_regular(self._folder, 'candidate.json', maximum=4096))
        except FileNotFoundError:
            raise WebSourceError('web_candidate_missing', 'No checked candidate exists for this request.',
                                 next_action='Run web.build to check the current working files and prepare a preview.') from None
        if (binding.get('format_version') != 2 or not re.fullmatch(r'[a-f0-9]{32}', binding.get('build_id', ''))):
            raise ValueError('invalid editor candidate metadata')
        if binding['revision'] != revision or binding['base_revision'] != source_version(self._project, include_commit=False)[0]:
            raise StaleRevision('The checked candidate no longer matches the working files or installed compiler inputs.')
        runtime = self._project / 'web_runtime'
        stage = local_path(runtime, 'staging/' + binding['build_id'])
        if not (stage / 'package.zip').exists():
            from web_tools.publication import read_pointer
            if read_pointer(runtime)['current'] != binding['build_id']:
                raise WebSourceError('web_candidate_expired', 'This temporary candidate has been cleaned up.',
                                     next_action='Run web.build to check the working files again.')
            stage = local_path(runtime, 'publications/' + binding['build_id'])
        content = read_regular(stage, 'package.zip')
        if digest(content) != binding['package_sha256']:
            raise ValueError('editor candidate bytes changed')
        identity = json.loads(read_regular(runtime, 'identity.json', maximum=4096))
        signed, files = verify_package(content, identity, **verification_versions(current_capabilities()))
        manifest = signed['manifest']
        if manifest['build_id'] != binding['build_id'] or manifest['base_build_id'] != binding['base_revision']:
            raise ValueError('editor candidate source binding is corrupted')
        result = {name: manifest[name] for name in ('build_id', 'base_build_id', 'content_hash', 'customization_version')}
        result.update(revision=revision, package_sha256=binding['package_sha256'], package_valid=True, activated=False)
        return result, signed, files, content
