"""Recognize and migrate the stock x-ui acme.sh IPv4 HTTP-01 profile only."""
from __future__ import annotations

import base64
import ipaddress
import os
import re
import secrets
import shlex

from web_api.setup_options import SetupError
from web_api.setup_system import system_path
from web_tools.package import digest
from web_tools.paths import atomic_write

HOME = '/root/.acme.sh'
RELOAD_COMMANDS = {
    'systemctl restart x-ui', 'x-ui restart',
    'systemctl restart x-ui 2>/dev/null || rc-service x-ui restart 2>/dev/null || true',
}
STANDALONE = re.compile(r'authenticator\s*=\s*standalone|--(?:standalone|alpn)\b|Le_Webroot=[\"\']?(?:no|standalone|alpn)[\"\']?(?:\s|$)')


class UnsupportedProfile(ValueError):
    """A bounded explanation that contains no certificate/account contents."""


def profile_path(address):
    if str(ipaddress.IPv4Address(address)) != address:
        raise ValueError('noncanonical IPv4 certificate')
    return f'{HOME}/{address}_ecc/{address}.conf'


def certificate_paths(address):
    profile_path(address)
    name = 'yadreno-acme-http01-' + address
    return {'nginx': '/etc/nginx/conf.d/' + name + '.conf', 'webroot': '/var/lib/' + name,
            'marker': '# Yadreno certificate HTTP-01 ' + address + '\n'}


def route_configuration(address, listen):
    paths = certificate_paths(address)
    if listen not in {'0.0.0.0', address}:
        raise UnsupportedProfile('выбранная привязка Nginx не обслуживает IPv4 сертификата')
    return (paths['marker'] + 'server {\n    listen ' + listen + ':80;\n    server_name ' + address + ';\n'
        '    location ^~ /.well-known/acme-challenge/ {\n        root ' + paths['webroot'] + ';\n'
        '        default_type text/plain;\n        try_files $uri =404;\n    }\n    location / { return 404; }\n}\n')


def _route_file(system, address):
    paths = certificate_paths(address)
    path = system_path(system, paths['nginx'])
    if path.exists() and (not path.is_file() or not path.read_bytes().startswith(paths['marker'].encode())):
        raise UnsupportedProfile('имя конфигурации HTTP-проверки занято чужим файлом')
    return path


