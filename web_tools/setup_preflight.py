"""Read-only prerequisites; no packages, certificate requests or settings writes."""
from __future__ import annotations

from dataclasses import replace
import ipaddress
import json
from pathlib import Path
import re

from web_tools.setup_options import SetupError
from web_tools.setup_detection import prepared_local_nginx
from web_tools.setup_certificates import check_profile, inspect_renewals, renewal_timer
from web_tools.setup_paths import certificate_name as lineage_name
from web_tools.setup_system import owned_paths, system_path, verify_owned_file
from web_tools.package import MAX_BYTES, digest, verify_package
from web_tools.paths import local_path
from web_tools.publication import manifest_path, read_pointer

SETTING_KEYS = ('web_enabled', 'web_listen_host', 'web_listen_port', 'web_public_origin', 'web_trusted_proxies')


class SettingsStore:
    """Maintenance uses the existing repository API, never installer SQL."""
    def snapshot(self):
        from database import requests as db
        return {name: db.get_setting(name) for name in SETTING_KEYS}

    def restore(self, values):
        from database import requests as db
        # Disable first so a crash between individual repository commits cannot
        # expose a listener with a mixture of origins, ports and proxy trust.
        db.set_setting('web_enabled', '0')
        for name in SETTING_KEYS[1:]:
            if values[name] is None:
                db.delete_setting(name)
            else:
                db.set_setting(name, values[name])
        if values['web_enabled'] is None:
            db.delete_setting('web_enabled')
        else:
            db.set_setting('web_enabled', values['web_enabled'])

    def configure(self, options):
        self.restore({'web_enabled': '1', 'web_listen_host': options.backend_bind,
            'web_listen_port': str(options.backend_port), 'web_public_origin': options.public_url,
            'web_trusted_proxies': json.dumps(options.trusted_proxies)})

    def enable_button(self):
        from core.web_ui import enable_web_cabinet_button
        return enable_web_cabinet_button()


def publication(root):
    runtime = Path(root) / 'web_runtime'
    try:
        current = read_pointer(runtime)['current']
        signed = json.loads(manifest_path(runtime, current).read_bytes())
        trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
        exact = local_path(runtime, 'publications/' + current + '/package.zip')
        verified, files = verify_package(exact.read_bytes(), trust)
        if verified != signed or signed['manifest']['build_id'] != current:
            raise ValueError('publication mismatch')
        for name, data in files.items():
            if local_path(runtime, 'publications/' + current + '/files/' + name).read_bytes() != data:
                raise ValueError('publication file mismatch')
        owned_paths(trust['instance_id'])
        return signed, trust
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupError('ui_not_ready', 'Сначала установите готовую совместимую сборку интерфейса; Node для базовой сборки не нужен.') from None


def verify_endpoint(system, origin, signed, trust, *, package=True, nginx_configuration=None):
    try:
        required = {'nginx_configuration': nginx_configuration} if nginx_configuration is not None else {}
        remote = json.loads(system.fetch(origin + '/api/v1/ui/manifest', **required))
        if remote != signed:
            raise ValueError('wrong signed publication')
        manifest = signed['manifest']
        build = manifest['build_id']
        for path in ('/', '/ui/versions/' + build + '/index.html'):
            if digest(system.fetch(origin + path)) != manifest['files']['index.html']['sha256']:
                raise ValueError('wrong immutable application entry')
        bootstrap = json.loads(system.fetch(origin + '/api/v1/bootstrap'))
        if bootstrap.get('api_version') != 1:
            raise ValueError('wrong API bootstrap')
        if package:
            downloaded = system.fetch(origin + '/api/v1/ui/packages/' + manifest['content_hash'], limit=MAX_BYTES)
            if verify_package(downloaded, trust)[0] != signed:
                raise ValueError('wrong package')
    except (ValueError, KeyError, TypeError):
        raise SetupError('installation_mismatch', 'HTTPS не подтвердил точную установку, версию и полный пакет интерфейса.', stage='https', exit_code=4) from None


def listeners(system):
    result = []
    for line in system.run(['ss', '-H', '-ltnp']).stdout.splitlines():
        columns = line.split()
        if len(columns) < 5:
            raise SetupError('listeners_unknown', 'Не удалось однозначно разобрать список TCP listeners.')
        endpoint = columns[3]
        host, port = endpoint.rsplit(':', 1)
        host = host.strip('[]').split('%', 1)[0]
        if host == '*':
            host = '::' if endpoint.startswith('[') else '0.0.0.0'
        result.append({'host': host, 'port': int(port), 'process': ' '.join(columns[5:])})
    if system.which('docker'):
        rows = system.run(['docker', 'ps', '--format', '{{.Ports}}']).stdout
        for host, port in re.findall(r'(\[[^\]]+\]|[0-9.]+):(\d+)->\d+/tcp', rows):
            result.append({'host': host.strip('[]'), 'port': int(port), 'process': 'docker published port'})
    return result


