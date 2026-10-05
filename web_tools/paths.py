"""Installation-owned paths, atomic metadata and protected backup material."""
from __future__ import annotations

import json
import contextlib
import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSISTENT = ('custom_web', 'web_runtime')
CUSTOM_SOURCE_IGNORED = frozenset({'node_modules', '__pycache__', '.git', '.cache', 'dist'})
BACKUP_ARCHIVE = 'ui-backup.zip'
BACKUP_MANIFEST = 'ui-backup.json'


def relative_name(value, *, hidden=False):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value) or value.startswith('/')):
        raise ValueError('invalid relative UI path')
    parts = value.split('/')
    if any(part in {'', '.', '..'} or not hidden and part.startswith('.') for part in parts):
        raise ValueError('invalid relative UI path')
    return PurePosixPath(value).as_posix()


def private_directory(path):
    """Create every missing level privately; never chmod/chown existing paths."""
    path = Path(path)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError('UI paths must not contain symbolic links')
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    if not parent.is_dir():
        raise NotADirectoryError(str(parent))
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            # A concurrent creator retains ownership and mode; never repair it.
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError('UI paths require directories without symbolic links')
    return path


def local_path(root, relative, *, directory=False, hidden=False):
    """Never follow links into installation data outside the selected tree."""
    root = Path(root)
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        raise ValueError('UI root must not be a symbolic link')
    root = root.resolve()
    if directory:
        private_directory(root)
    target = root
    for part in relative_name(relative, hidden=hidden).split('/'):
        target = target / part
        if target.is_symlink():
            raise ValueError('UI paths must not contain symbolic links')
        if directory:
            target.mkdir(exist_ok=True, mode=0o700)
    if not target.resolve().is_relative_to(root):
        raise ValueError('UI path escapes its root')
    return target


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def source_provenance(folder, signed):
    """Optional installation-local proof; package trust remains the signature."""
    try:
        path = local_path(folder, 'source.json')
        info = path.stat()
        parent = path.parent.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024
                or os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o022
                                       or parent.st_uid != os.getuid() or parent.st_mode & 0o022)):
            return None
        value = json.loads(path.read_bytes())
        fields = {'format_version', 'manifest_hash', 'custom_fingerprint'}
        if isinstance(value, dict) and value.get('format_version') == 2:
            fields |= {'base_build_id', 'customization_version', 'inventory'}
        if (not isinstance(value, dict) or set(value) != fields or type(value['format_version']) is not int
                or value['format_version'] not in {1, 2}
                or value['manifest_hash'] != hashlib.sha256(canonical(signed)).hexdigest()):
            return None
        fingerprint = value['custom_fingerprint']
        if not (isinstance(fingerprint, str) and re.fullmatch(r'[a-f0-9]{64}', fingerprint)):
            if not (value['format_version'] == 2 and fingerprint is None
                    and signed['manifest']['customization_version'] == 'base'):
                return None
        if value['format_version'] == 2:
            from web_tools.view_inventory import validate_inventory
            manifest = signed['manifest']
            if (value['base_build_id'] != manifest['base_build_id']
                    or not isinstance(value['base_build_id'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['base_build_id'])
                    or value['customization_version'] != manifest['customization_version']
                    or not isinstance(value['customization_version'], str)
                    or not re.fullmatch(r'base|[0-9]{1,20}\.[0-9]{1,20}\.[0-9]{1,20}', value['customization_version'])):
                return None
            validate_inventory(value['inventory'], modules={item['id'] for item in manifest.get('requirements', {}).get('modules', [])})
        return value
    except (ValueError, OSError, TypeError, KeyError):
        return None


def atomic_write(path, content, *, mode=0o600):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('metadata paths must not be symbolic links')
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode) if hasattr(os, 'fchmod') else None
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        _commit_file(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _commit_file(temporary, path):
    """Publish one complete file after its caller has flushed its contents."""
    path = Path(path)
    os.replace(temporary, path)
    if os.name != 'nt':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


@contextlib.contextmanager
def publication_lock(runtime):
    """Serialize publication changes and protected captures using only stdlib."""
    path = local_path(runtime, 'locks', directory=True) / 'publication.lock'
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def installation_files(root=PROJECT_ROOT, *, secrets=False):
    """Enumerate persistent UI state, excluding secrets unless requested."""
    root = Path(root).resolve()
    for name in PERSISTENT:
        folder = local_path(root, name)
        if not folder.exists():
            continue
        def excluded(path):
            parts = path.relative_to(folder).parts
            if _custom_source_path(path.relative_to(root).as_posix()):
                return any(part in CUSTOM_SOURCE_IGNORED for part in parts)
            return (any(part in {'node_modules', '__pycache__'} or part.startswith('.') for part in parts)
                    or name == 'web_runtime' and parts[0] in {'staging', 'cache', 'preview', 'locks', 'source-lock'}
                    or name == 'web_runtime' and parts[0] == 'secrets' and not secrets)
        for directory, subdirs, files in os.walk(folder, followlinks=False):
            parent = Path(directory)
            subdirs[:] = sorted(child for child in subdirs if not excluded(parent / child))
            for child in subdirs:
                relative = (parent / child).relative_to(root).as_posix()
                local_path(root, relative, hidden=_custom_source_path(relative))
            for child in sorted(files):
                path = parent / child
                if excluded(path):
                    continue
                relative = path.relative_to(root).as_posix()
                checked = local_path(root, relative, hidden=_custom_source_path(relative))
                if not stat.S_ISREG(checked.stat().st_mode):
                    raise ValueError('UI backup requires regular files')
                yield checked, relative


def backup_to_zip(archive, root=PROJECT_ROOT):
    """Capture an administrator backup, including the existing signing identity."""
    runtime = local_path(root, 'web_runtime')
    with publication_lock(runtime):
        files = list(installation_files(root))
        key_path = local_path(runtime, 'secrets/ed25519.key')
        if key_path.exists() or local_path(runtime, 'identity.json').exists():
            from web_tools.package import signing_identity
            signing_identity(runtime)
            files.append((key_path, 'web_runtime/secrets/ed25519.key'))
        for path, relative in files:
            archive.write(path, relative)
        # Preserve explicitly empty customization trees on another installation.
        if not any(name.startswith('custom_web/') for _, name in files):
            archive.writestr('custom_web/', b'')
        return len(files)


def backup_local(destination, root=PROJECT_ROOT):
    """Atomically replace a complete protected UI/system backup generation."""
    # Snapshot directories may be hidden while the outer updater prepares them.
    # Validate their ancestors as a root, not as a public relative asset name.
    destination = local_path(destination, BACKUP_ARCHIVE)
    private_directory(destination.parent)
    with publication_lock(local_path(root, 'web_runtime')):
        _capture_local_backup(destination, root)


def _capture_local_backup(destination, root):
    from web_tools.system_backup import system_archive
    system = system_archive(root)
    files = list(installation_files(root, secrets=True))
    descriptor, temporary = tempfile.mkstemp(prefix='.ui-backup-', suffix='.zip.tmp', dir=destination.parent)
    inventory = {}
    try:
        with os.fdopen(descriptor, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path, relative in files:
                    digest = hashlib.sha256()
                    with path.open('rb') as source, archive.open(relative, 'w', force_zip64=True) as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            digest.update(chunk)
                            target.write(chunk)
                    inventory[relative] = digest.hexdigest()
                if system is not None:
                    archive.writestr('web-system.zip', system)
                    inventory['web-system.zip'] = hashlib.sha256(system).hexdigest()
                roots = [name for name in PERSISTENT if local_path(root, name).exists()]
                archive.writestr(BACKUP_MANIFEST, canonical({'format_version': 2, 'files': inventory,
                                                            'roots': roots}))
            stream.flush()
            os.fsync(stream.fileno())
        _commit_file(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _custom_source_path(relative):
    """Hidden helpers belong only to live custom sources or private task copies."""
    return relative.startswith('custom_web/') or bool(re.match(
        r'^web_runtime/editor_tasks/[a-f0-9]{32}/editor/(?:baseline|custom_web)(?:/|$)', relative))


def _backup_inventory(content, version, *, with_roots=False):
    value = json.loads(content)
    if (not isinstance(value, dict) or (set(value) != {'format_version', 'files'}
            and not (version == 2 and set(value) == {'format_version', 'files', 'roots'}))
            or type(value['format_version']) is not int or value['format_version'] != version
            or not isinstance(value['files'], dict)):
        raise ValueError('invalid UI backup inventory')
    roots = value.get('roots', [name for name in PERSISTENT
                                if any(path.startswith(name + '/') for path in value['files'])])
    if (not isinstance(roots, list) or any(type(name) is not str or name not in PERSISTENT for name in roots)
            or len(roots) != len(set(roots))):
        raise ValueError('invalid UI backup roots')
    for name, digest in value['files'].items():
        relative_name(name, hidden=_custom_source_path(name))
        parts = PurePosixPath(name).parts
        if (not (version == 2 and name == 'web-system.zip')
                and (len(parts) < 2 or parts[0] not in roots)):
            raise ValueError('unowned UI backup path')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('invalid UI backup hash')
    return (value['files'], roots) if with_roots else value['files']


def _read_backup_contents(source, *, with_roots=False):
    """Read one complete generation; never fall back from a damaged new archive."""
    source = Path(source)
    archive_path = local_path(source, BACKUP_ARCHIVE)
    if archive_path.exists():
        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(set(names)) != len(names):
                    raise ValueError('duplicate UI backup file')
                for entry in entries:
                    relative_name(entry.filename, hidden=_custom_source_path(entry.filename))
                    if (entry.is_dir() or (entry.external_attr >> 16) & 0o170000 not in {0, 0o100000}
                            or entry.flag_bits & 1):
                        raise ValueError('UI backup requires unencrypted regular files')
                inventory, roots = _backup_inventory(archive.read(BACKUP_MANIFEST), 2, with_roots=True)
                if set(names) != set(inventory) | {BACKUP_MANIFEST}:
                    raise ValueError('incomplete or corrupted UI backup')
                contents = {}
                for name, digest in inventory.items():
                    data = archive.read(name)
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError('incomplete or corrupted UI backup')
                    contents[name] = data
                return (contents, roots) if with_roots else contents
        except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
            raise ValueError('incomplete or corrupted UI backup') from exc
    # Existing protected flat snapshots remain readable. New backups leave them
    # untouched; this branch is selected only when no atomic generation exists.
    inventory, roots = _backup_inventory(local_path(source, BACKUP_MANIFEST).read_bytes(), 1, with_roots=True)
    contents = {}
    for path, relative in installation_files(source, secrets=True):
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != inventory.get(relative):
            raise ValueError('incomplete or corrupted UI backup')
        contents[relative] = data
    if set(contents) != set(inventory):
        raise ValueError('incomplete or corrupted UI backup')
    system = local_path(source, 'web-system.zip')
    if system.exists():
        contents['web-system.zip'] = system.read_bytes()
    return (contents, roots) if with_roots else contents


def read_local_backup(source):
    """Return verified UI files and optional protected system material."""
    contents = _read_backup_contents(source)
    system = contents.pop('web-system.zip', None)
    return contents, system


def _remove_owned_tree(path, parent):
    """Resolve every recursive-delete boundary, including on Windows."""
    path, parent = Path(path), Path(parent).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(parent) or path.resolve() == parent:
        raise ValueError('UI cleanup path escapes its parent')
    if path.exists():
        shutil.rmtree(path)


def _replace_ui_trees(stage, destination, roots):
    old = Path(tempfile.mkdtemp(prefix='.ui-previous-', dir=destination))
    moved = []
    installed = []
    completed = False
    try:
        for name in PERSISTENT:
            target = local_path(destination, name)
            if target.exists():
                os.replace(target, old / name)
                moved.append(name)
            if name in roots:
                os.replace(stage / name, target)
                installed.append(name)
        completed = True
    except BaseException:
        for name in reversed(installed):
            _remove_owned_tree(local_path(destination, name), destination)
        for name in reversed(moved):
            os.replace(old / name, destination / name)
        completed = True
        raise
    finally:
        # A failed recovery retains its protected previous material for repair.
        if completed:
            _remove_owned_tree(old, destination)


def restore_local(source, destination):
    """Restore verified UI material into a clean installation; never touch its DB."""
    source, destination = Path(source), Path(destination)
    contents, roots = _read_backup_contents(source, with_roots=True)
    contents.pop('web-system.zip', None)
    for name in PERSISTENT:
        target = local_path(destination, name)
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError('UI restore requires empty installation UI directories')
    private_directory(destination)
    stage = Path(tempfile.mkdtemp(prefix='.ui-restore-', dir=destination))
    try:
        for name in PERSISTENT:
            local_path(stage, name, directory=True)
        for relative, content in contents.items():
            atomic_write(local_path(stage, relative, hidden=_custom_source_path(relative)), content)
        _replace_ui_trees(stage, destination, roots)
    finally:
        _remove_owned_tree(stage, destination)
    return {'restored_files': len(contents), 'database_changed': False}
