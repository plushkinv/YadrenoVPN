"""Prepare known published customs against target source without publishing drafts."""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

from web_tools.build import admin_directory, build, custom_source_fingerprint, require_toolchain, source_version
from web_tools.compatibility import verification_versions
from web_tools.package import _json, verify_package
from web_tools.paths import PROJECT_ROOT, atomic_write, canonical, local_path, relative_name, source_provenance
from web_tools.publication import manifest_path, read_pointer

STATUS_FILE = 'release-status.json'
CODES = {'updated', 'toolchain_missing', 'dependencies_changed', 'source_missing',
         'unverified_source', 'prebuild_failed', 'source_changed'}
_COMMIT = re.compile(r'[a-f0-9]{40,64}')
_BUILD = re.compile(r'[a-f0-9]{32}')
_HASH = re.compile(r'[a-f0-9]{64}')


class RebuildUnavailable(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _selected_source(name):
    return (name.startswith('web/src/') or name in {
        'web/package.json', 'web/package-lock.json', 'web/vite.app.config.ts',
        'web/customization.mjs', 'web/index.html', 'web/preview.html',
        'web_tools/service_worker.js', 'web_tools/compatibility.json', 'web_tools/toolchain.json'}
        or re.fullmatch(r'web/tsconfig[^/]*\.json', name) is not None)


def target_sources(root, commit, destination):
    """Extract only ordinary frontend inputs from the exact reviewed Git tree."""
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValueError('invalid target commit')
    git = shutil.which('git')
    if not git:
        raise RebuildUnavailable('toolchain_missing')
    listing = subprocess.run([git, '-C', str(root), 'ls-tree', '-r', '-z', commit, '--',
        'web', 'web_tools/service_worker.js', 'web_tools/compatibility.json', 'web_tools/toolchain.json'],
        capture_output=True, check=True, timeout=60).stdout
    names = []
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        if not _selected_source(name):
            continue
        mode, kind, _ = metadata.split(b' ')
        if kind != b'blob' or mode not in {b'100644', b'100755'}:
            raise ValueError('target frontend requires regular source files')
        relative_name(name)
        names.append(name)
    required = {'web/package.json', 'web/package-lock.json', 'web/tsconfig.json',
                'web/vite.app.config.ts', 'web/customization.mjs', 'web/index.html',
                'web/preview.html', 'web_tools/service_worker.js', 'web_tools/compatibility.json'}
    if not required.issubset(names):
        raise ValueError('target release has incomplete frontend sources')
    content = subprocess.run([git, '-C', str(root), 'archive', '--format=tar', commit, *names],
                             capture_output=True, check=True, timeout=60).stdout
    written = set()
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as archive:
        for entry in archive:
            if entry.isdir():
                continue
            if not entry.isfile() or entry.name not in names or entry.name in written:
                raise ValueError('unexpected target source archive entry')
            source = archive.extractfile(entry)
            if source is None:
                raise ValueError('missing target source bytes')
            atomic_write(local_path(destination, entry.name), source.read())
            written.add(entry.name)
    if written != set(names):
        raise ValueError('incomplete target source archive')


def prepare_custom(root, target, snapshot, plan):
    """Preflight only: existing active UI and original custom source stay unchanged."""
    if not isinstance(target, str) or not _COMMIT.fullmatch(target):
        raise ValueError('invalid target commit')
    result = {**plan, 'target_commit': target}
    if not plan.get('selected') or plan.get('customization_version') == 'base':
        return result
    try:
        root, snapshot = admin_directory(root), admin_directory(snapshot)
        runtime = local_path(root, 'web_runtime')
        signed = _json(manifest_path(runtime, plan['selected']).read_bytes())
        proof = source_provenance(local_path(runtime, 'publications/' + plan['selected']), signed)
        if proof is None:
            raise RebuildUnavailable('unverified_source')
        custom = admin_directory(root / 'custom_web')
        if not (custom / 'manifest.json').is_file():
            raise RebuildUnavailable('source_missing')
        fingerprint = custom_source_fingerprint(custom)
        if fingerprint != proof['custom_fingerprint']:
            raise RebuildUnavailable('source_changed')
        try:
            toolchain = require_toolchain(root)
        except (OSError, ValueError):
            raise RebuildUnavailable('toolchain_missing') from None
        workspace = local_path(snapshot, 'ui-rebuild-' + uuid.uuid4().hex, directory=True)
        target_sources(root, target, workspace)
        normalize = lambda path: path.read_bytes().replace(b'\r\n', b'\n')
        if normalize(root / 'web/package-lock.json') != normalize(workspace / 'web/package-lock.json'):
            raise RebuildUnavailable('dependencies_changed')
        for metadata in ('web/package.json', 'web_tools/toolchain.json'):
            installed, selected = root / metadata, workspace / metadata
            if installed.exists() != selected.exists() or installed.exists() and normalize(installed) != normalize(selected):
                raise RebuildUnavailable('dependencies_changed')
        # This is the only intentionally shared build directory. Reuse is local,
        # explicit and tied to the same pinned dependency lock; never run npm ci.
        from web_tools.toolchain import dependencies
        modules = dependencies(root)
        (workspace / 'web/node_modules').symlink_to(modules, target_is_directory=True)
        copied = workspace / 'custom_web'
        if custom_source_fingerprint(custom, copy_to=copied) != fingerprint:
            raise RebuildUnavailable('source_changed')
        before = source_version(workspace)[0]
        candidate = build(workspace, runtime, copied, product_version=target, toolchain=toolchain)
        if (source_version(workspace)[0] != before or custom_source_fingerprint(custom) != fingerprint
                or custom_source_fingerprint(copied) != fingerprint):
            raise RebuildUnavailable('source_changed')
        trust = _json(local_path(runtime, 'identity.json').read_bytes())
        staged = local_path(runtime, 'staging/' + candidate['build_id'])
        package, _ = verify_package(local_path(staged, 'package.zip').read_bytes(), trust,
                                     **verification_versions(plan['capabilities']))
        if (package['manifest']['base_build_id'] != before or package['manifest']['product_version'] != target
                or source_provenance(staged, package) is None):
            raise ValueError('target candidate metadata does not match its sources')
        result['custom_rebuild'] = {'code': 'prepared', 'build_id': candidate['build_id'],
            'base_build_id': before, 'custom_fingerprint': fingerprint}
    except RebuildUnavailable as exc:
        result['custom_rebuild'] = {'code': exc.code}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError):
        # Compiler/path/provider details may contain private source or secrets.
        result['custom_rebuild'] = {'code': 'prebuild_failed'}
    return result


