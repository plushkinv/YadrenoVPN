"""Apply only owned web configuration, with a durable rollback journal."""
from __future__ import annotations

import base64
from dataclasses import asdict
import json
from pathlib import Path
import re
import time

from web_tools.setup_options import SetupError, result
from web_tools.setup_certificates import check_profile, deploy_hook, renewal_timer
from web_tools.setup_preflight import SETTING_KEYS, SettingsStore, nginx_preflight, usable_certificate, verify_endpoint
from web_tools.setup_system import NGINX_CONFIG_HEADER, System, marker, owned_paths, setup_lock, system_path, verify_owned_file, write_owned
from web_tools.package import digest
from web_tools.paths import PROJECT_ROOT, atomic_write, canonical, local_path

SERVICE = 'yadreno-vpn'
JOURNAL = 'secrets/setup-transaction.json'
OWNED_FILES = ('nginx',)


def _journal_path(root):
    return local_path(Path(root) / 'web_runtime', JOURNAL)


def _read_state(root):
    path = local_path(Path(root) / 'web_runtime', 'setup.json')
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def _snapshot(root, options, trust, saved, paths, system, metadata):
    files = {}
    if options.proxy == 'managed-nginx':
        for name in OWNED_FILES:
            path = verify_owned_file(system, paths[name], trust['instance_id'])
            files[name] = {'bytes': base64.b64encode(path.read_bytes()).decode(), 'mode': path.stat().st_mode & 0o777} if path.exists() else None
    value = {'format_version': 1, 'instance_id': trust['instance_id'], 'phase': 'applying',
        'settings': saved, 'files': files, 'setup': _read_state(root), 'target': metadata,
        'services': {'bot': system.active(SERVICE), 'nginx': system.active('nginx') if options.proxy == 'managed-nginx' else None}}
    atomic_write(_journal_path(root), canonical(value))
    return value


def _validated_journal(root):
    value = json.loads(_journal_path(root).read_bytes())
    trust = json.loads(local_path(Path(root) / 'web_runtime', 'identity.json').read_bytes())
    if (not isinstance(value, dict) or set(value) != {'format_version', 'instance_id', 'phase', 'settings', 'files', 'setup', 'target', 'services'}
            or value['format_version'] != 1 or value['instance_id'] != trust['instance_id']
            or value['phase'] not in {'applying', 'verified'}):
        raise ValueError('invalid setup journal')
    paths = owned_paths(value['instance_id'])
    if (not isinstance(value['settings'], dict) or set(value['settings']) != set(SETTING_KEYS)
            or any(item is not None and (not isinstance(item, str) or len(item) > 4096) for item in value['settings'].values())
            or not isinstance(value['files'], dict) or set(value['files']) not in (set(), set(OWNED_FILES))
            or not isinstance(value['services'], dict) or set(value['services']) != {'bot', 'nginx'}
            or type(value['services']['bot']) is not bool
            or any(item is not None and type(item) is not bool for item in value['services'].values())):
        raise ValueError('invalid setup journal fields')
    for item in value['files'].values():
        if item is None:
            continue
        if not isinstance(item, dict) or set(item) != {'bytes', 'mode'} or type(item['mode']) is not int or item['mode'] & ~0o777:
            raise ValueError('invalid setup file snapshot')
        content = base64.b64decode(item['bytes'], validate=True)
        if len(content) > 1024 * 1024 or not content.startswith(marker(value['instance_id']).encode()):
            raise ValueError('foreign setup snapshot')
    target = value['target']
    if not isinstance(target, dict) or target.get('format_version') != 1 or target.get('instance_id') != value['instance_id'] or target.get('owned') != paths:
        raise ValueError('invalid setup target identity')
    if value['setup'] is not None:
        previous = base64.b64decode(value['setup'], validate=True)
        if len(previous) > 1024 * 1024:
            raise ValueError('invalid previous setup metadata')
    return value, paths