def _values(content):
    if b'\r' in content:
        raise UnsupportedProfile('нестандартный формат профиля acme.sh')
    values = {}
    for line in content.decode('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)='([^'\r\n]*)'", line)
        if not match or match[1] in values:
            raise UnsupportedProfile('нестандартный формат профиля acme.sh')
        values[match[1]] = match[2]
    return values


def _command(value):
    prefix, suffix = '__ACME_BASE64__START_', '__ACME_BASE64__END_'
    if value.startswith(prefix) and value.endswith(suffix):
        return base64.b64decode(value[len(prefix):-len(suffix)], validate=True).decode('utf-8')
    return value


def _profile(content, address, webroot):
    values = _values(content)
    expected = {'Le_Domain': address, 'Le_Alt': 'no', 'Le_Keylength': 'ec-256',
                'Le_API': 'https://acme-v02.api.letsencrypt.org/directory',
                'Le_Certificate_Profile': 'shortlived', 'Le_RenewalDays': '6',
                'Le_RealKeyPath': '/root/cert/ip/privkey.pem',
                'Le_RealFullChainPath': '/root/cert/ip/fullchain.pem'}
    if any(values.get(key) != value for key, value in expected.items()):
        raise UnsupportedProfile('схема отличается от поддерживаемого IP-сертификата x-ui')
    if values.get('Le_Webroot') not in {'no', webroot} or values.get('Le_HTTPPort', '') not in {'', '80'}:
        raise UnsupportedProfile('нестандартный способ HTTP-проверки')
    for key, value in values.items():
        if ('hook' in key.lower() or key in {'Le_LocalAddress', 'Le_RealCertPath', 'Le_RealCACertPath'}) and value:
            raise UnsupportedProfile('дополнительные hooks, адрес проверки или пути сертификата')
    if _command(values.get('Le_ReloadCmd', '')) not in RELOAD_COMMANDS:
        raise UnsupportedProfile('нестандартная команда обновления сертификата панели')
    return values


def _stock_cron(system):
    path = system.path('/var/spool/cron/crontabs/root')
    if not path.is_file():
        return False
    found = []
    for line in path.read_text(errors='replace').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or HOME not in line:
            continue
        fields = line.split(None, 5)
        if len(fields) != 6:
            return False
        args = shlex.split(fields[5])
        tails = ([], ['>', '/dev/null'], ['>', '/dev/null', '2>&1'])
        commands = ([HOME + '/acme.sh', '--cron', '--home', HOME],
                    [HOME + '/acme.sh', '--home', HOME, '--cron'])
        found.append(any(args == command + tail for command in commands for tail in tails))
    return found == [True]


def _recognize(system, path, addresses, listen_address):
    content = path.read_bytes()
    values = _values(content)
    address = values.get('Le_Domain', '')
    expected = profile_path(address)
    if path != system_path(system, expected) or address not in addresses:
        raise UnsupportedProfile('сертификат не относится к IPv4 этого сервера в стандартном каталоге')
    owned = certificate_paths(address)
    values = _profile(content, address, owned['webroot'])
    listen = listen_address or '0.0.0.0'
    route = route_configuration(address, listen).encode()
    target = _route_file(system, address)
    webroot = system_path(system, owned['webroot'])
    owner = webroot / '.yadreno-owner'
    if webroot.exists() and (owner.is_symlink() or not owner.is_file() or owner.read_text() != address):
        raise UnsupportedProfile('каталог HTTP-проверки уже существует без ожидаемой метки владельца')
    script = system_path(system, HOME + '/acme.sh')
    if not script.is_file() or not re.search(r'^VER=3\.1\.\d+$', script.read_text(errors='replace'), re.M):
        raise UnsupportedProfile('не распознана поддерживаемая версия acme.sh 3.1.x')
    account = system_path(system, HOME + '/account.conf')
    if account.exists():
        for key, value in re.findall(r'^([A-Za-z_][A-Za-z_0-9]*)=(.*)$', account.read_text(errors='replace'), re.M):
            if ('HOOK' in key.upper() or 'CMD' in key.upper()) and value.strip() not in {"''", '""', ''}:
                raise UnsupportedProfile('общая конфигурация acme.sh содержит дополнительные hooks')
    if not _stock_cron(system):
        raise UnsupportedProfile('не распознано стандартное задание root cron для acme.sh')
    from cryptography import x509
    cert = system_path(system, values['Le_RealFullChainPath'])
    key = system_path(system, values['Le_RealKeyPath'])
    try:
        names = x509.load_pem_x509_certificate(cert.read_bytes()).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
    except (ValueError, x509.ExtensionNotFound, x509.DuplicateExtension):
        raise UnsupportedProfile('сертификат панели не распознан') from None
    if list(names) != [x509.IPAddress(ipaddress.IPv4Address(address))] or not key.is_file():
        raise UnsupportedProfile('файлы сертификата панели не соответствуют единственному IP профиля')
    return {'address': address, 'file': expected, 'sha256': digest(content), 'listen_address': listen,
            'needs_migration': values['Le_Webroot'] == 'no' or not webroot.exists()
                               or not target.exists() or target.read_bytes() != route}


def _unknown_hooks(text):
    safe = RELOAD_COMMANDS | {'', 'systemctl reload nginx', '/bin/systemctl reload nginx',
                             'nginx -s reload', '/usr/sbin/nginx -s reload'}
    for key, value in re.findall(r'^\s*(Le_(?:PreHook|PostHook|RenewHook|DeployHook|ReloadCmd)|(?:pre|post|deploy|renew)_hook)\s*=\s*(.*)$', text, re.M):
        try:
            value = _command(value.strip().strip("'\""))
        except (ValueError, UnicodeError):
            return True
        if value not in safe:
            return True
    return False


def inspect_renewals(system, paths, addresses, listen_address=None):
    """Return nonsecret plans and independent conflicts without executing shell files."""
    plans, issues = [], []
    patterns = ('etc/letsencrypt/renewal/*.conf', 'root/.acme.sh/*/*.conf', 'home/*/.acme.sh/*/*.conf',
                'etc/cron.d/*', 'var/spool/cron/crontabs/*', 'etc/systemd/system/*.service')
    for pattern in patterns:
        for path in sorted(system.filesystem.glob(pattern)):
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            text = path.read_text(errors='replace')
            migrated = "Le_Webroot='/var/lib/yadreno-acme-http01-" in text
            hooks = _unknown_hooks(text)
            if not STANDALONE.search(text) and not migrated and not hooks:
                continue
            relative = '/' + path.relative_to(system.filesystem).as_posix()
            reason = 'не распознаны дополнительные команды продления' if hooks else 'способ продления требует отдельной настройки'
            if relative.startswith(HOME + '/'):
                try:
                    plans.append(_recognize(system, path, addresses, listen_address))
                    continue
                except (ValueError, OSError, KeyError, SetupError) as exc:
                    # Only our bounded recognition messages, never account/certificate contents.
                    reason = str(exc) if isinstance(exc, UnsupportedProfile) else 'профиль, сертификат или способ запуска не распознан'
            port = 443 if re.search(r'--alpn\b|Le_Webroot=[\"\']alpn', text) else 80
            issues.append(SetupError('renewal_conflict',
                f'Настройка продления {relative}: {reason}. Требуется проверить совместимость с Nginx на TCP-порту {port}.',
                details={'file': relative.lstrip('/'), 'port': port}))
    # The stock x-ui certificate destination is shared; never adopt competing lineages.
    if len(plans) > 1:
        issues.append(SetupError('renewal_conflict', 'Несколько профилей acme.sh используют один сертификат панели.'))
        plans = []
    return plans, issues


def snapshot_profiles(system, plans, paths):
    snapshots = {}
    for plan in plans:
        if not plan['needs_migration']:
            continue
        path = system_path(system, profile_path(plan['address']))
        content = path.read_bytes()
        address = plan['address']
        owned = certificate_paths(address)
        values = _profile(content, address, owned['webroot'])
        if digest(content) != plan['sha256']:
            raise SetupError('renewal_changed', 'Профиль продления изменился после проверки; запустите установку повторно.')
        route = _route_file(system, address)
        snapshots[address] = {'before': values['Le_Webroot'], 'after': owned['webroot'], 'sha256': plan['sha256'],
            'listen_address': plan['listen_address'],
            'nginx_before': base64.b64encode(route.read_bytes()).decode() if route.exists() else None}
    return snapshots


def validate_snapshots(values, paths):
    if not isinstance(values, dict) or len(values) > 1:
        raise ValueError('invalid certificate transition snapshot')
    for address, item in values.items():
        owned = certificate_paths(address)
        if (not isinstance(item, dict) or set(item) != {'before', 'after', 'sha256', 'listen_address', 'nginx_before'}
                or item['before'] not in {'no', owned['webroot']} or item['after'] != owned['webroot']
                or not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            raise ValueError('invalid certificate transition fields')
        route_configuration(address, item['listen_address'])
        if item['nginx_before'] is not None:
            previous = base64.b64decode(item['nginx_before'], validate=True)
            if len(previous) > 64 * 1024 or not previous.startswith(owned['marker'].encode()):
                raise ValueError('invalid certificate route snapshot')


def prepare_routes(system, values, paths):
    validate_snapshots(values, paths)
    for address, item in values.items():
        owned = certificate_paths(address)
        root = system_path(system, owned['webroot'])
        root.mkdir(mode=0o755, parents=True, exist_ok=True)
        root.chmod(0o755)
        atomic_write(root / '.yadreno-owner', address.encode(), mode=0o644)
        atomic_write(_route_file(system, address), route_configuration(address, item['listen_address']).encode(), mode=0o644)


def restore_routes(system, values, paths):
    validate_snapshots(values, paths)
    for address, item in values.items():
        path = _route_file(system, address)
        if item['nginx_before'] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, base64.b64decode(item['nginx_before'], validate=True), mode=0o644)


def _set_webroot(system, address, expected, value):
    path = system_path(system, profile_path(address))
    content = path.read_bytes()
    current = _values(content)
    if current.get('Le_Domain') != address or current.get('Le_Webroot') not in {expected, value}:
        raise SetupError('renewal_changed', 'Способ продления изменён другим процессом; требуется проверка профиля.',
                         stage='recovery', exit_code=4, details={'file': profile_path(address)})
    if current['Le_Webroot'] == value:
        return
    updated, count = re.subn(rb"(?m)^Le_Webroot='[^'\r\n]*'$", ("Le_Webroot='" + value + "'").encode(), content)
    if count != 1:
        raise ValueError('ambiguous certificate webroot')
    stat = path.stat()
    atomic_write(path, updated, mode=stat.st_mode & 0o777)
    os.chown(path, stat.st_uid, stat.st_gid)


def migrate_profiles(system, values, paths):
    validate_snapshots(values, paths)
    for address, item in values.items():
        path = system_path(system, profile_path(address))
        if digest(path.read_bytes()) != item['sha256']:
            raise SetupError('renewal_changed', 'Профиль продления изменился во время установки.',
                             stage='renewal', exit_code=4, details={'file': profile_path(address)})
        token = secrets.token_hex(24)
        relative = '/.well-known/acme-challenge/yadreno-setup-' + token
        challenge = system_path(system, item['after'] + relative)
        challenge.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        challenge.parent.parent.chmod(0o755)
        challenge.parent.chmod(0o755)
        try:
            atomic_write(challenge, token.encode(), mode=0o644)
            try:
                if system.fetch('http://' + address + relative, limit=1024) != token.encode():
                    raise ValueError('wrong challenge response')
            except (SetupError, ValueError):
                raise SetupError('renewal_http_unavailable',
                    'Nginx не подтвердил HTTP-проверку существующего IP-сертификата.',
                    stage='renewal', exit_code=4, details={'address': address}) from None
            _set_webroot(system, address, item['before'], item['after'])
        finally:
            challenge.unlink(missing_ok=True)


def restore_profiles(system, values, paths):
    validate_snapshots(values, paths)
    for address, item in values.items():
        # Preserve ACME metadata updated by a concurrent successful renewal.
        _set_webroot(system, address, item['after'], item['before'])