def prepared_candidate(root, plan):
    """Recheck candidate trust and both source fingerprints immediately before use."""
    value = plan.get('custom_rebuild', {'code': 'unverified_source'})
    if not isinstance(value, dict):
        return None, 'prebuild_failed'
    if value.get('code') != 'prepared':
        return None, value.get('code') if value.get('code') in CODES else 'prebuild_failed'
    try:
        if (set(value) != {'code', 'build_id', 'base_build_id', 'custom_fingerprint'}
                or not _BUILD.fullmatch(value['build_id']) or not _HASH.fullmatch(value['base_build_id'])
                or not _HASH.fullmatch(value['custom_fingerprint'])
                or not _COMMIT.fullmatch(plan['target_commit'])):
            raise ValueError('invalid prepared target metadata')
        root = Path(root)
        fingerprint = custom_source_fingerprint(root / 'custom_web')
        if fingerprint != value['custom_fingerprint'] or source_version(root)[0] != value['base_build_id']:
            return None, 'source_changed'
        runtime = local_path(root, 'web_runtime')
        trust = _json(local_path(runtime, 'identity.json').read_bytes())
        stage = local_path(runtime, 'staging/' + value['build_id'])
        content = local_path(stage, 'package.zip').read_bytes()
        signed, _ = verify_package(content, trust, **verification_versions(plan['capabilities']))
        proof = source_provenance(stage, signed)
        if (signed['manifest']['build_id'] != value['build_id']
                or signed['manifest']['base_build_id'] != value['base_build_id']
                or signed['manifest']['product_version'] != plan['target_commit']
                or proof is None or proof['custom_fingerprint'] != fingerprint):
            raise ValueError('prepared target does not match its validated sources')
        if custom_source_fingerprint(root / 'custom_web') != fingerprint or source_version(root)[0] != value['base_build_id']:
            return None, 'source_changed'
        return (signed, content, trust), 'updated'
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'prebuild_failed'


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
                or value['rebuild_required'] != (value['code'] != 'updated')):
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
        'target_commit': target, 'active_build_id': build_id, 'rebuild_required': code != 'updated', 'code': code}))
