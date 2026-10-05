"""Capture the old template baseline and report managed UI updates."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from web_tools.package import _json
from web_tools.paths import PROJECT_ROOT, atomic_write, canonical, local_path
from web_tools.publication import read_pointer

STATUS_FILE = 'release-status.json'
CODES = {'updated', 'custom_preserved', 'toolchain_missing', 'dependencies_changed', 'source_missing',
         'unverified_source', 'prebuild_failed', 'source_changed'}
_COMMIT = re.compile(r'[a-f0-9]{40,64}')
_BUILD = re.compile(r'[a-f0-9]{32}')


def prepare_sources(root, target, snapshot, plan):
    """Capture the old installed baseline; never rebuild or merge a modified UI."""
    from web_tools.source_tree import ensure, unchanged, fingerprint
    from web_tools.editor_files import scan_sources
    if not isinstance(target, str) or not _COMMIT.fullmatch(target):
        raise ValueError('invalid target commit')
    folder = ensure(root)
    files = scan_sources(folder)
    return {**plan, 'target_commit': target, 'working_fingerprint': fingerprint(files),
            'template_unchanged': unchanged(root, files)}


def release_status(root=PROJECT_ROOT):
    """Return bounded private diagnostics only for the still-active publication."""
    try:
        runtime = local_path(root, 'web_runtime')
        path = local_path(runtime, STATUS_FILE)
        if not path.exists():
            return None
        if path.stat().st_size > 4096:
            raise ValueError('oversized release diagnostic')
        value = _json(path.read_bytes())
        if (not isinstance(value, dict) or set(value) != {'format_version', 'target_commit', 'active_build_id', 'rebuild_required', 'code'}
                or type(value['format_version']) is not int or value['format_version'] != 1
                or not isinstance(value['target_commit'], str) or not _COMMIT.fullmatch(value['target_commit'])
                or not isinstance(value['active_build_id'], str) or not _BUILD.fullmatch(value['active_build_id'])
                or type(value['rebuild_required']) is not bool or value['code'] not in CODES
                or value['rebuild_required'] != (value['code'] not in {'updated', 'custom_preserved'})):
            raise ValueError('invalid release diagnostic')
        return value if value['active_build_id'] == read_pointer(runtime)['current'] else None
    except (OSError, ValueError, KeyError, TypeError):
        return {'code': 'unavailable', 'rebuild_required': True}


def capture_status(root):
    path = local_path(Path(root) / 'web_runtime', STATUS_FILE)
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def restore_status(root, previous):
    path = local_path(Path(root) / 'web_runtime', STATUS_FILE)
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, base64.b64decode(previous, validate=True))


def record_status(root, target, build_id, code):
    if not _COMMIT.fullmatch(target) or not _BUILD.fullmatch(build_id) or code not in CODES:
        raise ValueError('invalid release diagnostic')
    atomic_write(local_path(Path(root) / 'web_runtime', STATUS_FILE), canonical({'format_version': 1,
        'target_commit': target, 'active_build_id': build_id, 'rebuild_required': code not in {'updated', 'custom_preserved'}, 'code': code}))
