"""Copyable operator instructions for bounded setup failures, without secrets."""
from __future__ import annotations

from localization import text as _tr

import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import sys
import textwrap
import uuid

from web_api.settings import DEFAULT_TRUSTED_PROXIES

HEADING = _tr('setup_diagnostics.module.1')
ADMIN_URL = 'https://t.me/YadrenoAdmin_Bot'
COPY_START = _tr('setup_diagnostics.module.2')
COPY_END = _tr('setup_diagnostics.module.3')


def _steps(code, details, options):
    if code == 'public_port_conflict':
        port = details.get('port', options.https_port if options else 443)
        service = str(details.get('service', ''))
        steps = [_tr('setup_diagnostics.steps.1', p0=f'{port}')]
        if 'docker' in service:
            steps.append(_tr('setup_diagnostics.steps.2'))
        elif 'xray' in service or 'x-ui' in service:
            steps.append(_tr('setup_diagnostics.steps.3'))
        else:
            steps.append(_tr('setup_diagnostics.steps.4'))
        steps.append(_tr('setup_diagnostics.steps.5'))
        return steps
    if code == 'renewal_conflict':
        port = details.get('port')
        renewal = (_tr('setup_diagnostics.steps.6'))
        if port in {80, 443}:
            renewal = (_tr('setup_diagnostics.steps.7', p0=f'{port}'))
        return [_tr('setup_diagnostics.steps.8'),
                renewal,
                _tr('setup_diagnostics.steps.9'),
                _tr('setup_diagnostics.steps.10')]
    if code in {'nginx_invalid', 'nginx_include_missing', 'nginx_complex_configuration', 'server_name_conflict'}:
        return [_tr('setup_diagnostics.steps.11'),
                _tr('setup_diagnostics.steps.12'),
                _tr('setup_diagnostics.steps.13')]
    if code in {'dns_unavailable', 'dns_address_mismatch', 'ipv6_unavailable'}:
        return [_tr('setup_diagnostics.steps.14'),
                _tr('setup_diagnostics.steps.15'),
                _tr('setup_diagnostics.steps.16')]
    if code in {'backend_port_conflict', 'invalid_saved_web_settings', 'backend_address_missing', 'listen_address_missing'}:
        return [_tr('setup_diagnostics.steps.17'),
                _tr('setup_diagnostics.steps.18')]
    if code in {'owned_file_collision', 'certificate_directory_collision', 'unsafe_owned_path'}:
        return [_tr('setup_diagnostics.steps.19'),
                _tr('setup_diagnostics.steps.20')]
    if code in {'https_unavailable', 'unexpected_redirect', 'installation_mismatch', 'nginx_configuration_not_applied'}:
        return [_tr('setup_diagnostics.steps.21'),
                _tr('setup_diagnostics.steps.22'),
                _tr('setup_diagnostics.steps.23')]
    if code == 'upload_limit_too_small':
        return [_tr('setup_diagnostics.steps.24'),
                _tr('setup_diagnostics.steps.25')]
    if code == 'upload_check_failed':
        return [_tr('setup_diagnostics.steps.26'),
                _tr('setup_diagnostics.steps.27')]
    if code in {'certificate_unusable', 'renewal_not_ready', 'renewal_scheduler_unknown'}:
        return [_tr('setup_diagnostics.steps.28'),
                _tr('setup_diagnostics.steps.29')]
    if code in {'setup_incomplete', 'setup_recovery_invalid'}:
        return [_tr('setup_diagnostics.steps.30'),
                _tr('setup_diagnostics.steps.31')]
    if code == 'ui_not_ready':
        return [_tr('setup_diagnostics.steps.32'),
                _tr('setup_diagnostics.steps.33')]
    if code in {'setup_busy', 'update_operation_busy'}:
        return [_tr('setup_diagnostics.steps.34')]
    if code in {'command_missing', 'command_failed', 'listeners_unknown'}:
        return [_tr('setup_diagnostics.steps.35'),
                _tr('setup_diagnostics.steps.36'),
                _tr('setup_diagnostics.steps.37')]
    return [_tr('setup_diagnostics.steps.38'),
            _tr('setup_diagnostics.steps.39')]


