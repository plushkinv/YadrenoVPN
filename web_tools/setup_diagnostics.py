"""Copyable operator instructions for bounded setup failures, without secrets."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import sys
import textwrap
import uuid

from web_api.settings import DEFAULT_TRUSTED_PROXIES

HEADING = 'Автоматическая установка невозможна. Требуется дополнительная ручная настройка сервера'
ADMIN_URL = 'https://t.me/YadrenoAdmin_Bot'
COPY_START = '----- НАЧАЛО ИНСТРУКЦИИ ДЛЯ КОПИРОВАНИЯ -----'
COPY_END = '----- КОНЕЦ ИНСТРУКЦИИ -----'


def _steps(code, details, options):
    if code == 'public_port_conflict':
        port = details.get('port', options.https_port if options else 443)
        service = str(details.get('service', ''))
        steps = [f'Проверьте владельца TCP-порта {port} командой ss -ltnp.']
        if 'docker' in service:
            steps.append('Исправьте публикацию порта в настройках соответствующего контейнера или его Compose-проекта.')
        elif 'xray' in service or 'x-ui' in service:
            steps.append('В панели найдите соответствующий inbound, порт панели или подписки. '
                         'Для Reality или общего порта 443 сначала подготовьте полный план маршрутизации и возврата. '
                         'Перенос listener и включение нового маршрута выполняйте согласованно, чтобы VPN оставался доступен. '
                         'Не переносите работающий inbound отдельным предварительным шагом и не останавливайте весь Xray.')
        else:
            steps.append('Измените порт или привязку обнаруженной службы через её штатные настройки, сохранив её работу.')
        steps.append('Если служба должна сохранить этот порт, подготовьте совместную маршрутизацию вручную '
                     'и завершите подключение служебной командой для готового HTTPS-прокси ниже. '
                     'Повторный запуск мастера сам порт не освободит.')
        return steps
    if code == 'renewal_conflict':
        return ['Определите, какой клиент и какое задание продлевают указанный сертификат: acme.sh, Certbot, cron или systemd.',
                'Настройте HTTP-проверку через работающий Nginx (webroot) либо сохраните другой совместимый способ. '
                'Проверка должна быть доступна по HTTP на порту 80 для всех имён/IP этого сертификата.',
                'Сохраните сам сертификат на IP/домен, ключ, пути установки, CA, профиль и частоту продления, '
                'а также команды обновления сертификата в панели. Не удаляйте сертификат и не отключайте его продление.',
                'Проверьте продление штатными средствами найденного клиента и что панель/подписки используют обновлённый сертификат. '
                'Не заменяйте рабочий сертификат тестовым. Простого освобождения 443 для этого недостаточно.']
    if code in {'nginx_invalid', 'nginx_include_missing', 'nginx_complex_configuration', 'server_name_conflict'}:
        return ['Проверьте существующую конфигурацию командами nginx -t и nginx -T, включая сайты и TCP/UDP-маршрутизацию.',
                'Подготовьте отдельный сайт для указанного домена в корне /. '
                'Для автоматического режима нужен include /etc/nginx/conf.d/*.conf внутри http и отсутствие '
                'чужого сайта с тем же именем; существующие сайты, VPN и подписки должны продолжить работу.',
                'Если требуется перестройка stream, контейнерного или другого прокси, настройте HTTPS-маршрут '
                'к закрытому HTTP listener бота вручную и используйте служебную команду для готового прокси ниже.']
    if code in {'dns_unavailable', 'dns_address_mismatch', 'ipv6_unavailable'}:
        return ['Проверьте A и AAAA указанного домена. В автоматическом режиме они должны вести на этот сервер.',
                'Исправьте неверные записи. Если IPv6 не настроен, удалите лишнюю AAAA; затем дождитесь обновления DNS.',
                'Проверьте доступность TCP 80 и выбранного HTTPS-порта извне, включая firewall сервера и хостинга.']
    if code in {'backend_port_conflict', 'invalid_saved_web_settings', 'backend_address_missing', 'listen_address_missing'}:
        return ['Сопоставьте выбранные адреса и порты с ip -j address show и ss -ltnp.',
                'Сохраните публичные порты панели/VPN. Для бота выберите свободный закрытый адрес/порт '
                'через --backend-bind и --backend-port; согласуйте его с прокси. Не открывайте внутренний HTTP listener в Интернет.']
    if code in {'owned_file_collision', 'certificate_directory_collision', 'unsafe_owned_path'}:
        return ['Определите владельца конфликтующего файла или каталога. Не удаляйте и не перезаписывайте чужую конфигурацию.',
                'Устраните пересечение имён/путей с сохранением работающих служб либо подготовьте отдельный HTTPS-прокси.']
    if code in {'https_unavailable', 'unexpected_redirect', 'installation_mismatch', 'nginx_configuration_not_applied'}:
        return ['Проверьте DNS, доступность HTTPS, соответствие сертификата домену и полный сертификатный chain.',
                'Убедитесь, что корень / и /api/ ведут к этой установке бота, а не к панели, другому сайту или старому upstream.',
                'Проверьте применение конфигурации Nginx и работу yadreno-vpn. Готовый прокси должен передавать запросы '
                'без подмены страницы, дополнительной авторизации и неожиданных перенаправлений.']
    if code in {'certificate_unusable', 'renewal_not_ready', 'renewal_scheduler_unknown'}:
        return ['Проверьте выдачу сертификата для выбранного домена, HTTP-проверку на порту 80 и срок сертификата.',
                'Проверьте штатный certbot.timer либо расписание Snap Certbot, журнал клиента и reload Nginx после продления. '
                'Собственные таймеры и обходные штампы готовности не создавайте. '
                'Существующие сертификаты панели и подписок должны сохранить своё продление.']
    if code in {'setup_incomplete', 'setup_recovery_invalid'}:
        return ['Восстановите прерванную настройку штатной командой recover из этой установки. '
                'Не удаляйте журнал восстановления и файлы сертификатов вручную.',
                'Если восстановление сообщает о конфликте, сначала сопоставьте текущие файлы с сохранённой конфигурацией '
                'и устраните указанную причину. Затем повторите recover.']
    if code == 'ui_not_ready':
        return ['Восстановите или обновите эту установку штатным установщиком до версии с готовым веб-интерфейсом.',
                'Проверьте запуск бота и готовность интерфейса, затем повторите подключение домена.']
    if code in {'setup_busy', 'update_operation_busy'}:
        return ['Дождитесь завершения уже запущенной установки, обновления или восстановления и повторите команду. '
                'Не удаляйте lock-файлы и не запускайте несколько установок одновременно.']
    if code in {'command_missing', 'command_failed', 'listeners_unknown'}:
        return ['Проверьте наличие и исправность указанной системной команды, права root и используемую ОС.',
                'При ошибке пакетов проверьте apt; при ошибке Nginx — nginx -t и журнал nginx; '
                'при ошибке сертификата — журнал соответствующего ACME-клиента и доступность HTTP-проверки.',
                'Убедитесь, что после устранения ошибки продолжают работать панель, VPN и подписки.']
    return ['Проверьте указанные код и этап ошибки, журнал службы yadreno-vpn и состояние Nginx/сертификатов.',
            'Устраните конкретную причину, сохранив данные бота, панель, VPN, подписки и существующее продление сертификатов.']


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
    return [_command(command), '', 'Журнал этой попытки (читать отдельной командой после перезапуска бота):',
            _command(['journalctl', '--unit=' + unit, '--no-pager', '--output=cat', '--lines=80'])]


def manual_setup_report(payload, options, root):
    """The same complete report is delivered as text and in the JSON message."""
    details = payload.get('details', {})
    issues = details.get('issues') or [{'code': payload['code'], 'message': payload.get('message', ''), 'details': details}]
    lines = [HEADING, '', 'Вы можете выполнить настройку самостоятельно или полностью делегировать её Yadreno Admin:',
             ADMIN_URL, '', 'Скопируйте весь блок между отметками НАЧАЛО и КОНЕЦ и передайте администратору.',
             'SSH-доступ к нужному серверу передайте отдельно.', '', COPY_START, '',
             'Задача: подключить сайт и Mini App, сохранив работу панели, VPN и подписок.',
             'Каталог установленного бота: ' + str(root)]
    addresses = details.get('server_addresses') or details.get('server') or []
    visible = []
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_loopback:
                visible.append(address)
        except ValueError:
            pass
    if visible:
        lines.append('Адреса сервера по результатам проверки: ' + ', '.join(visible))
    if options:
        lines.extend(['Домен: ' + options.domain, 'Публичный адрес: ' + options.public_url,
                      'Подключение: ' + {'auto': 'автоматическое на этом сервере',
                          'managed-nginx': 'Nginx на сервере бота', 'external': 'готовый HTTPS-прокси'}[options.proxy]])
        retry = _retry(root, options)
        lines += ['', 'Сначала повторите штатную проверку без изменений: отчёт отражает состояние на момент его создания.',
                  '', _command([*retry, '--check-only']),
                  '', 'Работайте только по актуальным причинам из новой проверки. Уже устранённые причины не расследуйте повторно.',
                  'Проверяйте указанный порт, профиль сертификата или конфигурацию; расширяйте диагностику только '
                  'при новой ошибке или обнаруженной зависимости. Если требуется recover, используйте команду ниже.']
    lines += ['', 'Причины на момент составления отчёта и необходимые действия:']
    actions = []
    for index, issue in enumerate(issues, 1):
        lines.append('')
        lines.append(f'{index}. {issue["message"]} [код: {issue["code"]}]')
        for field in ('path', 'file', 'address', 'log', 'service_log'):
            if issue.get('details', {}).get(field):
                lines.append('   ' + {'path': 'Путь', 'file': 'Файл', 'address': 'Адрес',
                                     'log': 'Диагностика', 'service_log': 'Журнал команды'}[field] + ': ' + str(issue['details'][field]))
        for step in _steps(issue['code'], issue.get('details', {}), options):
            if step not in actions:
                lines.append('   — ' + step)
                actions.append(step)
    if options:
        lines += ['', 'После исправления повторите --check-only выше. При preflight_ready выполните установку одним из способов.',
                  '', 'Из SSH:', '', _command(retry),
                  '', 'Из ЯдреноАдмина: установка перезапускает бота, поэтому запускайте её вне его службы через systemd-run.',
                  'Прямой запуск и nohup внутри службы бота прерываются вместе с ней. '
                  'Команда ниже создаёт только временную службу, без таймера.', '', *_agent_commands(retry, root)]
        if options.proxy != 'external':
            lines += ['', 'Для администратора: если HTTPS-прокси настроен вручную, завершите подключение командой ниже.',
                      'До её запуска подготовьте маршрут к закрытому адресу бота ' + options.backend_bind + ':' +
                      str(options.backend_port or 18764) + '. Для удалённого прокси явно задайте --backend-bind, '
                      '--backend-port и --trusted-proxy под подготовленную закрытую сеть.', '',
                      'Из SSH:', '',
                      _command(_retry(root, options, prepared=True) + ([] if options.backend_port else ['--backend-port', '18764'])),
                      '', 'Из ЯдреноАдмина:', '',
                      *_agent_commands(_retry(root, options, prepared=True) + ([] if options.backend_port else ['--backend-port', '18764']), root)]
        lines += ['', 'Критерий успеха: конечный результат мастера ready (в JSON: ok=true, code=ready) '
                  'и доступный HTTPS-сайт именно этой установки. Сам запуск службы не означает завершение установки.',
                  'Если конечного результата ещё нет, проверьте журнал этой попытки; вторую установку одновременно не запускайте.',
                  'После ошибки сначала установите причину и исправьте её. Для новой попытки используйте новое имя временной службы.',
                  'Для затронутых соседних служб проверьте доступные серверные признаки: состояние, listener, '
                  'сертификат и изменённое продление. Успешную проверку продления мастером повторяйте только '
                  'после изменений, затрагивающих продление.',
                  'Администратор при желании отдельно проверяет вход через Telegram и использование VPN/панели/подписок. '
                  'Агент не выполняет эти пользовательские проверки и не ждёт их подтверждения для завершения серверной работы.']
    if payload.get('stage') == 'recovery' or payload['code'] in {'setup_incomplete', 'setup_recovery_invalid', 'setup_failed'}:
        command = [str(Path(root) / 'venv/bin/python'), '-m', 'web_tools.setup_cli', '--project-root', str(root), 'recover']
        lines += ['', 'Команда восстановления незавершённой настройки:',
                  'Из SSH:', 'cd ' + shlex.quote(str(root)) + ' && ' + shlex.join(command),
                  '', 'Из ЯдреноАдмина (восстановление также может перезапустить бота):', '',
                  *_agent_commands(command, root),
                  '', 'Дождитесь конечного recovered или nothing_to_recover с ok=true, затем повторите --check-only.']
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
