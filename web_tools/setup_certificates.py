"""Read-only renewal conflicts and the stock Certbot scheduler."""
from __future__ import annotations

import base64
import re
import shlex

from web_tools.setup_options import SetupError
from web_tools.setup_paths import renewal_profile

STANDALONE = re.compile(r'authenticator\s*=\s*standalone|--(?:standalone|alpn)\b|Le_Webroot=["\']?(?:no|standalone|alpn)["\']?(?:\s|$)')


def deploy_hook(system):
    return shlex.quote(system.which('nginx') or '/usr/sbin/nginx') + ' -t && ' + shlex.quote(system.which('systemctl')) + ' reload nginx'


def renewal_timer(system):
    """Do not mix a PATH client with a different package's unattended scheduler."""
    binary = system.which('certbot')
    if not binary:
        return None
    resolved = system.path(binary).resolve()
    if binary == '/snap/bin/certbot' or resolved == system.path('/usr/bin/snap').resolve():
        timer, service = 'snap.certbot.renew.timer', 'snap.certbot.renew.service'
        expected = r'/usr/bin/snap run(?: --timer=(?:"[^";]+"|[^\s;]+))? certbot\.renew'
    elif resolved == system.path('/usr/bin/certbot').resolve():
        timer, service = 'certbot.timer', 'certbot.service'
        expected = re.escape('/usr/bin/certbot -q renew') + r'(?: --no-random-sleep-on-renew)?'
    else:
        raise SetupError('renewal_scheduler_unknown', 'Способ установки Certbot не распознан; подготовьте штатное продление вручную.')
    load = system.run(['systemctl', 'show', timer, '--property=LoadState', '--value'], check=False)
    unit = system.run(['systemctl', 'show', service, '--property=ExecStart', '--value'], check=False)
    if (load.returncode or load.stdout.strip() != 'loaded' or unit.returncode
            or not re.search(r'argv\[\]=' + expected + r'\s*;', unit.stdout)):
        raise SetupError('renewal_scheduler_unknown', 'Не найдено штатное расписание установленного Certbot. '
                         'Восстановите его средствами пакета или подготовьте HTTPS-прокси.', details={'timer': timer})
    return timer


def _unknown_hooks(text, safe):
    for _, raw in re.findall(r'^\s*(Le_(?:PreHook|PostHook|RenewHook|DeployHook|ReloadCmd)|(?:pre|post|deploy|renew)_hook)\s*=\s*(.*)$', text, re.M):
        value = raw.strip().strip("'\"")
        prefix, suffix = '__ACME_BASE64__START_', '__ACME_BASE64__END_'
        if value.startswith(prefix) and value.endswith(suffix):
            try:
                value = base64.b64decode(value[len(prefix):-len(suffix)], validate=True).decode('utf-8')
            except (ValueError, UnicodeError):
                return True
        if value not in safe:
            return True
    return False


def inspect_renewals(system):
    """Report known port/hook conflicts; never execute or rewrite foreign profiles."""
    issues = []
    safe = {'', 'systemctl restart x-ui', 'x-ui restart',
            'systemctl restart x-ui 2>/dev/null || rc-service x-ui restart 2>/dev/null || true',
            'systemctl reload nginx', '/bin/systemctl reload nginx',
            'nginx -s reload', '/usr/sbin/nginx -s reload'}
    if system.which('nginx'):
        safe.add(deploy_hook(system))
    patterns = ('etc/letsencrypt/renewal/*.conf', 'root/.acme.sh/*/*.conf', 'home/*/.acme.sh/*/*.conf',
                'etc/cron.d/*', 'var/spool/cron/crontabs/*', 'etc/systemd/system/*.service')
    for pattern in patterns:
        for path in sorted(system.filesystem.glob(pattern)):
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            text = '\n'.join(line for line in path.read_text(errors='replace').splitlines() if not line.lstrip().startswith('#'))
            hooks = _unknown_hooks(text, safe)
            if not STANDALONE.search(text) and not hooks:
                continue
            relative = '/' + path.relative_to(system.filesystem).as_posix()
            reason = 'не распознаны дополнительные команды продления' if hooks else 'продление самостоятельно занимает публичный порт'
            port = 443 if re.search(r'--alpn\b|Le_Webroot=["\']alpn', text) else 80
            issues.append(SetupError('renewal_conflict',
                f'Настройка продления {relative}: {reason}. Требуется проверить совместимость с Nginx на TCP-порту {port}.',
                details={'file': relative.lstrip('/'), 'port': port}))
    return issues


def check_profile(system, paths, name):
    """An existing namespaced certificate must still use our webroot and hook."""
    from web_tools.setup_system import system_path
    path = system_path(system, paths['certbot'] + '/renewal/' + name + '.conf')
    if not path.exists():
        if any(system.path(paths['certbot'] + '/' + section + '/' + name).exists() for section in ('live', 'archive')):
            raise SetupError('certificate_directory_collision', 'Имя сертификата занято без соответствующего профиля продления.')
        return
    try:
        params = renewal_profile(path.read_text(), paths, name)
        if params.get('renew_hook', params.get('deploy_hook')) != deploy_hook(system):
            raise ValueError('unexpected deploy hook')
    except (ValueError, KeyError, OSError):
        raise SetupError('certificate_directory_collision', 'Профиль сертификата не соответствует этой установке; '
                         'существующие файлы не изменены.', details={'file': str(path)}) from None
