"""Prepared base assets from a trusted product release, personalized without Node.

The distribution is product source material, not an installation-signed package
or a new trust root. Its hashes detect damage; the installed release supplies
trust. Installation stages an ordinary locally signed UI package for the same
publication/consumer path used by custom builds.
"""
from __future__ import annotations

import io
import re
import os
import subprocess
import uuid
import zipfile
from pathlib import Path

from web_tools.build import admin_directory, compile_files, source_version, require_toolchain
from web_tools.compatibility import bounds, check_requirements, current_capabilities, release_capabilities, verification_versions
from web_tools.package import MAX_BYTES, MAX_FILES, _asset_name, _json, create_package, digest, file_inventory, signing_identity, verify_package
from web_tools.paths import atomic_write, canonical, local_path, relative_name
from web_tools.publication import publication_lock
from web_tools.view_inventory import write_source_provenance

BUNDLE_PATH = 'web/prebuilt/base-ui.zip'
MANIFEST = 'distribution.json'
BUILD_MARKER = '__YADRENO_BASE_BUILD_V1__'
INSTANCE_MARKER = '__YADRENO_BASE_INSTANCE_V1__'
PLATFORM_MARKER = '__YADRENO_PLATFORM_V1__'
TEXT_ASSETS = {'.html', '.js', '.css', '.json', '.svg'}
FIELDS = {'format_version', 'product', 'product_version', 'base_build_id', 'template_version',
          'core_api', 'environment_contract', 'requirements', 'content_hash', 'files', 'platform_version'}


def _capabilities(root):
    path = local_path(root, 'web_tools/compatibility.json')
    # Small isolated source fixtures predate this additive release declaration.
    return release_capabilities(_json(path.read_bytes())) if path.exists() else current_capabilities()


