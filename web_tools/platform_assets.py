"""System resources are installed independently of custom publication preservation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from web_tools.paths import atomic_write, canonical, local_path
from web_tools.package import digest


def platform_version(root):
    root = Path(root)
    files = list((root / 'web/platform').rglob('*'))
    files += [root / name for name in ('web/package.json', 'web/package-lock.json', 'web/tsconfig.json',
                                     'web/vite.platform.config.ts', 'web_tools/service_worker.js')]
    result = hashlib.sha256()
    for path in sorted((file for file in files if file.is_file()), key=lambda file: file.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix()
        content = local_path(root, name).read_bytes().replace(b'\r\n', b'\n')
        result.update(name.encode()); result.update(content)
    return result.hexdigest()


def install_platform(root, runtime):
    from web_tools.distribution import BUNDLE_PATH, PLATFORM_MARKER, read_distribution
    root, runtime = Path(root), Path(runtime)
    manifest, files = read_distribution(local_path(root, BUNDLE_PATH).read_bytes())
    version = platform_version(root)
    if version != manifest['platform_version']:
        raise ValueError('ready platform does not match the installed system sources')
    inventory = {}
    for name, content in files.items():
        if not name.startswith('platform/'):
            continue
        name = name.removeprefix('platform/')
        content = content.replace(PLATFORM_MARKER.encode(), version.encode())
        target = local_path(runtime, 'platform/' + version + '/' + name)
        if not target.exists() or target.read_bytes() != content:
            atomic_write(target, content)
        inventory[name] = {'sha256': digest(content), 'size': len(content)}
    record = {'version': version, 'files': inventory}
    atomic_write(local_path(runtime, 'platform/' + version + '/inventory.json'), canonical(record))
    atomic_write(local_path(runtime, 'platform-current.json'), canonical(record))
    return record


def current_platform(runtime):
    return json.loads(local_path(runtime, 'platform-current.json').read_bytes())


def platform_resource(runtime, version, name):
    record = json.loads(local_path(runtime, 'platform/' + version + '/inventory.json').read_bytes())
    if record['version'] != version or name not in record['files']:
        raise ValueError('unknown system resource')
    content = local_path(runtime, 'platform/' + version + '/' + name).read_bytes()
    if digest(content) != record['files'][name]['sha256']:
        raise ValueError('corrupted system resource')
    return content
