"""Protected backup of exactly this installation's managed HTTPS files."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

from web_tools.paths import atomic_write, canonical, local_path, read_local_backup, relative_name
from web_tools.setup_paths import marker, owned_paths


def _system_path(system_root, absolute):
    path = Path(system_root) / absolute.lstrip('/')
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('managed backup path contains a symbolic link')
    return path


def _identity(root):
    setup = local_path(Path(root) / 'web_runtime', 'setup.json')
    if not setup.exists():
        return None
    value = json.loads(setup.read_bytes())
    if value.get('options', {}).get('proxy') != 'managed-nginx':
        return None
    identity = json.loads(local_path(Path(root) / 'web_runtime', 'identity.json').read_bytes())
    return _identity_values(value, identity)


def _identity_values(value, identity):
    instance = identity['instance_id']
    if value.get('instance_id') != instance or value.get('owned') != owned_paths(instance):
        raise ValueError('managed backup ownership does not match installation identity')
    return instance


def _target(name, owned, system_root):
    relative_name(name)
    if name in {'nginx', 'renew_service', 'renew_timer'}:
        return _system_path(system_root, owned[name])
    if name == 'certbot-owner':
        return _system_path(system_root, owned['certbot'] + '/.yadreno-owner')
    if name.startswith('certbot/'):
        relative = name.removeprefix('certbot/')
        parts = PurePosixPath(relative).parts
        if parts[:2] not in {('config', 'accounts'), ('config', 'archive'), ('config', 'live'), ('config', 'renewal')} and relative != 'config/cli.ini':
            raise ValueError('unowned certificate backup path')
        base = _system_path(system_root, owned['certbot'])
        target = base / relative
        if any(parent.is_symlink() for parent in target.parents):
            raise ValueError('managed certificate parent is a symbolic link')
        return target
    raise ValueError('unowned system backup path')


def _link_target(path, link, cert_root):
    if not isinstance(link, str) or not link or '\\' in link or Path(link).is_absolute():
        raise ValueError('invalid certificate link')
    resolved = (path.parent / link).resolve()
    if not resolved.is_relative_to(cert_root.resolve() / 'config/archive'):
        raise ValueError('certificate link escapes its own archive')
    return resolved


def system_archive(root, *, system_root=Path('/')):
    """Prepare bytes without changing an existing protected backup generation."""
    instance = _identity(root)
    if instance is None:
        return None
    owned = owned_paths(instance)
    paths = [(name, _target(name, owned, system_root)) for name in ('nginx', 'renew_service', 'renew_timer', 'certbot-owner')]
    cert_root = _system_path(system_root, owned['certbot'])
    if (cert_root / '.yadreno-owner').read_text(encoding='utf-8').strip() != instance:
        raise ValueError('certificate directory ownership mismatch')
    for section in ('accounts', 'archive', 'live', 'renewal', 'cli.ini'):
        selected = cert_root / 'config' / section
        if not selected.exists() and not selected.is_symlink():
            continue
        candidates = [selected, *selected.rglob('*')] if selected.is_dir() and not selected.is_symlink() else [selected]
        for path in candidates:
            relative = 'certbot/' + path.relative_to(cert_root).as_posix()
            checked = _target(relative, owned, system_root)
            if path.is_symlink() or path.is_file():
                paths.append((relative, checked))
    files, metadata = {}, {}
    for name, path in paths:
        if path.is_symlink():
            link = os.readlink(path)
            target = _link_target(path, link, cert_root)
            if not target.is_file():
                raise ValueError('incomplete certificate archive')
            metadata[name] = {'kind': 'link', 'target': link}
        else:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('managed backup requires regular files')
            content = path.read_bytes()
            if name in owned and not content.startswith(marker(instance).encode()):
                raise ValueError('managed system file ownership mismatch')
            files[name] = content
            metadata[name] = {'kind': 'file', 'sha256': hashlib.sha256(content).hexdigest()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', canonical({'format_version': 1, 'instance_id': instance, 'files': metadata}))
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def backup_system(destination, root, *, system_root=Path('/')):
    """Compatibility writer for legacy flat protected snapshots."""
    destination = local_path(destination, 'web-system.zip')
    content = system_archive(root, system_root=system_root)
    if content is None:
        destination.unlink(missing_ok=True)
    else:
        atomic_write(destination, content)


def restore_system(source, root, *, system_root=Path('/'), check_only=False):
    """Restore only absent/identical owned files. Setup must revalidate HTTPS later."""
    source, root = Path(source), Path(root)
    backup, system = read_local_backup(source)
    setup = json.loads(backup.get('web_runtime/setup.json', b'{}'))
    if setup.get('options', {}).get('proxy') != 'managed-nginx':
        return {'restored_system_files': 0, 'services_started': False}
    instance = _identity_values(setup, json.loads(backup.get('web_runtime/identity.json', b'{}')))
    if system is None:
        raise ValueError('protected managed system backup is missing')
    installed = local_path(root / 'web_runtime', 'identity.json')
    if installed.exists() and json.loads(installed.read_bytes()).get('instance_id') != instance:
        raise ValueError('system restore belongs to another installation')
    owned = owned_paths(instance)
    cert_root = _system_path(system_root, owned['certbot'])
    with zipfile.ZipFile(io.BytesIO(system)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read('manifest.json'))
        if (len(set(names)) != len(names) or manifest.get('format_version') != 1
                or manifest.get('instance_id') != instance or not isinstance(manifest.get('files'), dict)):
            raise ValueError('invalid managed system backup')
        contents, links = {}, {}
        for name, info in manifest['files'].items():
            target = _target(name, owned, system_root)
            if info.get('kind') == 'file':
                content = archive.read(name)
                if hashlib.sha256(content).hexdigest() != info.get('sha256'):
                    raise ValueError('corrupted managed system backup')
                if target.is_symlink() or target.exists() and (not target.is_file() or target.read_bytes() != content):
                    raise ValueError('system restore would overwrite an existing file')
                contents[name] = content
            elif info.get('kind') == 'link' and name.startswith('certbot/config/live/'):
                _link_target(target, info['target'], cert_root)
                if target.is_symlink():
                    if os.readlink(target) != info['target']:
                        raise ValueError('system restore would overwrite an existing link')
                elif target.exists():
                    raise ValueError('system restore would overwrite an existing file')
                links[name] = info['target']
            else:
                raise ValueError('invalid managed system entry')
        if set(names) != {'manifest.json', *contents}:
            raise ValueError('unexpected managed system backup file')
        for name, link in links.items():
            destination = _link_target(_target(name, owned, system_root), link, cert_root)
            relative = 'certbot/' + destination.relative_to(cert_root.resolve()).as_posix()
            if relative not in contents:
                raise ValueError('certificate link target is absent from backup')
    if check_only:
        return {'valid': True, 'restored_system_files': 0, 'services_started': False}
    # All input and conflicts are checked before the first system write.
    for name, content in contents.items():
        atomic_write(_target(name, owned, system_root), content, mode=0o600 if name.startswith('certbot') else 0o644)
    for name, link in links.items():
        target = _target(name, owned, system_root)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.is_symlink():
            target.symlink_to(link)
    return {'restored_system_files': len(contents) + len(links), 'services_started': False, 'https_revalidation_required': True}
