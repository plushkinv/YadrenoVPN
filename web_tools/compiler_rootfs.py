"""Minimal filesystem inputs for the internal Linux UI compiler.

Only trusted, pinned toolchain binaries are inspected with ldd. Editor source
files never select programs, libraries, systemd properties or bind mounts.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from web_tools.build import custom_inventory_fingerprint
from web_tools.editor_files import checked_directory, read_regular, scan_sources
from web_tools.errors import CompilerEnvironmentError
from web_tools.package import MAX_BYTES, MAX_FILES, digest
from web_tools.paths import atomic_write, local_path

HOST_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
NODE = '/toolchain/bin/node'
NPM = '/toolchain/bin/npm'


def prerequisites():
    """Fail closed before creating a sandbox on unsupported installations."""
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise CompilerEnvironmentError('UI compilation requires a root-managed Linux systemd installation.')
    commands = {name: shutil.which(name, path=HOST_ENV['PATH'])
                for name in ('systemd-run', 'systemctl', 'mount', 'umount', 'ldd', 'env')}
    if not all(commands.values()) or not Path('/run/systemd/system').is_dir():
        raise CompilerEnvironmentError('UI compilation requires running systemd and the systemd-run, systemctl, mount, umount, ldd and env utilities.')
    try:
        version = subprocess.run([commands['systemd-run'], '--version'], env=HOST_ENV,
                                 capture_output=True, text=True, check=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        raise CompilerEnvironmentError('The installed systemd version could not be checked.') from None
    match = re.match(r'systemd (\d+)', version)
    if not match:
        raise CompilerEnvironmentError('The installed systemd version could not be recognized.')
    if int(match[1]) < 249:
        raise CompilerEnvironmentError(f'UI compilation requires systemd 249 or newer; installed version: {int(match[1])}.')
    controllers = Path('/sys/fs/cgroup/cgroup.controllers')
    try:
        available = set(controllers.read_text().split())
    except OSError:
        available = set()
    if not {'cpu', 'memory', 'pids'} <= available:
        raise CompilerEnvironmentError('UI compilation requires CPU, memory and PID cgroup v2 controllers.')
    return commands


def _directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    # The enclosing host task directory is root-only. Namespace directories must
    # remain traversable after chroot by the transient DynamicUser.
    path.chmod(0o755)


def _destination(rootfs, absolute):
    path = rootfs / Path(absolute).relative_to('/')
    for parent in reversed(path.parent.parents):
        if parent == rootfs or parent.is_relative_to(rootfs):
            _directory(parent)
    _directory(path.parent)
    return path


def _trusted_tree(root):
    """Allow only dependency-internal links; never bind a package's escape."""
    root = Path(root).resolve(strict=True)
    native = []
    for directory, subdirs, files in os.walk(root, followlinks=False):
        # Vite's generated files are hidden by a separate writable scratch bind.
        if Path(directory) == root:
            subdirs[:] = [name for name in subdirs if name != '.vite-temp']
        for name in [*subdirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError('compiler dependencies must not link outside their pinned tree')
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('compiler dependencies require regular local files')
            with path.open('rb') as source:
                if source.read(4) == b'\x7fELF':
                    native.append(path)
    return root, native


def _libraries(binaries, ldd):
    libraries = set()
    for binary in binaries:
        result = subprocess.run([ldd, str(binary)], env=HOST_ENV, capture_output=True,
                                text=True, timeout=10)
        if 'not found' in result.stdout:
            raise ValueError('incomplete compiler shared-library dependencies')
        if result.returncode and not any(text in result.stdout + result.stderr
                                        for text in ('not a dynamic executable', 'statically linked')):
            raise ValueError('cannot inspect compiler shared-library dependencies')
        for line in result.stdout.splitlines():
            match = re.search(r'(?:=>\s*)?(/[^\s]+)\s+\(0x', line)
            if match:
                libraries.add(Path(match[1]))
    return sorted(libraries)


def _native_for_libc(root, binaries, libc):
    """Npm may install both GNU and musl optional packages on the same CPU."""
    selected = []
    for binary in binaries:
        directory = binary.parent
        while directory.is_relative_to(root):
            metadata = directory / 'package.json'
            if metadata.is_file():
                choices = json.loads(metadata.read_bytes()).get('libc')
                if isinstance(choices, str):
                    choices = [choices]
                if choices is not None:
                    if not isinstance(choices, list) or any(not isinstance(value, str) for value in choices):
                        raise ValueError('invalid compiler dependency libc declaration')
                    positive = [value for value in choices if not value.startswith('!')]
                    if '!' + libc in choices or positive and libc not in positive and 'any' not in positive:
                        break
                selected.append(binary)
                break
            if directory == root:
                selected.append(binary)
                break
            directory = directory.parent
    return selected


def prepare_rootfs(rootfs, project, dependencies, toolchain, commands):
    """Return closed read-only binds; do not expose an installation directory."""
    node, npm = (Path(path).resolve(strict=True) for path in toolchain)
    if npm.name != 'npm-cli.js' or npm.parent.name != 'bin':
        raise ValueError('isolated UI compilation requires the standard pinned npm CLI')
    npm_root, npm_native = _trusted_tree(npm.parent.parent)
    dependencies, native = _trusted_tree(dependencies)
    programs = {NODE: node, '/usr/bin/env': Path(commands['env']).resolve(strict=True),
                '/bin/sh': Path('/bin/sh').resolve(strict=True)}
    for source in programs.values():
        if not stat.S_ISREG(source.stat().st_mode):
            raise ValueError('compiler toolchain programs must be regular files')
    node_libraries = _libraries([node], commands['ldd'])
    # Derive the ABI from the selected trusted Node's resolved dependencies,
    # rather than ignoring ldd errors from every optional native package.
    if any(path.name == 'libc.so.6' for path in node_libraries):
        libc = 'glibc'
    elif any(path.name.startswith(('libc.musl-', 'ld-musl-')) for path in node_libraries):
        libc = 'musl'
    else:
        raise ValueError('cannot determine the compiler Node libc')
    native = _native_for_libc(dependencies, native, libc)
    npm_native = _native_for_libc(npm_root, npm_native, libc)
    bindings = [(str(source), destination) for destination, source in programs.items()]
    bindings += [(str(npm_root), '/toolchain/npm'), (str(dependencies), str(project / 'web/node_modules'))]
    libraries = set(node_libraries) | set(_libraries(
        [programs['/usr/bin/env'], programs['/bin/sh'], *native, *npm_native], commands['ldd']))
    bindings += [(str(path.resolve(strict=True)), str(path)) for path in sorted(libraries)]
    for source, destination in bindings:
        target = _destination(rootfs, destination)
        if Path(source).is_dir():
            _directory(target)
        else:
            target.touch(mode=0o644)
    _destination(rootfs, NPM).symlink_to('../npm/bin/npm-cli.js')
    for directory in ('proc', 'sys', 'dev', 'tmp', 'var', 'var/tmp', 'etc'):
        _directory(rootfs / directory)
    # Never import the host's passwd, npmrc, DNS, environment or signing files.
    atomic_write(rootfs / 'etc/passwd', b'root:x:0:0:root:/root:/bin/sh\n', mode=0o644)
    atomic_write(rootfs / 'etc/group', b'root:x:0:\n', mode=0o644)
    _directory(rootfs)
    return bindings


def snapshot_sources(root, custom, work):
    """Use the editor's bounded source reader, preserving its owner checks."""
    checked_directory(root)
    project = work / 'project'
    web = project / 'web'
    stock = scan_sources(local_path(root, 'web/src'))
    for name, content in stock.items():
        atomic_write(web / 'src' / name, content, mode=0o644)
    for directory, names in (
        ('web', ['package.json', 'package-lock.json', 'vite.app.config.ts', 'customization.mjs',
                 'index.html', 'preview.html', 'manifest.json', *[p.name for p in (root / 'web').glob('tsconfig*.json')]]),
        ('web_tools', ['service_worker.js', 'compatibility.json', 'toolchain.json']),
    ):
        for name in names:
            path = local_path(root, directory + '/' + name)
            if path.exists():
                atomic_write(project / directory / name, read_regular(root / directory, name), mode=0o644)
    if (root / 'web/public').exists():
        for name, content in scan_sources(root / 'web/public').items():
            atomic_write(web / 'public' / name, content, mode=0o644)
    copied_custom = work / 'custom_web'
    if custom.exists():
        copied_custom.mkdir(mode=0o700)
        for name, content in scan_sources(custom).items():
            atomic_write(copied_custom / name, content)
    for directory in ('files', 'runtime/cache', 'tmp', 'var-tmp', 'shm', 'home', 'vite-temp'):
        (work / directory).mkdir(parents=True, mode=0o700)
    # All stock input files are readable, but protected by read-only mounts.
    for directory, _, _ in os.walk(project):
        Path(directory).chmod(0o755)
    (web / 'node_modules').mkdir(mode=0o755)
    return project, copied_custom


def custom_revision(custom):
    """Use the existing fingerprint schema with the editor's bounded scan."""
    if custom.is_symlink():
        raise ValueError('custom compiler inputs must not be symbolic links')
    if not custom.exists():
        return None
    return custom_inventory_fingerprint({name: {'sha256': digest(content), 'size': len(content)}
                                         for name, content in scan_sources(custom).items()})


def reclaim_output(folder):
    """After all processes exit, validate before chown/read; never follow links."""
    pending = [Path(folder)]
    files = []
    size = directories = 0
    while pending:
        directory = pending.pop()
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('compiler output requires regular directories')
        os.chown(directory, 0, 0, follow_symlinks=False)
        directory.chmod(0o700)
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories += 1
                    if directories > MAX_FILES:
                        raise ValueError('compiler output exceeds directory limits')
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    size += info.st_size
                    if len(files) >= MAX_FILES or size > MAX_BYTES:
                        raise ValueError('compiler output exceeds package limits')
                    files.append(Path(entry.path))
                else:
                    raise ValueError('compiler output requires regular files without links')
    for path in files:
        os.chown(path, 0, 0, follow_symlinks=False)
        path.chmod(0o600)
    return files