def _finish(root, journal, store):
    # The durable verified phase is written only after exact public HTTPS proof.
    target = journal['target']
    atomic_write(local_path(Path(root) / 'web_runtime', 'setup.json'), canonical(target))
    button = store.enable_button()
    _journal_path(root).unlink()
    return button


def _restore(root, journal, paths, system, store, *, startup=False):
    for name, item in journal['files'].items():
        target = verify_owned_file(system, paths[name], journal['instance_id'])
        if item is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, base64.b64decode(item['bytes'], validate=True), mode=item['mode'])
    store.restore(journal['settings'])
    state = local_path(Path(root) / 'web_runtime', 'setup.json')
    if journal['setup'] is None:
        state.unlink(missing_ok=True)
    else:
        atomic_write(state, base64.b64decode(journal['setup'], validate=True))
    if journal['files']:
        if journal['services']['nginx']:
            system.run(['nginx', '-t'])
            system.run(['systemctl', 'reload-or-restart', 'nginx'])
        elif system.which('nginx'):
            system.run(['systemctl', 'stop', 'nginx'])
    if not startup:
        system.run(['systemctl', 'restart' if journal['services']['bot'] else 'stop', SERVICE])
    _journal_path(root).unlink()


def recover_interrupted_setup(root=PROJECT_ROOT, *, system=None, store=None, startup=False, locked=False):
    if not _journal_path(root).exists():
        return False
    system, store = system or System(), store or SettingsStore()
    if not locked:
        with setup_lock(root, optional=startup) as acquired:
            return recover_interrupted_setup(root, system=system, store=store, startup=startup, locked=True) if acquired else False
    try:
        journal, paths = _validated_journal(root)
        if journal['phase'] == 'verified':
            from runtime.readiness import is_active
            if startup and not is_active():
                # Enabling the public button is a normal maintenance write, so
                # finish only after the runtime acceptance fence opens.
                return False
            _finish(root, journal, store)
        else:
            _restore(root, journal, paths, system, store, startup=startup)
    except (ValueError, KeyError, TypeError, OSError):
        raise SetupError('setup_recovery_invalid', 'Журнал настройки повреждён; произвольные файлы не восстанавливались.', stage='recovery', exit_code=4) from None
    return True


def finish_verified_setup(root=PROJECT_ROOT, *, system=None, store=None, startup=False):
    """Finish a previously proved setup after acceptance, without late rollback."""
    if not _journal_path(root).exists():
        return False
    with setup_lock(root, optional=startup) as acquired:
        if not acquired:
            return False
        journal, _ = _validated_journal(root)
        if journal['phase'] != 'verified':
            return False
        _finish(root, journal, store or SettingsStore())
        return True


def _listen_lines(options, addresses, port, suffix=''):
    hosts = [options.listen_address] if options.listen_address else ['0.0.0.0'] + (['::'] if any(':' in item for item in addresses) else [])
    return '\n'.join('    listen ' + ('[' + host + ']' if ':' in host else host) + ':' + str(port) + suffix + ';' for host in hosts)


def _nginx(root, options, paths, facts, *, challenge=False):
    template = Path(root) / 'deploy/nginx' / ('yadreno-web-http.conf.template' if challenge else 'yadreno-web.conf.template')
    values = {'HTTP_LISTEN': _listen_lines(options, facts['server_addresses'], 80),
              'HTTPS_LISTEN': _listen_lines(options, facts['server_addresses'], options.https_port, ' ssl'),
              'DOMAIN': options.domain, 'PUBLIC_URL': options.public_url, 'ACME_ROOT': paths['acme'],
              'CERT_ROOT': paths['certbot'] + '/live/' + str(facts['certificate_name']), 'UPSTREAM': facts['upstream']}
    text = template.read_text(encoding='utf-8')
    for name, value in values.items():
        text = text.replace('@' + name + '@', value)
    return text


