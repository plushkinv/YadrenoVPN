"""Installation-local pinned compiler inputs, independent of served UI state."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path

from web_tools.editor_files import checked_directory, checked_info, read_regular
from web_tools.paths import PROJECT_ROOT, atomic_write, canonical, local_path, publication_lock

PIN = 'web_tools/toolchain.json'
CACHE = 'web_runtime/cache/toolchains'
_HASH = re.compile(r'[a-f0-9]{64}')


def layout(root):
    root = checked_directory(root)
    pin = json.loads(read_regular(root, PIN))
    if (set(pin) != {'format_version', 'node_version', 'archives'}
            or type(pin['format_version']) is not int or pin['format_version'] != 1
            or not isinstance(pin['node_version'], str)
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', pin['node_version'])
            or not isinstance(pin['archives'], dict)
            or set(pin['archives']) != {'x64', 'arm64'}
            or any(not isinstance(value, str) or not _HASH.fullmatch(value)
                   for value in pin['archives'].values())):
        raise ValueError('invalid compiler toolchain pin')
    architecture = {'x86_64': 'x64', 'aarch64': 'arm64'}.get(platform.machine())
    if sys.platform != 'linux' or not architecture:
        raise ValueError('managed compiler requires supported Linux architecture')
    package = read_regular(root, 'web/package.json')
    lock = read_regular(root, 'web/package-lock.json')
    identity = {'format_version': 1, 'node_version': pin['node_version'],
                'architecture': architecture, 'archive_sha256': pin['archives'][architecture],
                'package_sha256': hashlib.sha256(package).hexdigest(),
                'lock_sha256': hashlib.sha256(lock).hexdigest()}
    key = hashlib.sha256(canonical(identity)).hexdigest()
    base = local_path(root, CACHE)
    return {'root': root, 'base': base, 'kit': local_path(base, key), 'key': key,
            'identity': identity, 'package': package, 'lock': lock}


def _ready(value, *, linked=True):
    kit = value['kit']
    checked_directory(kit, private=True)
    if json.loads(read_regular(kit, 'ready.json')) != value['identity']:
        raise ValueError('compiler inputs do not match the release pin')
    node = local_path(kit, 'node/bin/node')
    checked_info(node.lstat())
    if not os.access(node, os.X_OK):
        raise ValueError('compiler Node is not executable')
    npm = local_path(kit, 'node/lib/node_modules/npm/bin/npm-cli.js')
    checked_info(npm.lstat())
    modules = local_path(kit, 'dependencies/node_modules')
    checked_directory(modules)
    if modules.stat().st_mode & 0o005 != 0o005:
        raise ValueError('compiler dependencies must be readable inside the isolated mount')
    for name in ('vite', 'tsc'):
        path = modules / '.bin' / name
        if not path.resolve(strict=True).is_relative_to(modules):
            raise ValueError('compiler executable leaves its dependency tree')
        checked_info(path.resolve(strict=True).lstat())
    if linked:
        link = value['root'] / 'web/node_modules'
        if not link.is_symlink() or link.resolve(strict=True) != modules:
            raise ValueError('compiler dependency link is not prepared')
    return str(node), str(npm)


def resolve(root):
    """Return the current release's exact tools; never select an ambient version."""
    return _ready(layout(root))


def dependencies(root):
    """Resolve managed inputs; retain the pre-existing local builder contract."""
    root = Path(root)
    if local_path(root, PIN).is_file():
        value = layout(root)
        _ready(value)
        return local_path(value['kit'], 'dependencies/node_modules')
    return checked_directory(root / 'web/node_modules')


def describe(root=PROJECT_ROOT):
    """Bounded installation facts, with no paths, logs or private configuration."""
    try:
        value = layout(root)
        result = {'ready': False, 'node_version': value['identity']['node_version'],
                  'dependency_lock': value['identity']['lock_sha256'], 'code': 'not_prepared'}
        try:
            _ready(value)
        except (OSError, ValueError, TypeError, KeyError):
            return result
        return {**result, 'ready': True, 'code': 'ready'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'ready': False, 'code': 'unavailable'}


