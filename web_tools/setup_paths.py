"""Stable ownership names shared by setup and autonomous protected backups."""
import configparser
import hashlib
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit
import uuid


def owned_paths(instance_id):
    if str(uuid.UUID(instance_id)) != instance_id:
        raise ValueError('invalid installation identity')
    name = 'yadreno-web-' + instance_id
    return {'name': name, 'nginx': '/etc/nginx/conf.d/' + name + '.conf',
            'acme': '/var/lib/' + name + '/acme', 'certbot': '/etc/letsencrypt'}


def certificate_name(paths, domain):
    return paths['name'] + '-' + hashlib.sha256(domain.encode('ascii')).hexdigest()[:16]


def renewal_profile(content, paths, name):
    """Read a Certbot profile without executing hooks or adopting another lineage."""
    if not re.fullmatch(re.escape(paths['name']) + r'-[a-f0-9]{16}', name):
        raise ValueError('certificate does not belong to this installation')
    value = configparser.ConfigParser(interpolation=None)
    try:
        value.read_string('[lineage]\n' + content)
    except configparser.Error:
        raise ValueError('invalid certificate renewal profile') from None
    lineage, params = value['lineage'], value['renewalparams']
    base = paths['certbot']
    expected = {'archive_dir': base + '/archive/' + name,
                **{key: base + '/live/' + name + '/' + filename for key, filename in
                   (('cert', 'cert.pem'), ('privkey', 'privkey.pem'), ('chain', 'chain.pem'), ('fullchain', 'fullchain.pem'))}}
    if any(lineage.get(key) != item for key, item in expected.items()):
        raise ValueError('certificate profile references another lineage')
    if params.get('authenticator') != 'webroot' or params.get('webroot_path', '').rstrip(', ').strip('\"\'') != paths['acme']:
        raise ValueError('certificate profile has another authenticator or webroot')
    return dict(params)


def account_directory(params):
    """Resolve only the account referenced by the selected renewal profile."""
    account = params.get('account', '')
    server = urlsplit(params.get('server', ''))
    if (not re.fullmatch(r'[a-f0-9]{32}', account) or server.scheme != 'https' or not server.hostname
            or server.username or server.password or server.query or server.fragment
            or any(part in {'.', '..'} for part in server.path.split('/'))
            or not re.fullmatch(r'[A-Za-z0-9.:\[\]-]+', server.netloc)
            or not re.fullmatch(r'/[A-Za-z0-9_./-]*', server.path)):
        raise ValueError('invalid certificate account reference')
    # Certbot's server_path uses the ACME URL without its scheme.
    return PurePosixPath('accounts', server.netloc, server.path.strip('/'), account).as_posix()


def marker(instance_id):
    return '# Yadreno web installation ' + instance_id + '\n'