def _managed_configuration(root, options, paths, facts, instance):
    text = _nginx(root, options, paths, facts)
    placeholder = '@CONFIG_HEADER@\n'
    if text.count(placeholder) != 1:
        raise ValueError('managed Nginx template has no unique configuration proof location')
    # Include ownership and every rendered directive, excluding only the proof
    # header itself. The proxy location hides an upstream header with this name.
    fingerprint = digest((marker(instance) + text.replace(placeholder, '')).encode())
    header = '        add_header ' + NGINX_CONFIG_HEADER + ' "' + fingerprint + '" always;\n'
    return text.replace(placeholder, header), fingerprint


def _verify_ready(system, origin, signed, trust, *, runtime, nginx_configuration=None):
    deadline = time.monotonic() + 60
    while True:
        try:
            verify_endpoint(system, origin, signed, trust, runtime=runtime, nginx_configuration=nginx_configuration)
            break
        except SetupError as exc:
            if exc.code not in {'https_unavailable', 'nginx_configuration_not_applied'} or time.monotonic() >= deadline:
                raise
            time.sleep(1)
    # Upload admission is checked once, outside the bounded Nginx reload wait.
    system.verify_uploads(origin)


def _reload_nginx(system):
    system.run(['nginx', '-t'])
    system.run(['systemctl', 'reload' if system.active('nginx') else 'start', 'nginx'])


def _managed(root, options, system, trust, paths, facts, configuration):
    missing = [name for name in ('nginx', 'certbot') if not system.which(name)]
    if missing:
        system.run(['apt-get', 'update', '-qq'], timeout=600)
        system.run(['apt-get', 'install', '-y', '-o', 'Dpkg::Options::=--force-confold', *missing], timeout=600)
    nginx_preflight(system, options, paths, trust['instance_id'])
    timer = renewal_timer(system)
    facts['renewal_timer'] = timer
    check_profile(system, paths, facts['certificate_name'])
    acme = system_path(system, paths['acme'])
    acme.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    acme.mkdir(mode=0o755, exist_ok=True)
    if not usable_certificate(system, paths, options.domain):
        current = verify_owned_file(system, paths['nginx'], trust['instance_id'])
        # Preserve the old HTTPS vhost during a new domain's certificate request.
        previous = current.read_text() if current.exists() else ''
        challenge = _nginx(root, options, paths, facts, challenge=True)
        if not re_domain_present(previous, options.domain):
            previous += '\n' + challenge
        write_owned(system, paths['nginx'], previous or challenge, trust['instance_id'])
        _reload_nginx(system)
        if usable_certificate(system, paths, options.domain, min_days=-36500):
            system.run(['certbot', 'renew', '--non-interactive', '--cert-name', facts['certificate_name'], '--no-random-sleep-on-renew'], timeout=300)
        else:
            system.run(['certbot', 'certonly', '--non-interactive', '--webroot', '--webroot-path', paths['acme'],
                '--cert-name', facts['certificate_name'], '--domain', options.domain, '--email', options.email,
                '--agree-tos', '--keep-until-expiring', '--deploy-hook', deploy_hook(system)], timeout=300)
        if not usable_certificate(system, paths, options.domain, min_days=0):
            raise SetupError('certificate_unusable', 'Сертификат не соответствует домену или сроку.', stage='certificate', exit_code=4)
    write_owned(system, paths['nginx'], configuration, trust['instance_id'])
    _reload_nginx(system)
    system.run(['snap', 'start', '--enable', 'certbot.renew'] if timer.startswith('snap.')
               else ['systemctl', 'enable', '--now', timer])
    system.run(['certbot', 'renew', '--non-interactive', '--dry-run', '--no-random-sleep-on-renew',
                '--cert-name', facts['certificate_name']], timeout=300)
    # Dry runs intentionally do not execute deploy hooks on older Certbot.
    # Validate the same two commands explicitly, without version-specific flags.
    _reload_nginx(system)
    if not system.active(timer) or not system.enabled(timer):
        raise SetupError('renewal_not_ready', 'Штатное автопродление Certbot не включено или не работает.', stage='renewal', exit_code=4)