def _retry(root, options, *, prepared=False):
    proxy = 'external' if prepared else options.proxy
    args = ['bash', str(Path(root) / 'install.sh'), 'web-setup']
    if proxy != 'auto':
        args += ['--proxy', proxy]
    if proxy in {'auto', 'managed-nginx'}:
        args += ['--domain', options.domain, '--agree-tos']
        if options.email and options.email != 'admin@' + options.domain:
            args += ['--email', options.email]
    else:
        args += ['--public-url', options.public_url]
    if options.https_port != 443:
        args += ['--https-port', str(options.https_port)]
    if options.listen_address:
        args += ['--listen-address', options.listen_address]
    if options.backend_port:
        args += ['--backend-port', str(options.backend_port)]
    if options.backend_bind != '127.0.0.1':
        args += ['--backend-bind', options.backend_bind]
    if options.trusted_proxies != DEFAULT_TRUSTED_PROXIES:
        for address in options.trusted_proxies:
            args += ['--trusted-proxy', address]
    return args


def _command(args):
    """Wrap only between whole shell-quoted arguments, preserving paste-and-run."""
    lines, current = [], ''
    for arg in args:
        word = shlex.quote(arg)
        if current and len(current) + len(word) + 1 > 90:
            lines.append(current + ' \\')
            current = '  ' + word
        else:
            current += (' ' if current else '') + word
    return '\n'.join([*lines, current])


def _agent_commands(args, root):
    """Print a detached invocation and its journal; never start it here."""
    unit = 'yadreno-web-setup-' + uuid.uuid4().hex + '.service'
    # systemd expands dollars in arguments, but resolves the executable literally.
    command = ['systemd-run', '--unit=' + unit, '--collect', '--service-type=exec',
               '--working-directory=' + str(root), '--property=StandardOutput=journal',
               '--property=StandardError=journal', '--',
               args[0], *(arg.replace('$', '$$') for arg in args[1:]), '--output', 'json']
    return [_command(command), '', _tr('setup_diagnostics.agent_commands.1'),
            _command(['journalctl', '--unit=' + unit, '--no-pager', '--output=cat', '--lines=80'])]


