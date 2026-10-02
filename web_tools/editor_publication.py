"""Promote an admitted task's exact UI artifact without touching business data.

Task intent follows the existing customization apply contract. This internal
adapter enforces source/publication CAS and uses the existing signed publisher.
Its small write-ahead record recovers interrupted source promotion before another
editor operation or startup; it is not an agent job or a second publication store.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from web_tools.compatibility import current_capabilities
from web_tools.editor_files import read_regular, remove_regular, scan_sources, write_regular
from web_tools.editor_workspace import EditorWorkspace, StaleRevision
from web_tools.paths import canonical, local_path, publication_lock
from web_tools.publication import _activate_verified, read_pointer
from web_tools.release import publication

JOURNAL = 'editor-activation.json'
RECEIPT = 'publication.json'
LAST = 'editor-publication.json'


def _live(root):
    folder = local_path(root, 'custom_web')
    return scan_sources(folder) if folder.exists() else None


def _replace_sources(root, before, after):
    """Change only captured frontend files; preserve ignored local directories."""
    folder = local_path(root, 'custom_web')
    actual = _live(root)
    if any((actual or {}).get(name) not in ((before or {}).get(name), (after or {}).get(name))
           for name in set(actual or {}) | set(before or {}) | set(after or {})):
        raise StaleRevision('UI sources changed during publication recovery')
    if after is not None:
        folder.mkdir(mode=0o700, exist_ok=True)
    for name in sorted(set(actual or {}) - set(after or {}), reverse=True):
        remove_regular(folder, name)
        parent = local_path(folder, name).parent
        while parent != folder:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    for name, data in (after or {}).items():
        if (actual or {}).get(name) != data:
            write_regular(folder, name, data)
    if after is None and folder.exists():
        # Never recursively delete untracked/ignored installation material.
        folder.rmdir()
    if _live(root) != after:
        raise StaleRevision('UI source promotion did not complete')


def _record(workspace):
    try:
        return json.loads(read_regular(workspace._folder, RECEIPT, maximum=8192))
    except FileNotFoundError:
        return None


def _finish(workspace, journal):
    write_regular(workspace._folder, RECEIPT, canonical(journal))
    write_regular(workspace._project / 'web_runtime', LAST, canonical(journal))
    remove_regular(workspace._project / 'web_runtime', JOURNAL)


def _recover_locked(root, journal):
    if (not isinstance(journal, dict) or set(journal) != {
            'format_version', 'task_id', 'operation', 'before', 'after', 'source_revision', 'revision', 'build_id'}
            or journal['format_version'] != 1 or journal['operation'] not in {'publish', 'rollback'}
            or not isinstance(journal['task_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', journal['task_id'])):
        raise ValueError('invalid editor publication recovery record')
    workspace = EditorWorkspace.open(root, local_path(root, 'web_runtime/editor_tasks/' + journal['task_id']))
    with workspace._locked():
        state, draft, revision = workspace._snapshot()
        if state['source_revision'] != journal['source_revision'] or revision != journal['revision']:
            raise StaleRevision('editor sources changed during publication recovery')
        baseline = scan_sources(workspace._baseline) if state['source_revision']['custom'] is not None else None
        before, after = (baseline, draft) if journal['operation'] == 'publish' else (draft, baseline)
        runtime = local_path(root, 'web_runtime')
        pointer = read_pointer(runtime)
        if pointer == journal['after']:
            publication(runtime, pointer['current'], current_capabilities())
            if _live(root) != after:
                raise StaleRevision('published UI sources changed')
            _finish(workspace, journal)
        elif pointer == journal['before']:
            _replace_sources(root, after, before)
            remove_regular(runtime, JOURNAL)
        else:
            raise StaleRevision('another publication followed the interrupted editor operation')


def recover(root):
    """Resolve only a durable, installation-owned interrupted UI operation."""
    root = Path(root)
    runtime = local_path(root, 'web_runtime')
    if not local_path(runtime, JOURNAL).exists():
        return
    with publication_lock(runtime):
        try:
            journal = json.loads(read_regular(runtime, JOURNAL, maximum=8192))
        except FileNotFoundError:
            return
        _recover_locked(root, journal)


def inspect(workspace):
    """Report actual activation, including recovery after a lost tool response."""
    recover(workspace._project)
    with workspace._locked():
        receipt = _record(workspace)
        pointer = read_pointer(workspace._project / 'web_runtime')
        return {'current_build_id': pointer['current'], 'previous_build_id': pointer['previous'],
                'task_operation': receipt['operation'] if receipt else None,
                'task_build_id': receipt['build_id'] if receipt else None,
                'task_is_current': bool(receipt and pointer == receipt['after'])}


def undo(workspace, *, expected_revision, build_id, authorize):
    """A later shared turn can undo the last exact editor publication it viewed."""
    root = workspace._project
    runtime = local_path(root, 'web_runtime')
    recover(root)
    with workspace._locked():
        state, files, revision = workspace._snapshot()
        workspace._require_current(state, revision, expected_revision)
    last = json.loads(read_regular(runtime, LAST, maximum=8192))
    task_id = last.get('task_id')
    if (not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id)
            or last.get('build_id') != build_id):
        raise StaleRevision('the selected editor publication is no longer current')
    original = EditorWorkspace.open(root, local_path(root, 'web_runtime/editor_tasks/' + task_id))
    with original._locked():
        if _record(original) != last or files != scan_sources(original._custom):
            raise StaleRevision('another draft must not be discarded by UI rollback')
    result = apply(original, expected_revision=last['revision'], build_id=build_id,
                   base_build_id=(last['before']['current'] if last['operation'] == 'publish' else last['after']['current']),
                   rollback=True, authorize=authorize)
    if original._task != workspace._task:
        # Associate the outcome with the requesting turn without copying sources.
        with workspace._locked():
            write_regular(workspace._folder, RECEIPT, read_regular(original._folder, RECEIPT, maximum=8192))
    return result


def apply(workspace, *, expected_revision, build_id, base_build_id, rollback=False, authorize):
    """An explicit existing apply call is required; build/preview never call here."""
    root = workspace._project
    runtime = local_path(root, 'web_runtime')
    recover(root)
    # Read-only validations precede both live changes. No model-supplied receipt,
    # filesystem root, permission flag or alternative signer is accepted.
    with workspace._locked(), publication_lock(runtime):
        state, draft, revision = workspace._snapshot()
        workspace._require_current(state, revision, expected_revision)
        candidate, signed, files, content = workspace._read_candidate(state, revision)
        if candidate['build_id'] != build_id:
            raise StaleRevision('candidate changed after inspection')
        baseline = scan_sources(workspace._baseline) if state['source_revision']['custom'] is not None else None
        pointer = read_pointer(runtime)
        receipt = _record(workspace)
        operation = 'rollback' if rollback else 'publish'
        authorize()
        if (receipt and receipt['operation'] == operation and receipt['build_id'] == build_id
                and receipt['revision'] == revision and pointer == receipt['after']):
            if _live(root) != (baseline if rollback else draft):
                raise StaleRevision('UI sources changed after publication')
            publication(runtime, pointer['current'], current_capabilities())
            return {**inspect_locked(pointer, receipt), 'changed': False}
        if rollback:
            if (not receipt or receipt['operation'] != 'publish' or receipt['build_id'] != build_id
                    or receipt['revision'] != revision or pointer != receipt['after']
                    or pointer['previous'] != base_build_id):
                raise StaleRevision('only the unchanged task publication can be rolled back')
            signed, content, _ = publication(runtime, pointer['previous'], current_capabilities())
            from web_tools.package import verify_package
            trust = json.loads(read_regular(runtime, 'identity.json', maximum=4096))
            signed, files = verify_package(content, trust)
            before, after = draft, baseline
        else:
            if receipt or pointer['current'] != base_build_id:
                raise StaleRevision('UI publication changed since task capture')
            before, after = baseline, draft
        if _live(root) != before:
            raise StaleRevision('live custom sources changed since task capture')
        next_id = signed['manifest']['build_id']
        journal = {'format_version': 1, 'task_id': workspace._task.name, 'operation': operation,
                   'before': pointer, 'after': {'current': next_id, 'previous': pointer['current']},
                   'source_revision': state['source_revision'], 'revision': revision, 'build_id': build_id}
        trust = json.loads(read_regular(runtime, 'identity.json', maximum=4096))
        write_regular(runtime, JOURNAL, canonical(journal))
        try:
            _replace_sources(root, before, after)
            authorize()
            _activate_verified(runtime, content, trust, signed, files)
            # Read back the actual retained artifact and pointer before reporting.
            publication(runtime, next_id, current_capabilities())
            if read_pointer(runtime) != journal['after']:
                raise StaleRevision('UI publication changed during activation')
            _finish(workspace, journal)
        except BaseException:
            # The journal remains on an ambiguous result. Recovery distinguishes
            # completed pointer activation from a failed source-only promotion.
            if read_pointer(runtime) == pointer:
                _replace_sources(root, after, before)
                remove_regular(runtime, JOURNAL)
            raise
        return {**inspect_locked(journal['after'], journal), 'changed': True}


def inspect_locked(pointer, receipt):
    return {'current_build_id': pointer['current'], 'previous_build_id': pointer['previous'],
            'task_operation': receipt['operation'], 'task_build_id': receipt['build_id'], 'task_is_current': True}