def re_domain_present(text, domain):
    return any(domain in names.split() for names in re.findall(r'\bserver_name\s+([^;]+);', text))


def apply(root, checked, system, store):
    options, signed, trust, saved, paths, facts = checked
    system.diagnostic_path = local_path(Path(root) / 'web_runtime', 'secrets/setup-command.log')
    configuration, fingerprint = (_managed_configuration(root, options, paths, facts, trust['instance_id'])
                                  if options.proxy == 'managed-nginx' else (None, None))
    metadata = {'format_version': 1, 'instance_id': trust['instance_id'], 'options': asdict(options), 'owned': paths,
                'build_id': signed['manifest']['build_id'], 'content_hash': signed['manifest']['content_hash'],
                'tls_renewal': facts['tls_renewal'], 'certificate_name': facts['certificate_name']}
    metadata['options']['trusted_proxies'] = list(options.trusted_proxies)
    state_path = local_path(Path(root) / 'web_runtime', 'setup.json')
    if state_path.exists():
        previous = json.loads(state_path.read_bytes())
        ignored = {'email', 'agree_tos', 'check_only'}
        same_options = {key: value for key, value in previous.get('options', {}).items() if key not in ignored} == {
            key: value for key, value in metadata['options'].items() if key not in ignored}
        same_settings = saved == {'web_enabled': '1', 'web_listen_host': options.backend_bind,
            'web_listen_port': str(options.backend_port), 'web_public_origin': options.public_url,
            'web_trusted_proxies': json.dumps(options.trusted_proxies)}
        same_system = options.proxy == 'external' or (
            facts['renewal_timer'] and usable_certificate(system, paths, options.domain) and system.active('nginx')
            and system.active(facts['renewal_timer']) and system.enabled(facts['renewal_timer'])
            and verify_owned_file(system, paths['nginx'], trust['instance_id']).is_file()
            and verify_owned_file(system, paths['nginx'], trust['instance_id']).read_text() == marker(trust['instance_id']) + configuration)
        if same_options and same_settings and same_system and system.active(SERVICE):
            _verify_ready(system, options.public_url, signed, trust, runtime=Path(root) / 'web_runtime', nginx_configuration=fingerprint)
            button = store.enable_button()
            return result(options, ok=button['code'] == 'ready', code='ready' if button['code'] == 'ready' else 'web_button_unavailable',
                stage='complete', changed=button['changed'], button=button, publication={'instance_id': trust['instance_id'],
                'build_id': metadata['build_id'], 'content_hash': metadata['content_hash']}, **facts)
    journal = _snapshot(root, options, trust, saved, paths, system, metadata)
    try:
        if options.proxy == 'managed-nginx':
            _managed(root, options, system, trust, paths, facts, configuration)
        store.configure(options)
        system.run(['systemctl', 'restart', SERVICE], timeout=90)
        _verify_ready(system, options.public_url, signed, trust, runtime=Path(root) / 'web_runtime', nginx_configuration=fingerprint)
        journal['phase'] = 'verified'
        atomic_write(_journal_path(root), canonical(journal))
        button = _finish(root, journal, store)
        return result(options, ok=button['code'] == 'ready', code='ready' if button['code'] == 'ready' else 'web_button_unavailable',
            stage='complete', changed=True, button=button, publication={'instance_id': trust['instance_id'],
            'build_id': metadata['build_id'], 'content_hash': metadata['content_hash']}, **facts)
    except Exception:
        if journal['phase'] == 'applying':
            # Keep the journal when rollback itself fails, for startup/CLI recovery.
            _restore(root, journal, paths, system, store)
        raise