def manual_setup_report(payload, options, root):
    """The same complete report is delivered as text and in the JSON message."""
    details = payload.get('details', {})
    issues = details.get('issues') or [{'code': payload['code'], 'message': payload.get('message', ''), 'details': details}]
    lines = [HEADING, '', _tr('setup_diagnostics.manual_setup_report.1'),
             ADMIN_URL, '', _tr('setup_diagnostics.manual_setup_report.2'),
             _tr('setup_diagnostics.manual_setup_report.3'), '', COPY_START, '',
             _tr('setup_diagnostics.manual_setup_report.4'),
             _tr('setup_diagnostics.manual_setup_report.5') + str(root),
             _tr('setup_diagnostics.manual_setup_report.6') + str(Path(root).absolute() / 'deploy/nginx/yadreno-web.conf.template'),
             _tr('setup_diagnostics.manual_setup_report.7')]
    addresses = details.get('server_addresses') or details.get('server') or []
    visible = []
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_loopback:
                visible.append(address)
        except ValueError:
            pass
    if visible:
        lines.append(_tr('setup_diagnostics.manual_setup_report.8') + ', '.join(visible))
    if options:
        lines.extend([_tr('setup_diagnostics.manual_setup_report.9') + options.domain, _tr('setup_diagnostics.manual_setup_report.10') + options.public_url,
                      _tr('setup_diagnostics.manual_setup_report.11') + {'auto': _tr('setup_diagnostics.manual_setup_report.12'),
                          'managed-nginx': _tr('setup_diagnostics.manual_setup_report.13'), 'external': _tr('setup_diagnostics.manual_setup_report.14')}[options.proxy]])
        retry = _retry(root, options)
        lines += ['', _tr('setup_diagnostics.manual_setup_report.15'),
                  '', _command([*retry, '--check-only']),
                  '', _tr('setup_diagnostics.manual_setup_report.16'),
                  _tr('setup_diagnostics.manual_setup_report.17')]
    lines += ['', _tr('setup_diagnostics.manual_setup_report.18')]
    actions = []
    for index, issue in enumerate(issues, 1):
        lines.append('')
        lines.append(_tr('setup_diagnostics.manual_setup_report.19', p0=f'{index}', p1=f"{issue['message']}", p2=f"{issue['code']}"))
        for field in ('path', 'file', 'address', 'log', 'service_log'):
            if issue.get('details', {}).get(field):
                lines.append('   ' + {'path': _tr('setup_diagnostics.manual_setup_report.20'), 'file': _tr('setup_diagnostics.manual_setup_report.21'), 'address': _tr('setup_diagnostics.manual_setup_report.22'),
                                     'log': _tr('setup_diagnostics.manual_setup_report.23'), 'service_log': _tr('setup_diagnostics.manual_setup_report.24')}[field] + ': ' + str(issue['details'][field]))
        for step in _steps(issue['code'], issue.get('details', {}), options):
            if step not in actions:
                lines.append('   — ' + step)
                actions.append(step)
    if options:
        lines += ['', _tr('setup_diagnostics.manual_setup_report.25'),
                  '', _tr('setup_diagnostics.manual_setup_report.26'), '', _command(retry),
                  '', _tr('setup_diagnostics.manual_setup_report.27'),
                  _tr('setup_diagnostics.manual_setup_report.28'), '', *_agent_commands(retry, root)]
        if options.proxy != 'external':
            lines += ['', _tr('setup_diagnostics.manual_setup_report.29'),
                      _tr('setup_diagnostics.manual_setup_report.30') + options.backend_bind + ':' +
                      str(options.backend_port or 18764) + _tr('setup_diagnostics.manual_setup_report.31'), '',
                      _tr('setup_diagnostics.manual_setup_report.26'), '',
                      _command(_retry(root, options, prepared=True) + ([] if options.backend_port else ['--backend-port', '18764'])),
                      '', _tr('setup_diagnostics.manual_setup_report.32'), '',
                      *_agent_commands(_retry(root, options, prepared=True) + ([] if options.backend_port else ['--backend-port', '18764']), root)]
        lines += ['', _tr('setup_diagnostics.manual_setup_report.33'),
                  _tr('setup_diagnostics.manual_setup_report.34'),
                  _tr('setup_diagnostics.manual_setup_report.35'),
                  _tr('setup_diagnostics.manual_setup_report.36'),
                  _tr('setup_diagnostics.manual_setup_report.37')]
    if payload.get('stage') == 'recovery' or payload['code'] in {'setup_incomplete', 'setup_recovery_invalid', 'setup_failed'}:
        command = [str(Path(root) / 'venv/bin/python'), '-m', 'web_tools.setup_cli', '--project-root', str(root), 'recover']
        lines += ['', _tr('setup_diagnostics.manual_setup_report.38'),
                  _tr('setup_diagnostics.manual_setup_report.26'), 'cd ' + shlex.quote(str(root)) + ' && ' + shlex.join(command),
                  '', _tr('setup_diagnostics.manual_setup_report.39'), '',
                  *_agent_commands(command, root),
                  '', _tr('setup_diagnostics.manual_setup_report.40')]
    lines += ['', COPY_END]
    return '\n'.join(lines)


def terminal_report(message, *, stream=None):
    """Keep machine output plain; decorate the interactive terminal only."""
    stream = sys.stdout if stream is None else stream
    color = stream.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ
    warning, accent, reset = ('\033[1;33m', '\033[1;36m', '\033[0m') if color else ('', '', '')
    width = max(40, min(100, shutil.get_terminal_size((96, 24)).columns))
    rule = '=' * width
    first, second = HEADING.split('. ', 1)
    lines = ['', '', warning + rule, first + '.', second, rule + reset, '']
    for line in message.splitlines()[1:]:
        if line in {COPY_START, COPY_END}:
            lines.extend([accent + line + reset, ''])
        elif line == ADMIN_URL:
            lines.append(accent + line + reset)
        elif line.startswith(('bash ', 'cd ', 'systemd-run ', 'journalctl ', '  --', "  '", '  ')) and not line.startswith('   '):
            lines.append(line)  # Shell continuations must retain their exact bytes.
        elif line.startswith('   — '):
            lines.append(textwrap.fill(line, width=width, subsequent_indent='     ', break_long_words=False, break_on_hyphens=False))
        else:
            lines.append(textwrap.fill(line, width=width, break_long_words=False, break_on_hyphens=False) if line else '')
    return '\n'.join([*lines, ''])