def read_distribution(content, capabilities=None):
    """Validate a bounded archive completely before creating installation state."""
    if len(content) > MAX_BYTES:
        raise ValueError('base UI distribution exceeds resource limits')
    capabilities = release_capabilities(capabilities) if capabilities is not None else current_capabilities()
    versions = verification_versions(capabilities)
    if 3 not in versions['supported_formats']:
        raise ValueError('this release cannot install the prepared base UI format')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(names) != len(set(names)) or len(names) > MAX_FILES + 1
                    or sum(entry.file_size for entry in entries) > MAX_BYTES):
                raise ValueError('duplicate/oversized base UI archive')
            for entry in entries:
                relative_name(entry.filename)
                if (entry.is_dir() or (entry.external_attr >> 16) & 0o170000 not in {0, 0o100000}
                        or entry.flag_bits & 1):
                    raise ValueError('base UI archive requires unencrypted regular files')
            manifest = _json(archive.read(MANIFEST))
            if (not isinstance(manifest, dict) or set(manifest) != FIELDS
                    or type(manifest['format_version']) is not int or manifest['format_version'] != 2
                    or manifest['product'] != 'yadreno-vpn'
                    or type(manifest['template_version']) is not int or manifest['template_version'] != 1
                    or type(manifest['environment_contract']) is not int
                    or manifest['environment_contract'] != versions['environment_version']
                    or not isinstance(manifest['product_version'], str)
                    or not re.fullmatch(r'[A-Za-z0-9._+-]{1,128}', manifest['product_version'])
                    or not isinstance(manifest['base_build_id'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', manifest['base_build_id'])):
                raise ValueError('invalid/incompatible base UI distribution manifest')
            if not isinstance(manifest['platform_version'], str) or not re.fullmatch(r'[a-f0-9]{64}', manifest['platform_version']):
                raise ValueError('invalid platform version')
            bounds(manifest['core_api'], versions['api_version'], 'base UI core API')
            check_requirements(manifest['requirements'], **{key: value for key, value in versions.items()
                                                           if key != 'supported_formats'})
            if manifest['requirements']['modules']:
                raise ValueError('base UI distribution must not contain installation modules')
            inventory = manifest['files']
            if (not isinstance(inventory, dict) or not 1 <= len(inventory) <= MAX_FILES
                    or not {'application/application.json', 'platform/index.html', 'platform/frame.html'}.issubset(inventory)
                    or 'sw.js' in inventory or MANIFEST in inventory
                    or set(names) != set(inventory) | {MANIFEST}
                    or digest(canonical(inventory)) != manifest['content_hash']):
                raise ValueError('incomplete/invalid base UI inventory')
            files = {}
            markers = set()
            for name, info in inventory.items():
                _asset_name(name)
                if not name.startswith(('application/', 'platform/')):
                    raise ValueError('invalid distribution resource owner')
                if (not isinstance(info, dict) or set(info) != {'sha256', 'size'}
                        or type(info['size']) is not int or info['size'] < 0
                        or not isinstance(info['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', info['sha256'])):
                    raise ValueError('invalid base UI asset description')
                data = archive.read(name)
                if len(data) != info['size'] or digest(data) != info['sha256']:
                    raise ValueError('corrupted base UI asset: ' + name)
                found = set(re.findall(rb'__YADRENO_(?:BASE_|PLATFORM_)[A-Z0-9_]+__', data))
                if found and Path(name).suffix not in TEXT_ASSETS:
                    raise ValueError('base UI template markers require a text asset')
                markers.update(found)
                files[name] = data
            if markers != {BUILD_MARKER.encode(), INSTANCE_MARKER.encode(), PLATFORM_MARKER.encode()}:
                raise ValueError('base UI distribution is missing or has unknown template markers')
            from web_tools.application import read_application
            read_application({name.removeprefix('application/'): data for name, data in files.items() if name.startswith('application/')})
            return manifest, files
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise ValueError('invalid/incomplete base UI archive') from exc


def _archive(manifest, files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in [(MANIFEST, canonical(manifest)), *sorted(files.items())]:
            # Fixed metadata makes the archive independent of build time/host paths.
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    return output.getvalue()


def build_base(root, runtime):
    """Compile only stock UI once on the release builder, without signing keys."""
    root, runtime = admin_directory(root), admin_directory(runtime)
    capabilities = _capabilities(root)
    base_hash, product_version = source_version(root)
    stage = local_path(runtime, 'staging/' + uuid.uuid4().hex, directory=True)
    declaration = compile_files(root, runtime, root / 'web', stage,
                                build_id=BUILD_MARKER, instance_id=INSTANCE_MARKER)
    if declaration['requirements']['modules']:
        raise ValueError('base UI compilation unexpectedly loaded customization')
    if source_version(root)[0] != base_hash:
        raise ValueError('frontend sources changed during base UI compilation')
    from web_tools.platform_assets import platform_version
    platform_id = platform_version(root)
    compile_platform(root, stage)
    files = {'application/' + name: local_path(stage / 'files', name).read_bytes() for name in file_inventory(stage / 'files')}
    files.update({'platform/' + name: local_path(stage / 'platform', name).read_bytes()
                  for name in file_inventory(stage / 'platform', required=('index.html', 'frame.html'))})
    inventory = {name: {'sha256': digest(data), 'size': len(data)} for name, data in sorted(files.items())}
    if source_version(root)[0] != base_hash or platform_version(root) != platform_id:
        raise ValueError('platform sources changed during compilation')
    manifest = {'format_version': 2, 'product': 'yadreno-vpn', 'product_version': product_version,
        'base_build_id': base_hash, 'template_version': 1, 'core_api': {'min': 1, 'max': 1},
        'platform_version': platform_id,
        'environment_contract': 1, 'requirements': {'frontend_api': {'min': 1, 'max': 1}, 'modules': []},
        'content_hash': digest(canonical(inventory)), 'files': inventory}
    content = _archive(manifest, files)
    read_distribution(content, capabilities)
    destination = local_path(root, BUNDLE_PATH)
    admin_directory(destination.parent)
    atomic_write(destination, content, mode=0o644)
    return {'bundle': str(destination), 'base_build_id': base_hash, 'content_hash': manifest['content_hash'],
            'size_bytes': len(content), 'activated': False, 'installation_identity_included': False}


def install_base(root, runtime):
    """Prepare a normal signed candidate from shipped assets; never activate it."""
    root, runtime = admin_directory(root), admin_directory(runtime)
    bundle = local_path(root, BUNDLE_PATH)
    if not bundle.is_file():
        raise ValueError('ready base UI is missing from this product release')
    if bundle.stat().st_size > MAX_BYTES:
        raise ValueError('base UI distribution exceeds resource limits')
    capabilities = _capabilities(root)
    manifest, files = read_distribution(bundle.read_bytes(), capabilities)
    base_hash, _ = source_version(root)
    if manifest['base_build_id'] != base_hash:
        raise ValueError('ready base UI does not match this release frontend sources')
    build_id = uuid.uuid4().hex
    stage = local_path(runtime, 'staging/' + build_id, directory=True)
    with publication_lock(runtime):
        key, identity = signing_identity(runtime)
    substitutions = ((BUILD_MARKER.encode(), build_id.encode()),
                     (INSTANCE_MARKER.encode(), identity['instance_id'].encode()))
    for name, data in files.items():
        if not name.startswith('application/'):
            continue
        name = name.removeprefix('application/')
        if Path(name).suffix in TEXT_ASSETS:
            for before, after in substitutions:
                data = data.replace(before, after)
        atomic_write(local_path(stage / 'files', name), data)
    with publication_lock(runtime):
        signed, content = create_package(stage / 'files', key=key, identity=identity,
            product_version=manifest['product_version'], base_build_id=base_hash, build_id=build_id,
            customization_version='base', requirements=manifest['requirements'], core_api=manifest['core_api'])
        verify_package(content, identity, **verification_versions(capabilities))
        atomic_write(stage / 'manifest.json', canonical(signed))
        atomic_write(stage / 'package.zip', content)
        from web_tools.source_tree import archive_bytes, template
        atomic_write(stage / 'sources.zip', archive_bytes(template(root)))
        write_source_provenance(stage, signed, root=root)
    return {'build_id': build_id, 'stage': str(stage), 'content_hash': signed['manifest']['content_hash'],
            'customization_version': 'base', 'activated': False, 'requires_node': False,
            'ownership': {'pages': [], 'components': [], 'navigation': 'base'}}


def compile_platform(root, stage):
    """Release-only build. Custom compilation never ships the system interface."""
    node, npm = require_toolchain(root)
    web = root / 'web'
    config = {'extends': str(web / 'tsconfig.json'), 'compilerOptions': {'types': []},
              'include': [str(web / 'node_modules/vite/client.d.ts'), str(web / 'platform/**/*.ts'), str(web / 'platform/**/*.tsx')],
              'exclude': [str(web / 'platform/app-entry.tsx')]}
    atomic_write(stage / 'platform-tsconfig.json', canonical(config))
    env = {**os.environ, 'YADRENO_PLATFORM_OUT': str(stage / 'platform'),
           'YADRENO_PLATFORM_BASE': '/ui/platform/' + PLATFORM_MARKER + '/',
           'NODE_OPTIONS': '--max-old-space-size=512'}
    env['PATH'] = str(Path(node).parent) + os.pathsep + env.get('PATH', os.defpath)
    with (stage / 'platform-build.log').open('wb') as log:
        for args in (['tsc', '--project', str(stage / 'platform-tsconfig.json')],
                     ['vite', 'build', '--config', 'vite.platform.config.ts']):
            subprocess.run([npm, 'exec', '--no', '--', *args], cwd=web, env=env,
                           check=True, stdout=log, stderr=log, timeout=180)
