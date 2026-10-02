"""Verify complete publications before atomically switching a local UI pointer."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
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
             module_api_version=1, supported_formats=(1, 2), expected_pointer=None):
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
    if directory.exists():
        if manifest_path(runtime, manifest['build_id']).read_bytes() != canonical(signed):
            raise ValueError('publication identity collision')
        # Repeated activation must also verify material already on disk.
        if any(local_path(directory / 'files', name).read_bytes() != data for name, data in files.items()):
            raise ValueError('existing publication is corrupted')
        atomic_write(local_path(directory, 'package.zip'), content)
        if provenance is not None and not local_path(directory, 'source.json').exists():
            atomic_write(local_path(directory, 'source.json'), canonical(provenance))
    else:
        temporary = Path(tempfile.mkdtemp(prefix='.ui-', dir=versions))
        try:
            for name, data in files.items():
                atomic_write(local_path(temporary / 'files', name), data)
            atomic_write(temporary / 'manifest.json', canonical(signed))
            atomic_write(temporary / 'package.zip', content)
            if provenance is not None:
                atomic_write(temporary / 'source.json', canonical(provenance))
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
        atomic_write(runtime / 'active.json', canonical({'current': manifest['build_id'], 'previous': previous['current']}))
    return {'build_id': manifest['build_id'], 'content_hash': manifest['content_hash'], 'changed': previous['current'] != manifest['build_id']}


def rollback(runtime, trust):
    with publication_lock(runtime):
        previous = read_pointer(runtime)['previous']
        if not previous:
            raise ValueError('no previous UI publication')
        selected = json.loads(manifest_path(runtime, previous).read_bytes())
        manifest = selected['manifest']
        if manifest['build_id'] != previous:
            raise ValueError('rollback publication identity mismatch')
        package = local_path(runtime, 'publications/' + previous + '/package.zip')
        if not package.exists():
            # Older publications retained only the content-addressed HTTP package.
            package = local_path(runtime, 'packages/' + manifest['content_hash'] + '.zip')
        content = package.read_bytes()
        signed, files = verify_package(content, trust)
        if signed != selected:
            raise ValueError('rollback package does not match the selected publication')
        return _activate_verified(runtime, content, trust, signed, files)
