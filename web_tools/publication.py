"""Verify complete publications before atomically switching a local UI pointer."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from web_tools.package import verify_package
from web_tools.paths import atomic_write, canonical, local_path, publication_lock, source_provenance


def read_pointer(runtime):
    path = local_path(runtime, 'active.json')
    return json.loads(path.read_bytes()) if path.exists() else {'current': None, 'previous': None}


def manifest_path(runtime, build_id):
    if not isinstance(build_id, str) or not re.fullmatch(r'[a-f0-9]{32}', build_id):
        raise ValueError('invalid publication identity')
    return local_path(runtime, 'publications/' + build_id + '/manifest.json')


def activate(runtime, content, trust, *, api_version=1, environment_version=1, frontend_version=1,
             module_api_version=1, supported_formats=(3,), expected_pointer=None):
    signed, files = verify_package(content, trust, api_version=api_version, environment_version=environment_version,
                                  frontend_version=frontend_version, module_api_version=module_api_version,
                                  supported_formats=supported_formats)
    with publication_lock(runtime):
        if expected_pointer is not None and read_pointer(runtime) != expected_pointer:
            raise ValueError('UI publication changed after validation')
        return _activate_verified(runtime, content, trust, signed, files)


def _activate_verified(runtime, content, trust, signed, files):
    manifest = signed['manifest']
    runtime = Path(runtime)
    identity_path = local_path(runtime, 'identity.json')
    if identity_path.exists() and json.loads(identity_path.read_bytes()) != trust:
        raise ValueError('consumer trust differs from the pinned installation identity')
    versions = local_path(runtime, 'publications', directory=True)
    directory = local_path(versions, manifest['build_id'])
    provenance = source_provenance(local_path(runtime, 'staging/' + manifest['build_id']), signed)
    source_archive = local_path(runtime, 'staging/' + manifest['build_id'] + '/sources.zip')
    source_content = source_archive.read_bytes() if source_archive.exists() else None
    if source_content is not None:
        from web_tools.source_tree import read_archive, fingerprint
        source_files = read_archive(source_content)
        if provenance is None or fingerprint(source_files) != provenance['custom_fingerprint']:
            raise ValueError('publication sources do not match the verified candidate')
    if directory.exists():
        if manifest_path(runtime, manifest['build_id']).read_bytes() != canonical(signed):
            raise ValueError('publication identity collision')
        # Repeated activation must also verify material already on disk.
        if any(local_path(directory / 'files', name).read_bytes() != data for name, data in files.items()):
            raise ValueError('existing publication is corrupted')
        atomic_write(local_path(directory, 'package.zip'), content)
        if provenance is not None and not local_path(directory, 'source.json').exists():
            atomic_write(local_path(directory, 'source.json'), canonical(provenance))
        if source_content is not None:
            atomic_write(local_path(directory, 'sources.zip'), source_content)
    else:
        temporary = Path(tempfile.mkdtemp(prefix='.ui-', dir=versions))
        try:
            for name, data in files.items():
                atomic_write(local_path(temporary / 'files', name), data)
            atomic_write(temporary / 'manifest.json', canonical(signed))
            atomic_write(temporary / 'package.zip', content)
            if provenance is not None:
                atomic_write(temporary / 'source.json', canonical(provenance))
            if source_content is not None:
                atomic_write(temporary / 'sources.zip', source_content)
            os.replace(temporary, directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    package = local_path(runtime, 'packages/' + manifest['content_hash'] + '.zip')
    atomic_write(package, content)
    if not identity_path.exists():
        # Persist the independently supplied public trust, never package-provided trust.
        atomic_write(identity_path, canonical(trust))
    previous = read_pointer(runtime)
    if previous['current'] != manifest['build_id']:
        atomic_write(runtime / 'active.json', canonical({'current': manifest['build_id'], 'previous': None}))
        if previous['current']:
            retired = manifest_path(runtime, previous['current']).parent / 'retired.json'
            atomic_write(retired, canonical({'retired_at': time.time()}))
    (directory / 'retired.json').unlink(missing_ok=True)
    return {'build_id': manifest['build_id'], 'content_hash': manifest['content_hash'], 'changed': previous['current'] != manifest['build_id']}


def cleanup(runtime, *, now=None, retention_days=7):
    """Keep the current snapshot, recent public assets, and temporary candidates."""
    runtime = Path(runtime)
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400
    with publication_lock(runtime):
        current = read_pointer(runtime)['current']
        keep_hash = None
        if current:
            keep_hash = json.loads(manifest_path(runtime, current).read_bytes())['manifest']['content_hash']
        for group in ('publications', 'staging'):
            parent = local_path(runtime, group)
            if not parent.exists():
                continue
            for entry in parent.iterdir():
                if not re.fullmatch(r'[a-f0-9]{32}', entry.name) or entry.name == current:
                    continue
                entry = local_path(parent, entry.name)
                if group == 'publications':
                    retired = entry / 'retired.json'
                    if not retired.exists():
                        atomic_write(retired, canonical({'retired_at': now}))
                    stamp = json.loads(retired.read_bytes())['retired_at']
                    for name in ('package.zip', 'sources.zip', 'source.json'):
                        local_path(entry, name).unlink(missing_ok=True)
                else:
                    stamp = entry.stat().st_mtime
                if stamp < cutoff:
                    shutil.rmtree(entry)
        packages = local_path(runtime, 'packages')
        if packages.exists():
            for entry in packages.iterdir():
                if re.fullmatch(r'[a-f0-9]{64}\.zip', entry.name) and entry.stem != keep_hash:
                    local_path(packages, entry.name).unlink()
