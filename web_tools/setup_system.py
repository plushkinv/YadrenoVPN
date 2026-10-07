"""Injectable Linux commands and bounded I/O for the installation workflow."""
from __future__ import annotations

import contextlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener

from web_tools.setup_options import SetupError
from web_tools.paths import atomic_write, local_path
from web_tools.setup_paths import marker, owned_paths

NGINX_CONFIG_HEADER = 'X-Yadreno-Web-Config'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise SetupError('unexpected_redirect', 'Проверяемый адрес перенаправляет на другой URL.', stage='https', exit_code=4)


class System:
    """All subprocesses are noninteractive; tests replace this narrow boundary."""
    def __init__(self, filesystem=Path('/')):
        self.filesystem = Path(filesystem)
        self.diagnostic_path = None

    def path(self, absolute):
        if not absolute.startswith('/') or '..' in Path(absolute).parts:
            raise ValueError('absolute system path required')
        return self.filesystem / absolute.lstrip('/')

    def which(self, name):
        return shutil.which(name)

    def run(self, arguments, *, check=True, timeout=30):
        if not self.which(arguments[0]):
            raise SetupError('command_missing', 'Не найдена необходимая команда: ' + arguments[0])
        print('Выполняется: ' + ' '.join(arguments[:2]), file=sys.stderr)
        try:
            completed = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=timeout,
                env={**os.environ, 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'})
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = 'Превышено время ожидания.' if isinstance(exc, subprocess.TimeoutExpired) else 'Не удалось запустить команду.'
            raise self.command_error(arguments, reason, None) from None
        if check and completed.returncode:
            raise self.command_error(arguments, completed.stderr, completed.returncode)
        return completed

    def command_error(self, arguments, stderr, returncode):
        """Report bounded causes; never copy arbitrary command output into a handoff."""
        reason = 'Подробности доступны в журнале системной команды.'
        causes = (
            ('unrecognized arguments', 'Установленная версия команды не поддерживает переданные параметры.'),
            ('permission denied', 'Недостаточно прав для выполнения команды.'),
            ('address already in use', 'Требуемый сетевой порт занят.'),
            ('rate limit', 'Сервис ограничил частоту запросов.'),
            ('unauthorized', 'Проверка владения доменом не пройдена.'),
            ('nxdomain', 'Домен не найден в DNS.'),
            ('connection refused', 'Не удалось подключиться к требуемому сервису.'),
            ('timed out', 'Превышено время ожидания.'),
        )
        for needle, message in causes:
            if needle in stderr.lower():
                reason = message
                break
        if returncode is None:
            reason = stderr
        details = {'command': Path(arguments[0]).name, 'returncode': returncode, 'reason': reason}
        if self.diagnostic_path is not None:
            # The log is deliberately bounded and sanitized too: nginx -T and
            # ACME stderr may contain arbitrary neighbouring secrets.
            flags = sorted(set(re.findall(r'--[a-z][a-z-]{1,60}\b', stderr))) if 'unrecognized arguments' in stderr else []
            try:
                atomic_write(self.diagnostic_path, (json.dumps({**details, 'unsupported_options': flags}, ensure_ascii=False) + '\n').encode())
                details['log'] = str(self.diagnostic_path)
            except OSError:
                pass
        if Path(arguments[0]).name == 'certbot':
            details['service_log'] = '/var/log/letsencrypt/letsencrypt.log'
        elif Path(arguments[0]).name in {'nginx', 'systemctl'}:
            details['service_log'] = 'journalctl -u nginx -u certbot.service -u yadreno-vpn'
        return SetupError('command_failed', 'Команда ' + details['command'] + ' завершилась с ошибкой'
                          + (f' (код {returncode})' if returncode is not None else '') + '. ' + reason,
                          stage='apply', exit_code=4, details=details)

    def resolve(self, domain):
        try:
            return sorted({str(ipaddress.ip_address(item[4][0])) for item in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)})
        except OSError:
            raise SetupError('dns_unavailable', 'DNS не возвращает адреса домена. Проверьте A и AAAA.') from None

    def addresses(self):
        rows = json.loads(self.run(['ip', '-j', 'address', 'show']).stdout)
        return sorted({item['local'] for row in rows for item in row.get('addr_info', []) if item.get('family') in {'inet', 'inet6'}})

    def fetch(self, url, *, limit=2 * 1024 * 1024, nginx_configuration=None):
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
        try:
            with opener.open(url, timeout=15) as response:
                if response.status != 200:
                    raise ValueError('unexpected HTTP status')
                if (nginx_configuration is not None
                        and response.headers.get_all(NGINX_CONFIG_HEADER, []) != [nginx_configuration]):
                    raise SetupError('nginx_configuration_not_applied',
                        'Nginx ещё не подтвердил применение выбранной конфигурации.', stage='https', exit_code=4)
                content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError('response too large')
            return content
        except SetupError:
            raise
        except (OSError, ValueError):
            raise SetupError('https_unavailable', 'Адрес недоступен или HTTPS-сертификат не прошёл проверку.', stage='https', exit_code=4) from None

    def verify_uploads(self, origin):
        import asyncio
        from web_tools.setup_uploads import verify_uploads
        asyncio.run(verify_uploads(origin))

    def can_bind(self, host, port):
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            try:
                # Match asyncio's Linux TCP listener: a stopped server's
                # TIME_WAIT connections must not look like a live conflict.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((host, port))
                return True
            except OSError:
                return False

    def active(self, service):
        return self.run(['systemctl', 'is-active', '--quiet', service], check=False).returncode == 0

    def enabled(self, service):
        return self.run(['systemctl', 'is-enabled', '--quiet', service], check=False).returncode == 0


def system_path(system, absolute):
    """Owned writes never follow symlinks, including a linked parent directory."""
    path = system.path(absolute)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise SetupError('unsafe_owned_path', 'Управляемый системный путь содержит symbolic link.')
    return path


def verify_owned_file(system, path, instance_id):
    target = system_path(system, path)
    if target.exists() and (not target.is_file() or not target.read_bytes().startswith(marker(instance_id).encode())):
        raise SetupError('owned_file_collision', 'Управляемое имя занято чужим файлом; файл не изменён.', details={'path': path})
    return target


@contextlib.contextmanager
def setup_lock(root, *, optional=False):
    """A live setup may restart the runtime; its startup recovery must then skip."""
    import fcntl
    path = local_path(Path(root) / 'web_runtime', 'locks', directory=True) / 'setup.lock'
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if optional:
                yield False
                return
            raise SetupError('setup_busy', 'Другая настройка веба ещё выполняется.') from None
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def write_owned(system, path, content, instance_id):
    target = verify_owned_file(system, path, instance_id)
    atomic_write(target, marker(instance_id).encode() + content.encode(), mode=0o644)