def _download(url, target, expected, *, offline):
    if target.exists():
        checked_info(target.lstat())
        checksum = hashlib.sha256()
        with target.open('rb') as existing:
            for block in iter(lambda: existing.read(1024 * 1024), b''):
                checksum.update(block)
        if checksum.hexdigest() == expected:
            return
    if offline:
        raise ValueError('verified Node archive is not cached')
    temporary = target.with_name('.download-' + uuid.uuid4().hex)
    try:
        started, size, checksum = time.monotonic(), 0, hashlib.sha256()
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('xb') as output:
            if not response.url.startswith('https://nodejs.org/'):
                raise ValueError('unexpected Node archive redirect')
            for block in iter(lambda: response.read(1024 * 1024), b''):
                size += len(block)
                if size > 128 * 1024 * 1024 or time.monotonic() - started > 300:
                    raise ValueError('Node archive download exceeds its limits')
                checksum.update(block)
                output.write(block)
        if checksum.hexdigest() != expected:
            raise ValueError('Node archive checksum mismatch')
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _extract(archive, destination, prefix):
    """Verify names and links before extracting a checksum-verified Node archive."""
    seen, links, size = set(), [], 0
    with tarfile.open(archive, 'r:xz') as source:
        for member in source:
            name = member.name.rstrip('/')
            if name == prefix and member.isdir():
                continue
            if not name.startswith(prefix + '/'):
                raise ValueError('unexpected Node archive root')
            relative = name[len(prefix) + 1:]
            target = local_path(destination, relative, hidden=True)
            size += member.size
            if relative in seen or len(seen) >= 25000 or size > 512 * 1024 * 1024:
                raise ValueError('invalid or oversized Node archive')
            seen.add(relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with source.extractfile(member) as body, target.open('xb') as output:
                    shutil.copyfileobj(body, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym():
                links.append((target, member.linkname))
            else:
                raise ValueError('unsupported Node archive member')
    for target, link in links:
        if (not link or '\\' in link or Path(link).is_absolute()
                or not (target.parent / link).resolve(strict=True).is_relative_to(destination.resolve())):
            raise ValueError('Node archive link leaves its tree')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.symlink_to(link)


def _environment(value, stage):
    for name in ('home', 'npm-cache'):
        local_path(value['base'], name, directory=True)
    config = local_path(value['base'], 'user.npmrc')
    global_config = local_path(value['base'], 'global.npmrc')
    atomic_write(config, b'')
    atomic_write(global_config, b'')
    return {'PATH': str(stage / 'node/bin') + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'HOME': str(value['base'] / 'home'), 'NODE_OPTIONS': '--max-old-space-size=512',
            'npm_config_userconfig': str(config), 'npm_config_globalconfig': str(global_config),
            'npm_config_cache': str(value['base'] / 'npm-cache'),
            'npm_config_registry': 'https://registry.npmjs.org/', 'npm_config_update_notifier': 'false'}


def _install(value, stage, *, offline):
    identity = value['identity']
    prefix = 'node-v' + identity['node_version'] + '-linux-' + identity['architecture']
    archive = local_path(value['base'], prefix + '.tar.xz')
    _download('https://nodejs.org/dist/v' + identity['node_version'] + '/' + archive.name,
              archive, identity['archive_sha256'], offline=offline)
    node_root = local_path(stage, 'node', directory=True)
    _extract(archive, node_root, prefix)
    dependencies = local_path(stage, 'dependencies', directory=True)
    atomic_write(dependencies / 'package.json', value['package'])
    atomic_write(dependencies / 'package-lock.json', value['lock'])
    environment = _environment(value, stage)
    node = str(stage / 'node/bin/node')
    actual = subprocess.run([node, '--version'], env=environment, capture_output=True,
                            text=True, check=True, timeout=20).stdout.strip()
    if actual != 'v' + identity['node_version']:
        raise ValueError('compiler Node version mismatch')
    arguments = [node, str(stage / 'node/lib/node_modules/npm/bin/npm-cli.js'),
                 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
    if offline:
        arguments.append('--offline')
    with (stage / 'install.log').open('wb') as log:
        # The enclosing kit remains 0700; its read-only dependency bind must be
        # readable by the compiler's DynamicUser without changing ownership.
        subprocess.run(arguments, cwd=dependencies, env=environment, stdout=log,
                       stderr=log, check=True, timeout=300, umask=0o022)
    if read_regular(dependencies, 'package-lock.json') != value['lock']:
        raise ValueError('dependency installation changed the release lock')
    atomic_write(stage / 'ready.json', canonical(identity))
    _ready({**value, 'kit': stage}, linked=False)


def _attach(value):
    """Keep any old local dependencies, then atomically select the prepared kit."""
    link = value['root'] / 'web/node_modules'
    destination = local_path(value['kit'], 'dependencies/node_modules')
    if link.is_symlink() and link.resolve(strict=True) == destination:
        return False
    previous = None
    if link.is_symlink():
        if not link.resolve(strict=True).is_relative_to(value['base']):
            raise ValueError('existing dependency link is not installation-owned')
    elif link.exists():
        checked_directory(link)
        previous = local_path(value['base'], 'retained-' + uuid.uuid4().hex)
        link.rename(previous)
    temporary = local_path(value['base'], 'link-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(destination, target_is_directory=True)
        os.replace(temporary, link)
    except BaseException:
        if previous is not None and not link.exists() and not link.is_symlink():
            previous.rename(link)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return True


@contextlib.contextmanager
def _preparation_lock(base):
    # Startup repair and a new installer may overlap. Serialize the same kit
    # without turning the unrelated publication lock into a blocking operation.
    deadline = time.monotonic() + 640
    with contextlib.ExitStack() as stack:
        while True:
            try:
                stack.enter_context(publication_lock(base))
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('compiler preparation is still running')
                time.sleep(0.25)
        yield


def prepare(root=PROJECT_ROOT, *, offline=False):
    value = layout(root)
    base = local_path(value['root'], CACHE, directory=True)
    checked_directory(base, private=True)
    with _preparation_lock(base):
        changed = False
        if value['kit'].exists():
            try:
                _ready(value, linked=False)
            except (OSError, ValueError, TypeError, KeyError):
                checked_directory(value['kit'], private=True)
                value['kit'].rename(local_path(base, 'invalid-' + uuid.uuid4().hex))
        if not value['kit'].exists():
            stage = local_path(base, 'pending-' + uuid.uuid4().hex, directory=True)
            try:
                _install(value, stage, offline=offline)
                stage.rename(value['kit'])
                changed = True
            except BaseException:
                log = stage / 'install.log'
                if log.is_file() and not log.is_symlink():
                    with log.open('rb') as source:
                        source.seek(max(0, log.stat().st_size - 32 * 1024))
                        atomic_write(base / 'last-install.log', source.read())
                raise
            finally:
                # Only the private directory created by this attempt is disposable.
                if stage.exists():
                    checked_directory(stage, private=True)
                    if stage.parent != base:
                        raise ValueError('unexpected compiler preparation directory')
                    shutil.rmtree(stage)
        changed = _attach(value) or changed
        _ready(value)
    return {**describe(root), 'changed': changed}


async def prepare_after_start(root=PROJECT_ROOT):
    """Older updaters lack the hook; complete provisioning after core activation.

    One bounded child uses the same preparation/lock as the installer. A ready
    kit spawns nothing. Shutdown cancels the process group, not the running UI.
    """
    process = None
    try:
        if sys.platform != 'linux' or not local_path(root, PIN).is_file() or describe(root)['ready']:
            return
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'web_tools.toolchain', '--root', str(root), 'prepare',
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        result = await asyncio.wait_for(process.wait(), timeout=660)
        if result:
            logging.getLogger(__name__).warning('Optional Web compiler remains unavailable after startup preparation')
    except (OSError, ValueError, asyncio.TimeoutError):
        logging.getLogger(__name__).warning('Optional Web compiler preparation did not finish; runtime remains active')
    finally:
        if process is not None:
            # Stop descendants even if their preparation parent exited early.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('action', choices=('prepare', 'status'))
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    try:
        result = prepare(args.root, offline=args.offline) if args.action == 'prepare' else describe(args.root)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, tarfile.TarError):
        print(json.dumps({'ready': False, 'code': 'preparation_failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
