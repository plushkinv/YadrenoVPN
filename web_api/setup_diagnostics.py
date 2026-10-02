"""Copyable operator instructions for bounded setup failures, without secrets."""
from __future__ import annotations

import ipaddress
from pathlib import Path
import shlex

HEADING = 'Автоматическая установка невозможна. Требуется дополнительная ручная настройка сервера'
ADMIN_URL = 'https://t.me/YadrenoAdmin_Bot'


def _steps(code, details, options):
    if code == 'public_port_conflict':
        port = details.get('port', options.https_port if options else 443)
        service = str(details.get('service', ''))
        steps = [f'Проверьте владельца TCP-порта {port} командой ss -ltnp.']
        if 'docker' in service:
            steps.append('Исправьте публикацию порта в настройках соответствующего контейнера или его Compose-проекта.')
        elif 'xray' in service or 'x-ui' in service:
            steps.append('В панели найдите соответствующий inbound, порт панели или подписки. '
                         'Освободите порт для Nginx: перенесите этот listener либо отключите ненужный inbound. '
                         'Перед переносом работающего VPN учтите обновление подключений клиентов; не останавливайте весь Xray.')
        else:
            steps.append('Измените порт или привязку обнаруженной службы через её штатные настройки, сохранив её работу.')
        steps.append('Если служба должна сохранить этот порт, подготовьте совместную маршрутизацию вручную '
                     'и используйте готовый HTTPS-прокси. Повторный запуск мастера сам порт не освободит.')
        return steps
    if code in {'renewal_conflict', 'renewal_changed', 'renewal_http_unavailable'}:
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
                'к закрытому HTTP listener бота вручную и выберите режим готового прокси.']
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
    if code in {'certificate_unusable', 'renewal_not_ready'}:
        return ['Проверьте выдачу сертификата для выбранного домена, HTTP-проверку на порту 80 и срок сертификата.',
                'Проверьте созданное задание продления, его журнал и перезагрузку Nginx после продления. '
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


def _retry(root, options):
    args = ['bash', str(Path(root) / 'install.sh'), 'web-setup', '--proxy', options.proxy]
    if options.proxy == 'managed-nginx':
        args += ['--domain', options.domain, '--agree-tos']
        if options.email:
            args += ['--email', options.email]
    else:
        args += ['--public-url', options.public_url]
    if options.https_port != 443:
        args += ['--https-port', str(options.https_port)]
    if options.listen_address:
        args += ['--listen-address', options.listen_address]
    if options.backend_port:
        args += ['--backend-port', str(options.backend_port)]
    args += ['--backend-bind', options.backend_bind]
    for proxy in options.trusted_proxies:
        args += ['--trusted-proxy', proxy]
    return args


def manual_setup_report(payload, options, root):
    """The same complete report is delivered as text and in the JSON message."""
    details = payload.get('details', {})
    issues = details.get('issues') or [{'code': payload['code'], 'message': payload.get('message', ''), 'details': details}]
    lines = [HEADING, '', 'Вы можете выполнить настройку самостоятельно или полностью делегировать её Yadreno Admin:',
             ADMIN_URL, '', 'Инструкция ниже предназначена для администратора или для передачи агенту целиком.',
             'SSH-доступ к нужному серверу передайте агенту отдельно.', '', 'Задача: подключить сайт и Mini App, сохранив работу панели, VPN и подписок.',
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
                      'Режим: ' + ('Nginx на сервере бота' if options.proxy == 'managed-nginx' else 'готовый HTTPS-прокси')])
    lines += ['', 'Обнаруженные причины и необходимые действия:']
    actions = []
    for index, issue in enumerate(issues, 1):
        lines.append(f'{index}. {issue["message"]} [код: {issue["code"]}]')
        for field in ('path', 'file', 'address'):
            if issue.get('details', {}).get(field):
                lines.append('   ' + {'path': 'Путь', 'file': 'Файл', 'address': 'Адрес'}[field] + ': ' + str(issue['details'][field]))
        for step in _steps(issue['code'], issue.get('details', {}), options):
            if step not in actions:
                lines.append('   — ' + step)
                actions.append(step)
    if options:
        retry = _retry(root, options)
        lines += ['', 'После исправления повторите проверку без изменений:', shlex.join([*retry, '--check-only']),
                  'Если проверка успешна, выполните установку:', shlex.join(retry),
                  'Если подготовлен другой HTTPS-прокси, выберите второй режим мастера и укажите его готовый публичный URL.',
                  'Проверьте открытие сайта и Mini App, работу панели/VPN/подписок и продление их сертификатов.']
    if payload.get('stage') == 'recovery' or payload['code'] in {'setup_incomplete', 'setup_recovery_invalid', 'setup_failed'}:
        command = [str(Path(root) / 'venv/bin/python'), '-m', 'web_api.management', '--project-root', str(root), 'recover']
        lines += ['', 'Команда восстановления незавершённой настройки:',
                  'cd ' + shlex.quote(str(root)) + ' && ' + shlex.join(command)]
    return '\n'.join(lines)