def overlaps(first, second):
    first, second = ipaddress.ip_address(first), ipaddress.ip_address(second)
    return first == second or first.is_unspecified or second.is_unspecified


def nginx_configuration_preflight(system, options, paths, instance):
    verify_owned_file(system, paths['nginx'], instance)
    system_path(system, paths['certbot'])
    check_profile(system, paths, certificate_name(system, paths, options.domain))
    if system.which('nginx'):
        dump = system.run(['nginx', '-T'], check=False)
        if dump.returncode:
            failure = system.command_error(['nginx', '-T'], dump.stderr, dump.returncode)
            raise SetupError('nginx_invalid', 'Существующая конфигурация Nginx не проходит проверку. ' + str(failure),
                             details=failure.details)
        if re.search(r'\bstream\s*\{', re.sub(r'#[^\n]*', '', dump.stdout)):
            raise SetupError('nginx_complex_configuration',
                'Nginx содержит маршрутизацию TCP/UDP (stream); совместное использование портов требует отдельной проверки.')
        # A supported include must occur inside http{}, including across lines.
        context, directive, includes = [], [], []
        for token in re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|[^\s{};#]+|[{};]|#[^\n]*', dump.stdout):
            if token.startswith('#'):
                continue
            if token == '{':
                context.append(directive[0] if directive else ''); directive = []
            elif token == '}':
                if context: context.pop()
                directive = []
            elif token == ';':
                if directive[:1] == ['include'] and context == ['http']:
                    includes.extend(item.strip('\"\'') for item in directive[1:])
                directive = []
            else:
                directive.append(token)
        if '/etc/nginx/conf.d/*.conf' not in includes:
            raise SetupError('nginx_include_missing', 'Подготовьте include /etc/nginx/conf.d/*.conf внутри http; основной конфиг не изменён.')
        chunks = re.split(r'^# configuration file (.+):\s*$', dump.stdout, flags=re.M)
        for index in range(1, len(chunks), 2):
            if chunks[index] == paths['nginx']:
                continue
            for names in re.findall(r'\bserver_name\s+([^;]+);', re.sub(r'#[^\n]*', '', chunks[index + 1])):
                for name in names.split():
                    if (name == options.domain
                            or name.startswith('*.') and options.domain.endswith(name[1:]) or name.startswith('~')):
                        raise SetupError('server_name_conflict', 'Домен уже обслуживается другим virtual host; чужая конфигурация не изменена.')


def infrastructure_error(issues, addresses=()):
    """Keep the first stable error code while reporting independent prerequisites together."""
    if issues:
        first = issues[0]
        details = {**first.details, 'server_addresses': list(addresses),
                   'issues': [{'code': item.code, 'message': str(item), 'details': item.details} for item in issues]}
        raise SetupError(first.code, str(first), stage=first.stage, exit_code=first.exit_code, details=details)


def nginx_preflight(system, options, paths, instance, addresses=None):
    addresses = system.addresses() if addresses is None else addresses
    issues = inspect_renewals(system)
    try:
        nginx_configuration_preflight(system, options, paths, instance)
    except SetupError as exc:
        issues.insert(0, exc)
    infrastructure_error(issues, addresses)


def preflight(root, options, system, store):
    system.diagnostic_path = None  # Read-only checks never create diagnostic files.
    signed, trust = publication(root)
    paths = owned_paths(trust['instance_id'])
    saved = store.snapshot()
    for command in ('ip', 'ss', 'systemctl'):
        if not system.which(command):
            raise SetupError('command_missing', 'Не найдена необходимая команда: ' + command)
    addresses = system.addresses()
    if options.backend_bind not in addresses:
        raise SetupError('backend_address_missing', 'Выбранный закрытый адрес отсутствует на этом сервере.')
    resolved = system.resolve(options.domain)
    if not resolved:
        raise SetupError('dns_unavailable', 'DNS не возвращает A или AAAA домена.')
    if options.listen_address and not ipaddress.ip_address(options.listen_address).is_unspecified and options.listen_address not in addresses:
        raise SetupError('listen_address_missing', 'Выбранный публичный listen address отсутствует на сервере.')
    if options.proxy in {'auto', 'managed-nginx'}:
        public_local = {value for value in addresses if ipaddress.ip_address(value).is_global}
        if public_local and any(value not in public_local for value in resolved):
            raise SetupError('dns_address_mismatch', 'A/AAAA указывают не на публичные адреса этого сервера.', details={'dns': resolved, 'server': sorted(public_local)})
        if any(':' in value for value in resolved) and not any(':' in value for value in addresses):
            raise SetupError('ipv6_unavailable', 'DNS содержит AAAA, но на сервере нет IPv6.')
        if options.proxy == 'auto' and any(value not in addresses for value in resolved):
            raise SetupError('dns_address_mismatch', 'Автоматическое подключение требует, чтобы домен указывал на этот сервер.',
                             details={'dns': resolved, 'server': addresses})
    occupied = listeners(system)
    try:
        saved_port = int(saved['web_listen_port']) if saved.get('web_public_origin') and saved.get('web_listen_port') else None
        if saved_port is not None and not 1 <= saved_port <= 65535:
            raise ValueError('port out of range')
    except (TypeError, ValueError):
        raise SetupError('invalid_saved_web_settings', 'Сохранённый внутренний порт некорректен; он не изменён автоматически.') from None
    prepared = None
    if options.proxy == 'auto':
        prepared = prepared_local_nginx(system, options, paths, resolved, occupied, options.backend_port or saved_port)
        options = replace(options, proxy='external' if prepared else 'managed-nginx',
                          backend_port=prepared['port'] if prepared else options.backend_port)
    candidates = [options.backend_port or saved_port] if options.backend_port or saved_port else range(18764, 18785)
    for port in candidates:
        busy = [item for item in occupied if item['port'] == port and overlaps(item['host'], options.backend_bind)]
        own = False
        if busy and saved.get('web_enabled') == '1' and saved_port == port and saved.get('web_listen_host', '127.0.0.1') in {None, options.backend_bind}:
            try:
                host = '[' + options.backend_bind + ']' if ':' in options.backend_bind else options.backend_bind
                verify_endpoint(system, f'http://{host}:{port}', signed, trust, package=False)
                own = True
            except SetupError:
                pass
        if own or not busy and system.can_bind(options.backend_bind, port):
            options = replace(options, backend_port=port)
            break
    else:
        raise SetupError('backend_port_conflict', 'Выбранный внутренний порт занят; сохранённый или явный порт автоматически не меняется.')
    if options.proxy == 'managed-nginx':
        for command in (('apt-get',) if not system.which('nginx') or not system.which('certbot') else ()):
            if not system.which(command):
                raise SetupError('command_missing', 'Не найдена необходимая команда: ' + command)
        issues = []
        public_hosts = [options.listen_address] if options.listen_address else ['0.0.0.0', '::']
        if options.backend_port in {80, options.https_port} and any(overlaps(options.backend_bind, host) for host in public_hosts):
            raise SetupError('backend_port_conflict', 'Внутренний listener пересекается с публичным Nginx listener.')
        for item in occupied:
            if item['port'] in {80, options.https_port} and any(overlaps(item['host'], host) for host in public_hosts):
                if not item['process'] or 'nginx' not in item['process'] or 'docker' in item['process']:
                    service = item['process'][:200]
                    issues.append(SetupError('public_port_conflict',
                        f'TCP-порт {item["port"]}, адрес {item["host"]}, уже занят: {service or "процесс не определён"}. '
                        'Для Nginx нужен свободный порт. Перенос VPN, панели или другого прокси требует '
                        'отдельной настройки; службы не изменены.',
                        details={'address': item['host'], 'port': item['port'], 'service': service}))
        renewal_issues = inspect_renewals(system)
        try:
            nginx_configuration_preflight(system, options, paths, trust['instance_id'])
        except SetupError as exc:
            issues.append(exc)
        issues.extend(renewal_issues)
        try:
            timer = renewal_timer(system)
        except SetupError as exc:
            issues.append(exc)
        infrastructure_error(issues, addresses)
        if not usable_certificate(system, paths, options.domain, min_days=-36500) and (not options.email or not options.agree_tos):
            raise SetupError('acme_arguments_required',
                'Для новой выдачи нужен --agree-tos; email по умолчанию admin@<домен>, другой можно задать через --email.',
                stage='arguments', exit_code=2)
    return options, signed, trust, saved, paths, {'dns': resolved, 'server_addresses': addresses,
        'existing_local_proxy': prepared['file'] if prepared else None,
        'renewal_timer': timer if options.proxy == 'managed-nginx' else None,
        'tls_renewal': 'managed' if options.proxy == 'managed-nginx' else 'external_owner',
        'certificate_name': certificate_name(system, paths, options.domain) if options.proxy == 'managed-nginx' else None,
        'upstream': 'http://' + ('[' + options.backend_bind + ']' if ':' in options.backend_bind else options.backend_bind) + ':' + str(options.backend_port)}


def certificate_name(system, paths, domain):
    return lineage_name(paths, domain)


def usable_certificate(system, paths, domain, *, min_days=30, cert_name=None):
    from cryptography import x509
    from datetime import datetime, timedelta, timezone
    base = system.path(paths['certbot'])
    cert_name = cert_name or certificate_name(system, paths, domain)
    cert = base / 'live' / cert_name / 'fullchain.pem'
    key = base / 'live' / cert_name / 'privkey.pem'
    try:
        archive = system_path(system, paths['certbot'] + '/archive/' + cert_name)
        system_path(system, paths['certbot'] + '/live/' + cert_name)
        if cert.resolve().parent != archive.resolve() or key.resolve().parent != archive.resolve() or not key.is_file():
            return False
        value = x509.load_pem_x509_certificate(cert.read_bytes())
        names = value.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        return set(names) == {domain} and value.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=min_days)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
