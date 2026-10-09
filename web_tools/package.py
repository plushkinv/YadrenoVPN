"""Version 1 Ed25519 UI packages and a consumer with externally pinned trust."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from web_tools.paths import atomic_write, canonical, local_path, relative_name
from web_tools.compatibility import bounds, check_requirements
from web_tools.permissions import require_permissions

MAX_FILES = 2000
MAX_BYTES = 64 * 1024 * 1024
EXTENSIONS = {'.html', '.js', '.css', '.woff2', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.ico', '.json'}
MANIFEST_FIELDS = {'format_version', 'product', 'product_version', 'base_build_id', 'build_id', 'instance_id',
                   'core_api', 'environment_contract', 'customization_version', 'key_id', 'content_hash', 'files'}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def _json(content):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('duplicate JSON field')
            result[name] = value
        return result
    return json.loads(content, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON number')))


def signing_identity(runtime):
    """Called under the publication lock. Never derive signing trust from bot tokens."""
    secrets = local_path(runtime, 'secrets', directory=True)
    key_path = local_path(secrets, 'ed25519.key')
    identity_path = local_path(runtime, 'identity.json')
    if key_path.exists() != identity_path.exists():
        raise ValueError('incomplete signing identity; restore its protected backup')
    if not key_path.exists():
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw()
        identity = {'instance_id': str(uuid.uuid4()), 'key_id': digest(public), 'public_key': base64.b64encode(public).decode()}
        atomic_write(key_path, key.private_bytes_raw())
        atomic_write(identity_path, canonical(identity))
    else:
        require_permissions(key_path, private=True,
                            message='signing key must be owned by the invoking administrator with private permissions')
        key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
        identity = _json(identity_path.read_bytes())
        public = key.public_key().public_bytes_raw()
        if identity.get('key_id') != digest(public) or identity.get('public_key') != base64.b64encode(public).decode():
            raise ValueError('signing identity mismatch; restore its protected backup')
    return key, identity


def _asset_name(name):
    relative_name(name)
    if Path(name).suffix not in EXTENSIONS or name == 'manifest.json':
        raise ValueError('unsupported UI asset: ' + name)
    return name


def file_inventory(folder, *, required=('application.json',)):
    folder = Path(folder)
    files = {}
    total = 0
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder).as_posix()
        checked = local_path(folder, relative)
        if not checked.is_file():
            continue
        _asset_name(relative)
        content = checked.read_bytes()
        total += len(content)
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise ValueError('UI package exceeds resource limits')
        if path.suffix in {'.html', '.js', '.css', '.json', '.svg'} and re.search(
                rb'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----|\bBOT_TOKEN\s*[:=]|\bTELEGRAM_API_URL\s*[:=]', content):
            raise ValueError('possible installation secret in UI output')
        files[relative] = {'sha256': digest(content), 'size': len(content)}
    if not set(required).issubset(files):
        raise ValueError('UI package is missing application entries')
    return files


def create_package(folder, *, key, identity, product_version, base_build_id, build_id, customization_version, requirements=None, core_api=None):
    files = file_inventory(folder)
    from web_tools.application import read_application
    read_application({name: local_path(folder, name).read_bytes() for name in files})
    manifest = {'format_version': 3, 'product': 'yadreno-vpn', 'product_version': product_version,
        'base_build_id': base_build_id, 'build_id': build_id, 'instance_id': identity['instance_id'],
        'core_api': bounds({'min': 1, 'max': 1} if core_api is None else core_api, 1, 'core API'), 'environment_contract': 1, 'customization_version': customization_version,
        'key_id': identity['key_id'], 'content_hash': digest(canonical(files)), 'files': files}
    manifest['requirements'] = check_requirements(requirements or {'frontend_api': {'min': 1, 'max': 1}, 'modules': []})
    signed = {'manifest': manifest, 'signature': base64.b64encode(key.sign(canonical(manifest))).decode()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', canonical(signed))
        for name in files:
            archive.write(local_path(folder, name), name)
    return signed, output.getvalue()


def verify_manifest(signed, trust, *, api_version=1, environment_version=1, frontend_version=1,
                    module_api_version=1, supported_formats=(3,)):
    """Trust is installed independently; a downloaded key is never a trust root."""
    if (not isinstance(trust, dict) or set(trust) != {'instance_id', 'key_id', 'public_key'}
            or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in trust.values())):
        raise ValueError('invalid pinned UI trust')
    if not isinstance(signed, dict) or set(signed) != {'manifest', 'signature'}:
        raise ValueError('invalid signed manifest envelope')
    value = signed['manifest']
    expected = MANIFEST_FIELDS | ({'requirements'} if isinstance(value, dict) and value.get('format_version') in {2, 3} else set())
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('invalid manifest fields')
    try:
        public = base64.b64decode(trust['public_key'], validate=True)
        if digest(public) != trust['key_id'] or value['key_id'] != trust['key_id'] or value['instance_id'] != trust['instance_id']:
            raise ValueError('UI package belongs to an untrusted installation/key')
        Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode(signed['signature'], validate=True), canonical(value))
    except (InvalidSignature, KeyError, TypeError) as exc:
        raise ValueError('invalid UI signature/trust') from exc
    if (type(value['format_version']) is not int or value['format_version'] not in {1, 2, 3}
            or value['format_version'] not in supported_formats or value['product'] != 'yadreno-vpn'
            or type(value['environment_contract']) is not int or value['environment_contract'] != environment_version):
        raise ValueError('incompatible UI format/product/environment')
    bounds = value['core_api']
    if (not isinstance(bounds, dict) or set(bounds) != {'min', 'max'} or any(type(v) is not int for v in bounds.values())
            or not 1 <= bounds['min'] <= api_version <= bounds['max']):
        raise ValueError('incompatible core API range')
    if value['format_version'] in {2, 3}:
        check_requirements(value['requirements'], api_version=api_version, frontend_version=frontend_version,
                           environment_version=environment_version, module_api_version=module_api_version)
    elif frontend_version != 1 or module_api_version != 1:
        # Released v1 packages predate explicit ranges and depend on contract 1.
        raise ValueError('legacy UI package requires frontend/module API 1')
    if not isinstance(value['build_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['build_id']):
        raise ValueError('invalid UI build identity')
    files = value['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('invalid UI inventory')
    size = 0
    for name, info in files.items():
        _asset_name(name)
        if not isinstance(info, dict) or set(info) != {'sha256', 'size'} or type(info['size']) is not int or info['size'] < 0 or not isinstance(info['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
            raise ValueError('invalid UI asset description')
        size += info['size']
    required = {'application.json'} if value['format_version'] == 3 else {'index.html', 'preview.html'}
    if size > MAX_BYTES or not required.issubset(files) or digest(canonical(files)) != value['content_hash']:
        raise ValueError('invalid UI content inventory/hash')
    return value


def verify_package(content, trust, **versions):
    if len(content) > MAX_BYTES:
        raise ValueError('UI package exceeds resource limits')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or len(names) > MAX_FILES + 1 or sum(item.file_size for item in entries) > MAX_BYTES:
                raise ValueError('duplicate/oversized UI archive')
            for entry in entries:
                relative_name(entry.filename)
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 not in {0, 0o100000} or entry.flag_bits & 1:
                    raise ValueError('UI archive must contain unencrypted regular files')
            signed = _json(archive.read('manifest.json'))
            manifest = verify_manifest(signed, trust, **versions)
            if set(names) != set(manifest['files']) | {'manifest.json'}:
                raise ValueError('UI archive has missing or unexpected files')
            files = {}
            for name, info in manifest['files'].items():
                data = archive.read(name)
                if len(data) != info['size'] or digest(data) != info['sha256']:
                    raise ValueError('corrupted UI asset: ' + name)
                files[name] = data
            if manifest['format_version'] == 3:
                from web_tools.application import read_application
                read_application(files)
            return signed, files
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise ValueError('invalid/incomplete UI archive') from exc
