"""Closed Python filesystem using the existing compiler's process containment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import sysconfig

from web_tools.compiler_rootfs import (HOST_ENV, _destination, _directory,
                                      _libraries, _trusted_tree, reclaim_output)
from web_tools.compiler_sandbox import _CompilerSandbox, _property_word, SCRATCH_BYTES
from web_tools.paths import atomic_write

ROOT = Path('/run/yadreno-extension-check')
PRIVATE_ROOT = Path('/run/private/yadreno-extension-check')
SOURCE_DIRS = ('bot', 'core', 'database', 'runtime', 'web_api', 'web_tools', 'extension_tools', 'localization')
CONFIG = '''BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
ADMIN_IDS = [91002]
TELEGRAM_API_URL = "http://127.0.0.1:9"
GITHUB_REPO_URL = "https://example.invalid/synthetic/repository.git"
DEFAULT_LIMIT_IP = 1
DEFAULT_TOTAL_GB = 1099511627776
RATE_LIMITS = {"commands_per_minute": 30, "critical_operations_per_minute": 5}
RETRY_CONFIG = {"max_attempts": 3, "delays": [1, 3, 9], "timeout_seconds": 15}
'''


def regular_bytes(path, maximum=2 * 1024 * 1024):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Check inputs and output paths cannot contain symbolic links')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValueError('Check inputs must be bounded regular files without hard links')
    content = path.read_bytes()
    if len(content) > maximum:
        raise ValueError('Check input exceeds size limit')
    return content


class PythonSandbox(_CompilerSandbox):
    unit_prefix = 'yadreno-extension-check'
    unit_description = 'Yadreno extension check'

    def __init__(self, root, extensions, tests, output):
        super().__init__(root, root, None)
        self.folder, self.private_folder = ROOT / self.token, PRIVATE_ROOT / self.token
        self.work, self.public_work = self.private_folder / 'work', self.folder / 'work'
        self.rootfs, self.parent_stage = self.folder / 'rootfs', self.folder / 'parent-stage'
        self.extensions, self.tests, self.output = extensions, tests, output
        self.project, self.copy_custom = self.work / 'project', Path('/project/custom_extensions')
        self.manifest = {}
        self.resources = {}

    def _copy(self, source, target):
        content = regular_bytes(source)
        atomic_write(self.project / target, content, mode=0o644)
        self.manifest[target] = {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}

    def __enter__(self):
        from web_tools.build import admin_directory
        for path in (ROOT, PRIVATE_ROOT.parent, PRIVATE_ROOT):
            admin_directory(path)
            path.mkdir(mode=0o700, exist_ok=True)
            if path.stat().st_mode & 0o077:
                raise ValueError('Check runtime directories must be private')
        self.folder.mkdir(mode=0o700)
        try:
            self.private_folder.mkdir(mode=0o700)
            self.work.mkdir(mode=0o700)
            self.rootfs.mkdir(mode=0o755)
            self.parent_stage.mkdir(mode=0o700)
            subprocess.run([self.commands['mount'], '-t', 'tmpfs', '-o',
                f'size={SCRATCH_BYTES},nr_inodes=16384,mode=0700,nosuid,nodev',
                'yadreno-extension-check', str(self.work)], env=HOST_ENV,
                capture_output=True, check=True, timeout=10)
            self.mounted = True
            for directory in SOURCE_DIRS:
                for path in sorted((self.root / directory).rglob('*.py')):
                    if '__pycache__' not in path.parts:
                        self._copy(path, path.relative_to(self.root).as_posix())
            # Shipped catalogs are source inputs, never installation settings/data.
            for path in sorted((self.root / 'locales').rglob('*.json')):
                self._copy(path, path.relative_to(self.root).as_posix())
            # Newer database modules import this stdlib-only application helper.
            # Historical snapshots without that dependency remain checkable.
            if (self.root / 'backup_io.py').exists():
                self._copy(self.root / 'backup_io.py', 'backup_io.py')
            for path in self.extensions:
                self._copy(path, 'custom_extensions/' + path.name)
            for index, path in enumerate(self.tests):
                self._copy(path, f'scenarios/check_{index}.py')
            atomic_write(self.project / 'config.py', CONFIG.encode(), mode=0o644)
            for name in ('custom_extensions', 'scenarios'):
                (self.project / name).mkdir(exist_ok=True)
            atomic_write(self.project / 'check-input.json', json.dumps({
                'tests': [f'scenarios/check_{i}.py' for i in range(len(self.tests))],
                'extensions': [path.name for path in self.extensions],
                'sources': self.manifest,
            }).encode(), mode=0o644)
            for directory, _, _ in os.walk(self.project):
                Path(directory).chmod(0o755)
            for name in ('files', 'state', 'home', 'tmp', 'var-tmp', 'shm'):
                (self.work / name).mkdir(mode=0o700)
            self._python_filesystem()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _python_filesystem(self):
        python = Path(sys.executable).resolve(strict=True)
        stdlib = Path(sysconfig.get_path('stdlib')).resolve(strict=True)
        dependencies, native = _trusted_tree(Path(sysconfig.get_path('purelib')))
        # Standard-library native modules are trusted installation dependencies.
        native += list((stdlib / 'lib-dynload').glob('*.so'))
        libraries = _libraries([python, Path(self.commands['env']), *native], self.commands['ldd'])
        self.bindings = [(str(python), '/toolchain/python'), (str(stdlib), str(stdlib)),
                         (str(dependencies), '/dependencies'), (str(self.project), '/project'),
                         (self.commands['env'], '/usr/bin/env')]
        self.bindings += [(str(path.resolve()), str(path)) for path in libraries]
        for source, destination in self.bindings:
            target = _destination(self.rootfs, destination)
            if Path(source).is_dir():
                _directory(target)
            else:
                target.touch(mode=0o644)
        for name in ('proc', 'dev', 'tmp', 'var', 'var/tmp', 'etc', 'work'):
            _directory(self.rootfs / name)
        atomic_write(self.rootfs / 'etc/passwd', b'root:x:0:0:root:/root:/bin/false\n', mode=0o644)
        atomic_write(self.rootfs / 'etc/group', b'root:x:0:\n', mode=0o644)
        atomic_write(self.rootfs / 'isolation.json', json.dumps({
            name: os.readlink('/proc/self/ns/' + name) for name in ('net', 'ipc')
        }).encode(), mode=0o644)
        _directory(self.rootfs)

    def _properties(self, timeout):
        result = super()._properties(timeout)
        pair = lambda source, target: _property_word(source) + ':' + _property_word(target)
        result.update({
            'WorkingDirectory': '/project',
            'ReadOnlyPaths': ' '.join(_property_word('-+' + str(path)) for path in
                                     (self.work, self.public_work, '/work', '/project', '/dependencies')),
            'ReadWritePaths': '+/work/state +/work/files +/work/home +/tmp +/var/tmp +/dev/shm',
            'BindReadOnlyPaths': ' '.join(pair(a, b) for a, b in self.bindings),
            'BindPaths': ' '.join(pair(str(self.work / name), target) for name, target in
                (('tmp', '/tmp'), ('var-tmp', '/var/tmp'), ('shm', '/dev/shm')))
                + ' ' + pair(self.work, '/work'),
        })
        return result

    def execute(self):
        environment = {'PATH': '/toolchain', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'HOME': '/work/home', 'TMPDIR': '/tmp', 'PYTHONHOME': sys.base_prefix,
            'PYTHONPATH': '/project:/dependencies', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONUNBUFFERED': '1'}
        # Inherited execution owns deadline, bounded output, cgroup stop and verification.
        return self._execute(['/toolchain/python', '-B', '-S', '-m', 'extension_tools.worker'],
                             environment, 180)

    def _stop(self):
        if self.unit is not None:
            try:
                result = subprocess.run([self.commands['systemctl'], 'show', self.unit,
                    '--property=MemoryPeak,MemoryMax,MemorySwapMax,CPUQuotaPerSecUSec,TasksMax,CPUUsageNSec,Result'],
                    env=HOST_ENV, capture_output=True, text=True, timeout=10)
                values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
                # systemd reports manager defaults after a successful transient
                # unit has been collected; those are not run measurements.
                self.resources = values if values.get('MemoryMax') == str(640 * 1024 * 1024) else {
                    'measurement_available': False, 'reason': 'Transient unit already collected'}
            except (OSError, subprocess.SubprocessError) as error:
                self.resources['measurement_error'] = type(error).__name__
            finally:
                super()._stop()

    def export(self):
        self._stop()
        files = reclaim_output(self.work / 'files')
        for path in files:
            relative = path.relative_to(self.work / 'files')
            atomic_write(self.output / relative, path.read_bytes())
        atomic_write(self.output / 'sources.json', json.dumps(self.manifest, indent=2).encode())
        atomic_write(self.output / 'resources.json', json.dumps(self.resources, indent=2).encode())
