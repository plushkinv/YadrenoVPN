"""Internal, opt-in systemd runner for the existing UI compiler pipeline.

This module neither executes editor tasks nor signs/publishes UI packages. Its
caller owns task locking and revision binding. The ordinary local CLI is intact;
editor compilation must call compile_isolated and cannot fall back to host exec.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from web_tools.build import (admin_directory, compile_files,
                             require_toolchain, source_version)
from web_tools.compiler_rootfs import (HOST_ENV, NODE, NPM, custom_revision, prepare_rootfs, prerequisites,
                                      reclaim_output, snapshot_sources)
from web_tools.editor_files import read_regular
from web_tools.package import file_inventory
from web_tools.paths import atomic_write, local_path
from web_tools.errors import WebSourceError

# These are compiler containment limits, not an agent latency or W2 performance
# SLO. The existing 180 s subprocess limit and 512 MiB Node heap are preserved.
MEMORY_BYTES = 640 * 1024 * 1024
SCRATCH_BYTES = 128 * 1024 * 1024
STREAM_BYTES = 4 * 1024 * 1024
TASKS = 64
RUN_ROOT = Path('/run/yadreno-ui-compile')
PRIVATE_RUN_ROOT = RUN_ROOT.parent / 'private' / RUN_ROOT.name


class IsolatedCompileError(WebSourceError):
    """Bounded compiler diagnostics using source-relative, non-host paths."""

    def __init__(self, message, *, code='web_build_failed', file=None):
        super().__init__(code, message, file=file)


def _property_word(value):
    # systemd's property parser, not a shell. All mount paths are core-generated.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def _guard(work):
    """Refuse silent kernel degradation before loading any editor input."""
    host_net = os.readlink('/proc/self/ns/net')
    host_ipc = os.readlink('/proc/self/ns/ipc')
    return ("const fs = require('node:fs');\n"
            "const fail = () => { throw new Error('UI compiler isolation is unavailable'); };\n"
            "const status = fs.readFileSync('/proc/self/status', 'utf8');\n"
            "if (process.getuid() === 0 || !/^CapEff:\\s+0+$/m.test(status) || "
            "!/^NoNewPrivs:\\s+1$/m.test(status)) fail();\n"
            "if (fs.existsSync('/proc/1/status') || fs.existsSync('/proc/sys')) fail();\n"
            "for (const path of ['/run/dbus/system_bus_socket', '/run/systemd/private', '/run/user']) "
            "if (fs.existsSync(path)) fail();\n"
            "if (fs.readlinkSync('/proc/self/ns/net') === " + json.dumps(host_net) + ") fail();\n"
            "if (fs.readlinkSync('/proc/self/ns/ipc') === " + json.dumps(host_ipc) + ") fail();\n"
            "const mounts = fs.readFileSync('/proc/self/mountinfo', 'utf8');\n"
            "if (!mounts.split('\\n').some(line => line.includes(' /proc ') && "
            "/hidepid=(?:2|invisible)/.test(line) && line.includes('subset=pid'))) fail();\n"
            "for (const path of ['/tmp', '/var/tmp', '/dev/shm']) "
            "if (fs.statSync(path).dev !== fs.statSync(" + json.dumps(str(work)) + ").dev) fail();\n").encode()


class _CompilerSandbox:
    """One compilation's private snapshots and three sequential transient units."""

    def __init__(self, root, custom, toolchain):
        self.root, self.custom = root, custom
        self.toolchain = toolchain
        self.commands = prerequisites()
        self.token = uuid.uuid4().hex
        self.folder = RUN_ROOT / self.token
        self.private_folder = PRIVATE_RUN_ROOT / self.token
        self.work = self.private_folder / 'work'
        self.public_work = self.folder / 'work'
        self.rootfs = self.folder / 'rootfs'
        self.parent_stage = self.folder / 'parent-stage'
        self.unit = None
        self.mounted = False
        self.calls = 0
        self.output_bytes = 0

    def __enter__(self):
        for prefix in (RUN_ROOT, PRIVATE_RUN_ROOT.parent, PRIVATE_RUN_ROOT):
            admin_directory(prefix)
            prefix.mkdir(mode=0o700, exist_ok=True)
            if prefix.stat().st_mode & 0o077:
                raise ValueError('compiler runtime directory must be private')
        self.folder.mkdir(mode=0o700)
        try:
            self.private_folder.mkdir(mode=0o700)
            self.work.mkdir(mode=0o700)
            self.rootfs.mkdir(mode=0o755)
            self.parent_stage.mkdir(mode=0o700)
            # Preserved DynamicUser runtime directories live under /run/private.
            # Creating the canonical location avoids systemd trying to rename a
            # mounted tmpfs. It creates only the public host symlink itself.
            subprocess.run([self.commands['mount'], '-t', 'tmpfs', '-o',
                            f'size={SCRATCH_BYTES},nr_inodes=16384,mode=0700,nosuid,nodev',
                            'yadreno-ui-compile', str(self.work)], env=HOST_ENV, check=True,
                           capture_output=True, timeout=10)
            self.mounted = True
            self.project, self.copy_custom = snapshot_sources(self.root, self.custom, self.work)
            self.bindings = prepare_rootfs(self.rootfs, self.project, self.root / 'web/node_modules',
                                           self.toolchain, self.commands)
            atomic_write(self.rootfs / 'toolchain/compiler-guard.cjs', _guard(self.work), mode=0o644)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _properties(self, timeout):
        readonly = [self.work, self.public_work, self.project, self.copy_custom]
        # With RootDirectory, systemd binds the private runtime storage at the
        # public namespace path. Keep that alias entirely read-only, and bind
        # the canonical path used by compile_files with only narrow RW children.
        writable = [(self.work / 'tmp', '/tmp'), (self.work / 'var-tmp', '/var/tmp'),
                    (self.work / 'vite-temp', str(self.project / 'web/node_modules/.vite-temp')),
                    (self.work / 'files', str(self.parent_stage / 'files')),
                    (self.work / 'shm', '/dev/shm'), (self.work, self.work)]
        pairs = lambda items: ' '.join(_property_word(source) + ':' + _property_word(target)
                                       for source, target in items)
        return {
            'Type': 'exec', 'RootDirectory': str(self.rootfs), 'MountAPIVFS': 'yes',
            'WorkingDirectory': str(self.project / 'web'), 'DynamicUser': 'yes',
            'RuntimeDirectory': self.public_work.relative_to('/run').as_posix(),
            'RuntimeDirectoryMode': '0700', 'RuntimeDirectoryPreserve': 'yes',
            'PrivateNetwork': 'yes', 'PrivateIPC': 'yes', 'PrivateDevices': 'yes', 'PrivateTmp': 'yes',
            'ProtectProc': 'invisible', 'ProcSubset': 'pid',
            'NoNewPrivileges': 'yes', 'CapabilityBoundingSet': '', 'AmbientCapabilities': '',
            'ProtectSystem': 'strict', 'ProtectHome': 'read-only',
            'ProtectKernelTunables': 'yes', 'ProtectKernelModules': 'yes',
            'ProtectKernelLogs': 'yes', 'ProtectControlGroups': 'yes',
            'RestrictSUIDSGID': 'yes', 'RestrictRealtime': 'yes', 'RestrictNamespaces': 'yes',
            'LockPersonality': 'yes', 'RemoveIPC': 'yes', 'UMask': '0077',
            'LimitCORE': '0', 'NotifyAccess': 'none',
            'ReadOnlyPaths': ' '.join(_property_word('-+' + str(path)) for path in readonly),
            'ReadWritePaths': ' '.join(_property_word('+' + str(path)) for path in
                                     [*[self.work / name for name in
                                        ('files', 'runtime/cache', 'home', 'tmp', 'var-tmp', 'shm', 'vite-temp')],
                                      '/tmp', '/var/tmp', '/dev/shm', writable[2][1], writable[3][1]]),
            'BindReadOnlyPaths': pairs([*self.bindings,
                ('-' + str(self.parent_stage / 'tsconfig.json'), str(self.parent_stage / 'tsconfig.json'))]),
            'BindPaths': pairs(writable),
            # /run is created inside the closed rootfs, not bound from the host.
            # Masking its absent children breaks namespace setup on systemd 255;
            # the mandatory pre-input guard verifies privileged sockets are absent.
            'InaccessiblePaths': '-+/sys',
            'MemoryMax': str(MEMORY_BYTES), 'MemorySwapMax': '0', 'TasksMax': str(TASKS),
            'CPUQuota': '100%', 'OOMPolicy': 'kill', 'RuntimeMaxSec': str(timeout),
            'TimeoutStartSec': '10', 'TimeoutStopSec': '2', 'KillMode': 'control-group',
            'SendSIGKILL': 'yes', 'Restart': 'no',
        }

    def _environment(self, requested):
        required = ('YADRENO_CUSTOM_WEB', 'YADRENO_UI_OUT', 'YADRENO_VITE_CACHE',
                    'YADRENO_UI_BUILD', 'YADRENO_UI_BASE', 'YADRENO_UI_INSTANCE')
        environment = {name: str(requested[name]) for name in required}
        if 'YADRENO_UI_CUSTOMIZATION' in requested:
            environment['YADRENO_UI_CUSTOMIZATION'] = requested['YADRENO_UI_CUSTOMIZATION']
        environment.update(PATH='/toolchain/bin:/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8',
                           HOME=str(self.work / 'home'), TMPDIR='/tmp',
                           NODE_OPTIONS='--max-old-space-size=512 --require=/toolchain/compiler-guard.cjs',
                           npm_config_cache=str(self.work / 'runtime/cache/npm'),
                           npm_config_offline='true', npm_config_update_notifier='false',
                           npm_config_audit='false', npm_config_fund='false')
        return environment

    def _stop(self):
        if self.unit is None:
            return
        unit = self.unit
        # Stop waits for all children, including detached descendants. Do not
        # export files or unmount scratch if this boundary cannot be confirmed.
        subprocess.run([self.commands['systemctl'], 'stop', unit], env=HOST_ENV,
                       capture_output=True, timeout=10)
        state = subprocess.run([self.commands['systemctl'], 'show', unit,
                                '--property=LoadState,ActiveState,MainPID'], env=HOST_ENV,
                               capture_output=True, text=True, timeout=10)
        values = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
        if (values.get('LoadState') != 'not-found'
                and (state.returncode or values.get('ActiveState') not in {'inactive', 'failed'}
                     or values.get('MainPID') != '0')):
            raise ValueError('compiler process group did not stop')
        events = Path('/sys/fs/cgroup/system.slice') / unit / 'cgroup.events'
        if events.exists() and 'populated 1' in events.read_text():
            raise ValueError('compiler descendants are still running')
        subprocess.run([self.commands['systemctl'], 'reset-failed', unit], env=HOST_ENV,
                       capture_output=True, timeout=10)
        self.unit = None

    def _execute(self, args, environment, timeout):
        self.calls += 1
        self.unit = f'yadreno-ui-compile-{self.token}-{self.calls}.service'
        # A previous DynamicUser may be reused. Reset only this protected scratch
        # root so RuntimeDirectory also chowns newly written root-owned files.
        os.chown(self.work, 0, 0)
        config = self.parent_stage / 'tsconfig.json'
        if config.exists():
            config.chmod(0o644)
        properties = self._properties(timeout)
        command = [self.commands['systemd-run'], '--system', '--quiet', '--wait', '--pipe',
                   '--no-ask-password', '--slice=system.slice',
                   '--unit=' + self.unit, '--description=Yadreno UI compiler']
        command += ['--property=' + name + '=' + value for name, value in properties.items()]
        # systemd 249 expands command arguments too. Its documented $$ escape
        # preserves literal values without the switch introduced in systemd 254.
        arguments = ['/usr/bin/env', '-i', *[name + '=' + value for name, value in environment.items()], *args]
        command += ['--', *[argument.replace('$', '$$') for argument in arguments]]
        process = None
        buffers = [bytearray(), bytearray()]
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=HOST_ENV, close_fds=True)
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 0)
                selector.register(process.stderr, selectors.EVENT_READ, 1)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(args, timeout, output=bytes(buffers[0]), stderr=bytes(buffers[1]))
                    for key, _ in selector.select(timeout=min(0.2, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            buffers[key.data].extend(chunk)
                            self.output_bytes += len(chunk)
                            if self.output_bytes > STREAM_BYTES:
                                raise ValueError('compiler diagnostic output exceeds resource limits')
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()
            self._stop()
        return subprocess.CompletedProcess(args, returncode, bytes(buffers[0]), bytes(buffers[1]))

    def run(self, args, *, cwd, env, timeout, capture_output=False, text=False,
            check=False, stdout=None, stderr=None):
        """The narrow subprocess.run seam used solely by compile_files."""
        if Path(cwd) != self.project / 'web' or args[0] not in {NODE, NPM} or not 0 < timeout <= 180:
            raise ValueError('unexpected isolated compiler invocation')
        result = self._execute(args, self._environment(env), timeout)
        if stdout is not None:
            stdout.write(result.stdout)
        if stderr is not None:
            stderr.write(result.stderr)
        if text:
            result.stdout = result.stdout.decode('utf-8', errors='replace')
            result.stderr = result.stderr.decode('utf-8', errors='replace')
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
        return result

    def __exit__(self, *_):
        self._stop()
        if self.mounted:
            subprocess.run([self.commands['umount'], str(self.work)], env=HOST_ENV,
                           capture_output=True, check=True, timeout=10)
            self.mounted = False
        if self.private_folder.exists():
            shutil.rmtree(self.private_folder)
        if self.folder.exists():
            shutil.rmtree(self.folder)


def compile_isolated(root, runtime, custom, stage, *, build_id, instance_id, toolchain=None, customization_version=None):
    """Compile into an empty parent-owned stage; never sign, activate or fallback.

    The trusted caller supplies installation/task paths and holds its task lock.
    On return every compiler cgroup has stopped and the copied files pass the
    existing package inventory validation. Signing remains the caller's job.
    """
    root, runtime, custom, stage = map(admin_directory, (root, runtime, custom, stage))
    if stage.exists() and any(stage.iterdir()):
        raise ValueError('isolated compiler requires an empty output stage')
    tools = toolchain or require_toolchain(root)
    base = source_version(root, include_commit=False)[0]
    revision = custom_revision(custom)
    with _CompilerSandbox(root, custom, tools) as sandbox:
        if source_version(sandbox.project, include_commit=False)[0] != base:
            raise ValueError('frontend sources changed while creating compiler snapshot')
        if custom_revision(sandbox.copy_custom) != revision:
            raise ValueError('custom sources changed while creating compiler snapshot')
        # The stock template proves the platform version but is never a second
        # source tree available to user imports during compilation.
        for name in ('src', 'public'):
            stock = local_path(sandbox.project, 'web/' + name)
            if stock.exists():
                shutil.rmtree(stock)
        try:
            declaration = compile_files(sandbox.project, sandbox.work / 'runtime', sandbox.copy_custom,
                                        sandbox.parent_stage, build_id=build_id, instance_id=instance_id,
                                        toolchain=(NODE, NPM), run=sandbox.run,
                                        customization_version=customization_version)
        except WebSourceError as error:
            detail = str(error)
            for path, label in ((sandbox.copy_custom, 'custom_web'), (sandbox.project / 'web', 'web'),
                                (sandbox.parent_stage, '<build>'), (sandbox.work, '<compiler>')):
                detail = detail.replace(str(path), label)
            # The child can read frontend inputs only. Keep its bounded diagnostics,
            # but no discarded temporary paths or nonexistent log-link promises.
            detail = detail.replace(' Log: <build>/build.log', '')[-4000:]
            atomic_write(stage / 'build.log', detail.encode('utf-8'))
            raise IsolatedCompileError(detail, code=error.code, file=error.file) from None
        if (source_version(root, include_commit=False)[0] != base
                or custom_revision(custom) != revision):
            raise ValueError('UI sources changed during isolated compilation')
        output = sandbox.work / 'files'
        reclaim_output(output)
        inventory = file_inventory(output)
        (sandbox.parent_stage / 'build.log').chmod(0o600)
        log = read_regular(sandbox.parent_stage, 'build.log', maximum=STREAM_BYTES)
        # Files are now root-owned, regular, bounded, and no child can change
        # them. No untrusted descriptor is passed to the signing parent.
        stage.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in inventory:
            atomic_write(local_path(stage / 'files', name), read_regular(output, name))
        atomic_write(stage / 'build.log', log)
        return declaration
