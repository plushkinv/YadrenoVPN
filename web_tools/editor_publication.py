"""Publish an exact checked source snapshot, or restore sources and rebuild it."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from web_tools.compatibility import current_capabilities
from web_tools.editor_files import read_regular, remove_regular, scan_sources, write_regular
from web_tools.editor_workspace import EditorWorkspace, StaleRevision
from web_tools.errors import WebSourceError
from web_tools.paths import canonical, local_path, publication_lock
from web_tools.publication import _activate_verified, read_pointer
from web_tools.release import publication
from web_tools.source_tree import archive_bytes, fingerprint, read_archive, replace_tree, restore_source

JOURNAL = 'editor-activation.json'
RECEIPT = 'publication.json'
LAST = 'editor-publication.json'


def _record(workspace):
    path = workspace._folder / RECEIPT
    return json.loads(read_regular(workspace._folder, RECEIPT, maximum=8192)) if path.exists() else None


def _projection(pointer, receipt):
    return {'current_build_id': pointer['current'], 'previous_build_id': None,
            'task_operation': receipt['operation'] if receipt else None,
            'task_build_id': receipt['build_id'] if receipt else None,
            'task_is_current': bool(receipt and pointer == receipt['after'])}


def inspect(workspace):
    recover(workspace._project)
    return _projection(read_pointer(workspace._project / 'web_runtime'), _record(workspace))


def _finish(workspace, journal):
    runtime = workspace._project / 'web_runtime'
    write_regular(workspace._folder, RECEIPT, canonical(journal))
    write_regular(runtime, LAST, canonical(journal))
    remove_regular(runtime, JOURNAL)
    (runtime / 'editor-before.zip').unlink(missing_ok=True)


def recover(root):
    """Resolve a single write-ahead activation; do not adopt unrelated source edits."""
    root = Path(root)
    runtime = local_path(root, 'web_runtime')
    if not (runtime / JOURNAL).exists():
        return
    with publication_lock(runtime / 'source-lock'), publication_lock(runtime):
        if not (runtime / JOURNAL).exists():
            return
        value = json.loads(read_regular(runtime, JOURNAL, maximum=8192))
        if value.get('format_version') == 1:
            from web_tools.source_transition import recover_legacy
            recover_legacy(root, value)
            return
        if value.get('format_version') != 2 or not re.fullmatch(r'[a-f0-9]{32}', value.get('task_id', '')):
            raise ValueError('invalid publication recovery metadata')
        workspace = EditorWorkspace.open(root, runtime / 'editor_tasks' / value['task_id'])
        before = read_archive(read_regular(runtime, 'editor-before.zip'))
        stage = local_path(runtime, 'staging/' + value['build_id'])
        after = read_archive(read_regular(stage, 'sources.zip'))
        actual = scan_sources(workspace._custom)
        if any(actual.get(name) not in (before.get(name), after.get(name)) for name in set(before) | set(after) | set(actual)):
            raise StaleRevision('Working files were changed outside the interrupted publication.')
        pointer = read_pointer(runtime)
        if pointer == value['after']:
            publication(runtime, pointer['current'], current_capabilities())
            replace_tree(workspace._custom, after)
            _finish(workspace, value)
        elif pointer == value['before']:
            replace_tree(workspace._custom, before)
            remove_regular(runtime, JOURNAL)
            (runtime / 'editor-before.zip').unlink(missing_ok=True)
        else:
            raise StaleRevision('Another publication followed the interrupted operation.')


def _activate(workspace, bundle, *, before_sources, after_sources, base_build_id, operation, authorize):
    """Caller holds the source lock; pointer commit also selects its source archive."""
    runtime = workspace._project / 'web_runtime'
    candidate, signed, files, content = bundle
    with publication_lock(runtime):
        pointer = read_pointer(runtime)
        if pointer['current'] == candidate['build_id']:
            return {**_projection(pointer, _record(workspace)), 'changed': False}
        receipt = _record(workspace)
        if pointer['current'] != base_build_id and not (receipt and receipt['after'] == pointer):
            raise StaleRevision('The live publication changed. Inspect web.publication before applying this candidate.')
        if scan_sources(workspace._custom) != before_sources:
            raise StaleRevision('Working sources changed before activation.')
        authorize()
        journal = {'format_version': 2, 'task_id': workspace._task.name, 'operation': operation,
                   'before': pointer, 'after': {'current': candidate['build_id'], 'previous': None},
                   'revision': fingerprint(after_sources), 'build_id': candidate['build_id']}
        write_regular(runtime, 'editor-before.zip', archive_bytes(before_sources))
        write_regular(runtime, JOURNAL, canonical(journal))
        try:
            replace_tree(workspace._custom, after_sources)
            authorize()
            trust = json.loads(read_regular(runtime, 'identity.json', maximum=4096))
            _activate_verified(runtime, content, trust, signed, files)
            publication(runtime, candidate['build_id'], current_capabilities())
            _finish(workspace, journal)
        except BaseException:
            if read_pointer(runtime) == pointer:
                replace_tree(workspace._custom, before_sources)
                remove_regular(runtime, JOURNAL)
                (runtime / 'editor-before.zip').unlink(missing_ok=True)
            raise
    return {**_projection(journal['after'], journal), 'changed': True}


def apply(workspace, *, build_id, base_build_id, authorize):
    recover(workspace._project)
    with workspace._locked():
        _, sources, revision = workspace._snapshot()
        bundle = workspace._read_candidate(None, revision)
        if bundle[0]['build_id'] != build_id:
            checked_id = bundle[0]['build_id']
            raise WebSourceError('web_candidate_mismatch',
                                 f'This build_id does not identify the checked candidate {checked_id}.',
                                 next_action=f'Use web.publish with build_id="{checked_id}" to apply this candidate. '
                                 'No rebuild is needed while the working files remain unchanged.')
        return _activate(workspace, bundle, before_sources=sources, after_sources=sources,
                         base_build_id=base_build_id, operation='publish', authorize=authorize)


def restore(workspace, *, source, publish, base_build_id, authorize):
    """Preview-only restore never activates. Published restore builds before changing files."""
    recover(workspace._project)
    with workspace._locked():
        before = scan_sources(workspace._custom)
        pointer = read_pointer(workspace._project / 'web_runtime')
        after = restore_source(workspace._project, source)
        authorize()
        if not publish:
            try:
                replace_tree(workspace._custom, after)
                if source == 'published':
                    # Reuse the verified live package after resetting its exact
                    # sources; this receipt does not activate a publication.
                    write_regular(workspace._folder, RECEIPT, canonical({
                        'format_version': 2, 'task_id': workspace._task.name, 'operation': 'restore',
                        'before': pointer, 'after': pointer, 'revision': fingerprint(after),
                        'build_id': pointer['current'],
                    }))
            except BaseException:
                replace_tree(workspace._custom, before)
                raise
            (workspace._folder / 'candidate.json').unlink(missing_ok=True)
            return {'changed': before != after, 'publication_changed': False,
                    'source': source, 'revision': fingerprint(after)}
        from web_tools.build import build
        from web_tools.compiler_sandbox import compile_isolated
        folder = local_path(workspace._folder, 'restore-' + uuid.uuid4().hex, directory=True)
        candidate_path = workspace._folder / 'candidate.json'
        previous_candidate = candidate_path.read_bytes() if candidate_path.exists() else None
        try:
            replace_tree(folder, after)
            candidate = build(workspace._project, workspace._project / 'web_runtime', folder,
                              compiler=compile_isolated)
            # Record only after the full build succeeds. Exact package checks do
            # not depend on the temporary source directory surviving.
            workspace.record_candidate(candidate, fingerprint(after))
            bundle = workspace._read_candidate(None, fingerprint(after))
            result = _activate(workspace, bundle, before_sources=before, after_sources=after,
                               base_build_id=base_build_id, operation='restore', authorize=authorize)
            return {**result, 'publication_changed': result['changed']}
        except BaseException:
            if not (workspace._project / 'web_runtime' / JOURNAL).exists():
                if previous_candidate is None:
                    candidate_path.unlink(missing_ok=True)
                else:
                    write_regular(workspace._folder, candidate_path.name, previous_candidate)
            raise
        finally:
            if folder.resolve().is_relative_to(workspace._folder.resolve()):
                shutil.rmtree(folder)
