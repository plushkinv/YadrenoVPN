"""Internal file operations bound to an already authorized editor task.

Only trusted task orchestration may supply installation/task roots. This is not
an authentication, Hub, build, publication or process-isolation boundary. Model
inputs select relative custom files or declared stock targets, never roots.
Concurrent API writers use the task-local lock; unrelated local processes must
not mutate task files while an operation holds that lock.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

from web_tools.build import custom_inventory_fingerprint, source_version
from web_tools.editor_files import (
    ASSET_EXTENSIONS, TEXT_EXTENSIONS, checked_directory, checked_info,
    read_regular, remove_regular, scan_sources, source_name, write_regular,
)
from web_tools.package import MAX_BYTES, MAX_FILES, digest
from web_tools.paths import canonical, local_path, publication_lock
from web_tools.view_inventory import build_inventory


class StaleRevision(ValueError):
    """No mutation occurred because the inspected source revision is obsolete."""


def _revision(value, *, optional=False):
    if value is None and optional:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('invalid editor source revision')
    return value


def _inventory(files):
    return {name: {'sha256': digest(content), 'size': len(content)} for name, content in sorted(files.items())}


def _source_revision(value):
    if not isinstance(value, dict) or set(value) != {'base', 'custom'}:
        raise ValueError('invalid editor source revision')
    return {'base': _revision(value['base']), 'custom': _revision(value['custom'], optional=True)}


def _fingerprint(files):
    return custom_inventory_fingerprint(_inventory(files))


class EditorWorkspace:
    """A private task copy; resolve owner/task authorization before constructing."""

    def __init__(self, project_root, task_root):
        self._project = checked_directory(project_root)
        self._task = checked_directory(task_root, private=True)
        self._folder = local_path(self._task, 'editor')
        self._custom = self._folder / 'custom_web'
        self._baseline = self._folder / 'baseline'
        live = self._project / 'custom_web'
        if (self._task == self._project or self._project.is_relative_to(self._task)
                or self._task.is_relative_to(live) or live.is_relative_to(self._task)
                or self._task.is_relative_to(self._project / 'web')):
            raise ValueError('editor task must be separate from installation sources')

    @classmethod
    def create(cls, project_root, task_root, *, expected_source_revision):
        """Capture supported inputs without changing live files or an older task."""
        expected_source_revision = _source_revision(expected_source_revision)
        workspace = cls(project_root, task_root)
        if workspace._folder.exists():
            raise ValueError('editor task workspace already exists')
        live = workspace._project / 'custom_web'
        if live.is_symlink():
            raise ValueError('editor source directory must not be a link')
        exists = live.exists()
        files = scan_sources(live) if exists else {}
        source_revision = {'base': source_version(workspace._project, include_commit=False)[0],
                           'custom': _fingerprint(files) if exists else None}
        if source_revision != expected_source_revision:
            raise StaleRevision('UI sources changed before task capture')
        # No task files are written before the bounded source/CAS checks pass.
        workspace._folder.mkdir(mode=0o700)
        workspace._baseline.mkdir(mode=0o700)
        workspace._custom.mkdir(mode=0o700)
        for name, content in files.items():
            write_regular(workspace._baseline, name, content)
            write_regular(workspace._custom, name, content)
        captured = _fingerprint(scan_sources(workspace._baseline))
        if source_revision['custom'] is not None and captured != source_revision['custom']:
            raise StaleRevision('custom sources changed during task capture')
        write_regular(workspace._folder, 'state.json', canonical({
            'format_version': 1, 'source_revision': source_revision,
        }))
        return workspace

    @classmethod
    def open(cls, project_root, task_root):
        """Resume the same trusted task; incomplete captures are never repaired."""
        workspace = cls(project_root, task_root)
        with workspace._locked():
            workspace._state()
            scan_sources(workspace._custom)
        return workspace

    def fork(self, task_root, *, expected_revision, source_revision=None):
        """Continue a verified draft with its exact candidate and live-source CAS."""
        with self._locked():
            state, files, revision = self._snapshot()
            self._require_current(state, revision, expected_revision)
            try:
                candidate = json.loads(read_regular(self._folder, 'candidate.json', maximum=4096))
            except FileNotFoundError:
                candidate = None
            if candidate is not None:
                self._read_candidate(state, candidate.get('revision'))
                if candidate['revision'] != revision:
                    # Keep unfinished edits, but never carry an older build as their candidate.
                    candidate = None
            captured_revision = state['source_revision'] if source_revision is None else _source_revision(source_revision)
            if captured_revision != state['source_revision']:
                from web_tools.editor_publication import _record
                from web_tools.publication import read_pointer
                receipt = _record(self)
                if (not receipt or receipt['operation'] != 'publish'
                        or read_pointer(self._project / 'web_runtime') != receipt['after']
                        or captured_revision['base'] != state['source_revision']['base']
                        or captured_revision['custom'] != receipt['revision']):
                    raise StaleRevision('only the parent task publication can seed a later draft')
            target = self.create(self._project, task_root, expected_source_revision=captured_revision)
            captured = scan_sources(target._custom)
            for name in set(captured) - set(files):
                remove_regular(target._custom, name)
            for name, data in files.items():
                if captured.get(name) != data:
                    write_regular(target._custom, name, data)
            if candidate is not None:
                candidate['source_revision'] = captured_revision
                write_regular(target._folder, 'candidate.json', canonical(candidate))
                target._read_candidate(target._state(), revision)
            return target

    @contextlib.contextmanager
    def _locked(self):
        checked_directory(self._task, private=True)
        checked_directory(self._folder, private=True)
        # This reuses the existing interprocess lock on a task-local directory.
        # The lock and baseline are outside the only agent-writable custom tree.
        lock = local_path(self._folder, 'locks/publication.lock')
        if lock.exists():
            checked_info(lock.lstat())
        with publication_lock(self._folder):
            yield

    def _state(self):
        value = json.loads(read_regular(self._folder, 'state.json', maximum=4096))
        if (not isinstance(value, dict) or set(value) != {'format_version', 'source_revision'}
                or type(value['format_version']) is not int or value['format_version'] != 1):
            raise ValueError('invalid editor workspace metadata')
        source_revision = _source_revision(value['source_revision'])
        baseline = scan_sources(self._baseline)
        if ((source_revision['custom'] is None and baseline)
                or source_revision['custom'] is not None
                and _fingerprint(baseline) != source_revision['custom']):
            raise ValueError('editor baseline changed')
        return value

    def _snapshot(self):
        state = self._state()
        files = scan_sources(self._custom)
        revision = _fingerprint(files)
        return state, files, revision

    def source_revision(self):
        """Immutable admission metadata, readable while the draft compiler runs."""
        return dict(self._state()['source_revision'])

    def inspect(self):
        with self._locked():
            state, files, revision = self._snapshot()
            return {'source_revision': state['source_revision'], 'revision': revision,
                    'files': _inventory(files)}

    def read_custom(self, relative):
        name = source_name(relative)
        with self._locked():
            state, files, revision = self._snapshot()
            if name not in files:
                if not name.startswith('src/'):
                    raise ValueError('editor source file is unavailable')
                self._require_current(state, revision, revision)
                content = read_regular(self._project / 'web', name)
                self._require_current(state, revision, revision)
            else:
                content = files[name]
            return {'file': name, 'content': content, 'sha256': digest(content),
                    'size': len(content), 'revision': revision,
                    'source_kind': 'custom' if name in files else 'stock'}

    def read_base(self, kind, target_id):
        """Read only a stock target from the single frontend source registry."""
        if not isinstance(kind, str) or kind not in {'pages', 'components', 'styles'} or not isinstance(target_id, str):
            raise ValueError('unsupported stock UI target')
        with self._locked():
            state = self._state()
            if source_version(self._project, include_commit=False)[0] != state['source_revision']['base']:
                raise StaleRevision('stock UI sources changed since task capture')
            inventory = build_inventory(self._project)
            if kind == 'styles':
                source = next((item for item in inventory['styles'] if item['file'] == target_id), None) if inventory else None
                target = {'source': source} if source else None
            else:
                target = next((item for item in inventory[kind] if item['id'] == target_id), None) if inventory else None
            if target is None or target['source']['kind'] != 'stock':
                raise ValueError('stock UI source is unavailable')
            name = target['source']['file']
            content = read_regular(self._project / 'web', name)
            if source_version(self._project, include_commit=False)[0] != state['source_revision']['base']:
                raise StaleRevision('stock UI sources changed during reading')
            return {'target_id': target_id, 'kind': kind, 'file': name,
                    'content': content, 'sha256': digest(content), 'size': len(content)}

    def _effective_inventory(self, files):
        declaration = json.loads(files.get('manifest.json', b'{}'))
        if not isinstance(declaration, dict):
            raise ValueError('invalid custom manifest')
        declaration = dict(declaration)
        for group in ('pages', 'components'):
            declaration[group] = [{**item, 'file': str(local_path(self._custom, source_name(item['file'])))}
                                  for item in declaration.get(group, [])]
        declaration['styles'] = [str(local_path(self._custom, source_name(name)))
                                 for name in declaration.get('styles', [])]
        return build_inventory(self._project, self._custom, declaration)

    def inspect_targets(self):
        """Discover registered draft targets only when inspection requests them."""
        with self._locked():
            state, files, revision = self._snapshot()
            self._require_current(state, revision, revision)
            inventory = self._effective_inventory(files)
            targets = [{'kind': kind, 'key': item['id']} for group, kind in
                       [('pages', 'web_page'), ('components', 'web_component')] for item in inventory[group]]
            targets.extend({'kind': 'web_style', 'key': item['file']} for item in inventory['styles'])
            return {'revision': revision, 'targets': targets}

    def read_effective(self, kind, target_id):
        """Read the actual draft override, or a stock file ready for a mirror edit."""
        if kind not in {'pages', 'components', 'styles'} or not isinstance(target_id, str):
            raise ValueError('unsupported UI target')
        with self._locked():
            state, files, revision = self._snapshot()
            self._require_current(state, revision, revision)
            inventory = self._effective_inventory(files)
            if kind == 'styles':
                source = next((item for item in inventory['styles'] if item['file'] == target_id), None)
            else:
                target = next((item for item in inventory[kind] if item['id'] == target_id), None)
                source = target['source'] if target else None
            if source is None:
                raise ValueError('UI source is unavailable')
            name = source['file']
            content = files[name] if source['kind'] == 'custom' else read_regular(self._project / 'web', name)
            self._require_current(state, revision, revision)
            return {'target_id': target_id, 'kind': kind, 'source_kind': source['kind'],
                    'file': name, 'export': source.get('export'), 'revision': revision,
                    'content': content, 'sha256': digest(content), 'size': len(content),
                    'shared_targets': [item['id'] for group in ('pages', 'components') for item in inventory[group]
                                       if item['source']['file'] == name and item['source']['kind'] == source['kind']]}

    def write_custom(self, relative, content, *, expected_revision):
        """Replace UTF-8 frontend source after an exact whole-workspace CAS."""
        name = source_name(relative)
        if Path(name).suffix not in TEXT_EXTENSIONS or not isinstance(content, str):
            raise ValueError('editor source writes require UTF-8 text')
        if len(content) > MAX_BYTES:
            raise ValueError('editor file exceeds resource limits')
        return self._mutate(name, content.encode('utf-8'), expected_revision)

    def build_candidate(self, *, expected_revision):
        """Compile a locked task copy, then sign outside the isolated compiler.

        This verifies package integrity only. It does not authorize publication
        or attest visual, business or environment checks required by the task.
        """
        from web_tools.build import build
        from web_tools.compiler_sandbox import compile_isolated

        _revision(expected_revision)
        with self._locked():
            state, _, revision = self._snapshot()
            self._require_current(state, revision, expected_revision)
            candidate = build(self._project, self._project / 'web_runtime', self._custom,
                              compiler=compile_isolated)
            # Recheck the task revision before making its result discoverable.
            current, _, actual = self._snapshot()
            self._require_current(current, actual, expected_revision)
            stage = local_path(self._project / 'web_runtime', 'staging/' + candidate['build_id'])
            package = read_regular(stage, 'package.zip')
            binding = {'format_version': 1, 'source_revision': state['source_revision'],
                       'revision': revision, 'build_id': candidate['build_id'],
                       'package_sha256': digest(package)}
            write_regular(self._folder, 'candidate.json', canonical(binding))
            return self._candidate(state, revision)

    def candidate(self, *, expected_revision):
        """Reopen the exact verified artifact; never infer a candidate from live UI."""
        _revision(expected_revision)
        with self._locked():
            state, _, revision = self._snapshot()
            self._require_current(state, revision, expected_revision)
            return self._candidate(state, revision)

    def candidate_bundle(self, *, expected_revision):
        """Return verified bytes to trusted preview/publication orchestration only."""
        _revision(expected_revision)
        with self._locked():
            state, _, revision = self._snapshot()
            self._require_current(state, revision, expected_revision)
            return self._read_candidate(state, revision)

    def _require_current(self, state, revision, expected):
        if (revision != expected or source_version(self._project, include_commit=False)[0]
                != state['source_revision']['base']):
            raise StaleRevision('editor sources changed since inspection')

    def _candidate(self, state, revision):
        return self._read_candidate(state, revision)[0]

    def _read_candidate(self, state, revision):
        from web_tools.compatibility import current_capabilities, verification_versions
        from web_tools.package import verify_package

        binding = json.loads(read_regular(self._folder, 'candidate.json', maximum=4096))
        if (not isinstance(binding, dict)
                or set(binding) != {'format_version', 'source_revision', 'revision', 'build_id', 'package_sha256'}
                or type(binding['format_version']) is not int or binding['format_version'] != 1
                or not isinstance(binding['build_id'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', binding['build_id'])):
            raise ValueError('invalid editor candidate metadata')
        _revision(binding['package_sha256'])
        if binding['source_revision'] != state['source_revision'] or binding['revision'] != revision:
            raise StaleRevision('editor candidate belongs to another source revision')
        runtime = local_path(self._project, 'web_runtime')
        stage = local_path(runtime, 'staging/' + binding['build_id'])
        content = read_regular(stage, 'package.zip')
        if digest(content) != binding['package_sha256']:
            raise ValueError('editor candidate bytes changed')
        identity = json.loads(read_regular(runtime, 'identity.json', maximum=4096))
        signed, files = verify_package(content, identity, **verification_versions(current_capabilities()))
        manifest = signed['manifest']
        if (manifest['build_id'] != binding['build_id']
                or manifest['base_build_id'] != state['source_revision']['base']):
            raise ValueError('editor candidate does not match its source binding')
        result = {'build_id': manifest['build_id'], 'base_build_id': manifest['base_build_id'],
                'content_hash': manifest['content_hash'], 'customization_version': manifest['customization_version'],
                'revision': revision, 'package_sha256': binding['package_sha256'],
                'package_valid': True, 'activated': False}
        return result, signed, files, content

    def write_asset(self, relative, content, *, expected_revision):
        """Store a ready asset as bytes; archives and commands are not supported."""
        name = source_name(relative)
        if Path(name).suffix not in ASSET_EXTENSIONS or not isinstance(content, bytes):
            raise ValueError('editor asset writes require supported asset bytes')
        return self._mutate(name, content, expected_revision)

    def delete_custom(self, relative, *, expected_revision):
        return self._mutate(source_name(relative), None, expected_revision)

    def _mutate(self, name, content, expected_revision):
        _revision(expected_revision)
        if content is not None and len(content) > MAX_BYTES:
            raise ValueError('editor file exceeds resource limits')
        with self._locked():
            state, files, revision = self._snapshot()
            if revision != expected_revision:
                raise StaleRevision('editor sources changed since inspection')
            if content is None:
                if name not in files:
                    raise ValueError('editor source file is unavailable')
            if name.startswith('src/') and name not in files and content is not None:
                self._require_current(state, revision, expected_revision)
            changed = {name: content}
            # Addressed edits register only the conventional style directory or
            # bootstrap a mirrored source tree. Existing declarations stay intact.
            style = (name.startswith('styles/') and name.endswith('.css')
                     and all(not part.startswith('.') for part in name.split('/')))
            if style or name.startswith('src/') and content is not None and 'manifest.json' not in files:
                manifest = json.loads(files.get('manifest.json', b'{}'))
                if 'manifest.json' not in files:
                    manifest = {'format_version': 1, 'version': '1.0.0', 'api': {'min': 1, 'max': 1},
                                'frontend_api': {'min': 1, 'max': 1}, 'environment_contract': 1}
                if not isinstance(manifest, dict):
                    raise ValueError('invalid custom manifest')
                if style:
                    styles = manifest.get('styles', [])
                    if not isinstance(styles, list) or any(not isinstance(item, str) for item in styles):
                        raise ValueError('invalid custom styles')
                    if content is None:
                        manifest['styles'] = [item for item in styles if item != name]
                    else:
                        manifest['styles'] = [*styles, name] if name not in styles else styles
                changed['manifest.json'] = canonical(manifest)
            updated = dict(files)
            for path, data in changed.items():
                if data is None:
                    updated.pop(path, None)
                else:
                    updated[path] = data
            if len(updated) > MAX_FILES or sum(map(len, updated.values())) > MAX_BYTES:
                raise ValueError('editor tree exceeds resource limits')
            written = []
            try:
                for path, data in changed.items():
                    if data is None:
                        remove_regular(self._custom, path)
                    else:
                        write_regular(self._custom, path, data)
                    written.append(path)
            except OSError:
                for path in reversed(written):
                    if path in files:
                        write_regular(self._custom, path, files[path])
                    else:
                        remove_regular(self._custom, path)
                raise
            files = updated
            return {'file': name, 'revision': _fingerprint(files),
                    'changed_files': list(changed),
                    'sha256': digest(content) if content is not None else None,
                    'size': len(content) if content is not None else 0}
