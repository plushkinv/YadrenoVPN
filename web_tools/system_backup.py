"""Protected backup of one managed certificate and its required ACME account."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile
from pathlib import Path

from web_tools.paths import atomic_write, canonical, local_path, read_local_backup
from web_tools.setup_paths import account_directory, certificate_name, marker, owned_paths, renewal_profile

CERT_FILES = ('cert.pem', 'chain.pem', 'fullchain.pem', 'privkey.pem')
ACCOUNT_FILES = ('meta.json', 'private_key.json', 'regr.json')


def _system_path(system_root, absolute):
    path = Path(system_root) / absolute.lstrip('/')
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('managed backup path contains a symbolic link')
    return path


def _identity_values(value, identity):
    instance = identity['instance_id']
    owned = owned_paths(instance)
    name = certificate_name(owned, value['options']['domain'])
    if value.get('instance_id') != instance or value.get('owned') != owned or value.get('certificate_name') != name:
        raise ValueError('managed backup ownership does not match installation identity')
    return instance, owned, name


def _target(name, owned, certificate, account, system_root):
    base = owned['certbot']
    if name == 'nginx':
        return _system_path(system_root, owned['nginx'])
    if name == 'certbot/renewal.conf':
        return _system_path(system_root, base + '/renewal/' + certificate + '.conf')
    if name.startswith('certbot/account/') and name.removeprefix('certbot/account/') in ACCOUNT_FILES:
        return _system_path(system_root, base + '/' + account + '/' + name.rsplit('/', 1)[1])
    if re.fullmatch(r'certbot/archive/(?:cert|chain|fullchain|privkey)[0-9]+\.pem', name):
        return _system_path(system_root, base + '/archive/' + certificate + '/' + name.rsplit('/', 1)[1])
    if name.startswith('certbot/live/') and name.removeprefix('certbot/live/') in CERT_FILES:
        # Certbot owns the final relative symlink, but no parent may be a link.
        return _system_path(system_root, base + '/live/' + certificate) / name.rsplit('/', 1)[1]
    raise ValueError('unowned certificate backup path')


def _link_target(path, link, archive):
    if not isinstance(link, str) or not re.fullmatch(r'\.\./\.\./archive/' + re.escape(archive.name)
                                                   + r'/(?:cert|chain|fullchain|privkey)[0-9]+\.pem', link):
        raise ValueError('invalid certificate link')
    resolved = (path.parent / link).resolve()
    if resolved.parent != archive.resolve():
        raise ValueError('certificate link escapes its own archive')
    return resolved


def system_archive(root, *, system_root=Path('/')):
    """Capture one lineage, never shared config, schedulers or other certificates."""
    runtime = Path(root) / 'web_runtime'
    setup_path = local_path(runtime, 'setup.json')
    if not setup_path.exists():
        return None
    setup = json.loads(setup_path.read_bytes())
    if setup.get('options', {}).get('proxy') != 'managed-nginx':
        return None
    instance, owned, certificate = _identity_values(setup, json.loads(local_path(runtime, 'identity.json').read_bytes()))
    profile = _target('certbot/renewal.conf', owned, certificate, '', system_root).read_bytes()
    account = account_directory(renewal_profile(profile.decode('utf-8'), owned, certificate))
    archive = _system_path(system_root, owned['certbot'] + '/archive/' + certificate)
    names = ['nginx', 'certbot/renewal.conf', *('certbot/account/' + name for name in ACCOUNT_FILES),
             *('certbot/live/' + name for name in CERT_FILES)]
    names.extend('certbot/archive/' + path.name for path in sorted(archive.iterdir()))
    contents, metadata = {}, {}
    for name in names:
        path = _target(name, owned, certificate, account, system_root)
        if name.startswith('certbot/live/'):
            if not path.is_symlink():
                raise ValueError('certificate live entry must be a Certbot symlink')
            link = os.readlink(path)
            target = _link_target(path, link, archive)
            if not target.is_file() or 'certbot/archive/' + target.name not in names:
                raise ValueError('incomplete certificate archive')
            metadata[name] = {'kind': 'link', 'target': link}
        else:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('managed backup requires regular files')
            content = profile if name == 'certbot/renewal.conf' else path.read_bytes()
            if name == 'nginx' and not content.startswith(marker(instance).encode()):
                raise ValueError('managed system file ownership mismatch')
            contents[name] = content
            metadata[name] = {'kind': 'file', 'sha256': hashlib.sha256(content).hexdigest()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', canonical({'format_version': 1, 'instance_id': instance, 'files': metadata}))
        for name, content in contents.items():
            bundle.writestr(name, content)
    return output.getvalue()


def restore_system(source, root, *, system_root=Path('/'), check_only=False):
    """Validate everything before restoring absent/identical files; start no service."""
    backup, system = read_local_backup(Path(source))
    setup = json.loads(backup.get('web_runtime/setup.json', b'{}'))
    if setup.get('options', {}).get('proxy') != 'managed-nginx':
        return {'restored_system_files': 0, 'services_started': False}
    instance, owned, certificate = _identity_values(setup, json.loads(backup.get('web_runtime/identity.json', b'{}')))
    if system is None:
        raise ValueError('protected managed system backup is missing')
    installed = local_path(Path(root) / 'web_runtime', 'identity.json')
    if installed.exists() and json.loads(installed.read_bytes()).get('instance_id') != instance:
        raise ValueError('system restore belongs to another installation')
    archive_dir = _system_path(system_root, owned['certbot'] + '/archive/' + certificate)
    with zipfile.ZipFile(io.BytesIO(system)) as bundle:
        names = bundle.namelist()
        manifest = json.loads(bundle.read('manifest.json'))
        if (len(set(names)) != len(names) or manifest.get('format_version') != 1
                or manifest.get('instance_id') != instance or not isinstance(manifest.get('files'), dict)):
            raise ValueError('invalid managed system backup')
        profile = bundle.read('certbot/renewal.conf')
        account = account_directory(renewal_profile(profile.decode('utf-8'), owned, certificate))
        contents, links = {}, {}
        for name, info in manifest['files'].items():
            target = _target(name, owned, certificate, account, system_root)
            if info.get('kind') == 'file' and not name.startswith('certbot/live/'):
                content = bundle.read(name)
                if hashlib.sha256(content).hexdigest() != info.get('sha256'):
                    raise ValueError('corrupted managed system backup')
                if name == 'nginx' and not content.startswith(marker(instance).encode()):
                    raise ValueError('managed system file ownership mismatch')
                if target.is_symlink() or target.exists() and (not target.is_file() or target.read_bytes() != content):
                    raise ValueError('system restore would overwrite an existing file')
                contents[name] = content
            elif info.get('kind') == 'link' and name.startswith('certbot/live/'):
                _link_target(target, info['target'], archive_dir)
                if target.is_symlink():
                    if os.readlink(target) != info['target']:
                        raise ValueError('system restore would overwrite an existing link')
                elif target.exists():
                    raise ValueError('system restore would overwrite an existing file')
                links[name] = info['target']
            else:
                raise ValueError('invalid managed system entry')
        required = {'nginx', 'certbot/renewal.conf', *('certbot/account/' + name for name in ACCOUNT_FILES)}
        if (set(names) != {'manifest.json', *contents} or not required <= contents.keys()
                or set(links) != {'certbot/live/' + name for name in CERT_FILES}):
            raise ValueError('incomplete or unexpected managed system backup')
        for name, link in links.items():
            destination = _link_target(_target(name, owned, certificate, account, system_root), link, archive_dir)
            if 'certbot/archive/' + destination.name not in contents:
                raise ValueError('certificate link target is absent from backup')
    if check_only:
        return {'valid': True, 'restored_system_files': 0, 'services_started': False}
    for name, content in contents.items():
        atomic_write(_target(name, owned, certificate, account, system_root), content,
                     mode=0o600 if name.startswith('certbot') else 0o644)
    for name, link in links.items():
        target = _target(name, owned, certificate, account, system_root)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.is_symlink():
            target.symlink_to(link)
    return {'restored_system_files': len(contents) + len(links), 'services_started': False, 'https_revalidation_required': True}
