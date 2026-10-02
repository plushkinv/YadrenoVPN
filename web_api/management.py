"""Local installer entry point; no public HTTP administration or second runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from web_api.setup_options import SetupError, SetupOptions, result
from web_api.setup_preflight import SettingsStore, preflight
from web_api.setup_system import System, setup_lock
from web_tools.paths import PROJECT_ROOT, local_path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SetupError('invalid_arguments', message, stage='arguments', exit_code=2)


def parser():
    value = Parser(description='Подключить сайт и Mini App: отдельный домен, HTTPS и общий runtime.')
    value.add_argument('--project-root', type=Path, default=PROJECT_ROOT)
    commands = value.add_subparsers(dest='command', required=True, parser_class=Parser)
    setup = commands.add_parser('setup')
    setup.add_argument('--proxy', default='auto', choices=('auto', 'managed-nginx', 'external'),
                       help='По умолчанию подключение определяется автоматически; явный режим — для администратора.')
    setup.add_argument('--domain')
    setup.add_argument('--public-url')
    setup.add_argument('--email', help='Контакт для сертификата; по умолчанию admin@<домен>.')
    setup.add_argument('--agree-tos', action='store_true')
    setup.add_argument('--backend-port', type=int)
    setup.add_argument('--backend-bind', default='127.0.0.1')
    setup.add_argument('--https-port', type=int)
    setup.add_argument('--listen-address')
    setup.add_argument('--trusted-proxy', action='append')
    setup.add_argument('--check-only', action='store_true')
    setup.add_argument('--output', choices=('json', 'text'), default='text')
    recovery = commands.add_parser('recover')
    recovery.add_argument('--output', choices=('json', 'text'), default='text')
    return value


def perform_setup(root, options, *, system=None, store=None):
    from web_api.setup_apply import apply, recover_interrupted_setup
    system, store = system or System(), store or SettingsStore()
    journal = local_path(Path(root) / 'web_runtime', 'secrets/setup-transaction.json')
    if options.check_only:
        if journal.exists():
            raise SetupError('setup_incomplete', 'Сначала восстановите незавершённую настройку командой recover.')
        checked = preflight(root, options, system, store)
        options, signed, trust, _, _, facts = checked
        return result(options, ok=True, code='preflight_ready', stage='preflight', **facts,
                      publication={'instance_id': trust['instance_id'], 'build_id': signed['manifest']['build_id'],
                                   'content_hash': signed['manifest']['content_hash']})
    from bot.services.update_rollback import update_operation_lock
    with update_operation_lock(root), setup_lock(root):
        recover_interrupted_setup(root, system=system, store=store, locked=True)
        checked = preflight(root, options, system, store)
        return apply(root, checked, system, store)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    output_json = '--output=json' in argv or '--output' in argv and argv[argv.index('--output') + 1:][:1] == ['json']
    options = None
    root = PROJECT_ROOT
    try:
        args = parser().parse_args(argv)
        output_json = args.output == 'json'
        root = args.project_root.absolute()
        if root.resolve() != PROJECT_ROOT:
            raise SetupError('installation_mismatch', 'Запустите management из выбранной установленной версии.', stage='arguments', exit_code=2)
        if args.command == 'setup':
            options = SetupOptions.parse(args)
            payload = perform_setup(root, options)
        else:
            from web_api.setup_apply import recover_interrupted_setup
            changed = recover_interrupted_setup(root)
            payload = result(ok=True, code='recovered' if changed else 'nothing_to_recover', stage='recovery', changed=changed)
        status = 0 if payload['ok'] else 4
    except SetupError as exc:
        payload = result(options, code=exc.code, stage=exc.stage, changed=exc.stage not in {'arguments', 'preflight'},
                         message=str(exc), details=exc.details)
        status = exc.exit_code
    except Exception as exc:
        # Exception details may contain local paths or external service output.
        payload = result(options, code='setup_failed', stage='apply', changed=True,
                         message='Настройка не завершена; повтор восстановит сохранённую конфигурацию.',
                         details={'error_type': type(exc).__name__})
        status = 4
    manual = not payload['ok'] and payload['stage'] not in {'arguments', 'complete'}
    if manual:
        from web_api.setup_diagnostics import manual_setup_report
        payload['message'] = manual_setup_report(payload, options, root)
    if output_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if manual:
            from web_api.setup_diagnostics import terminal_report
            print(terminal_report(payload['message']))
        else:
            print(payload.get('message') or ('Сайт и Mini App готовы: ' + str(payload['public_url']) if payload['code'] == 'ready'
                  else 'Результат: ' + payload['code']))
        if payload.get('upstream'):
            print('Закрытый upstream: ' + payload['upstream'])
        if payload.get('existing_local_proxy'):
            print('Найдено готовое локальное подключение домена. Конфигурация Nginx и сертификат сохраняются.')
        if payload.get('tls_renewal') == 'external_owner':
            print('Продление сертификата остаётся в существующей конфигурации HTTPS; будущая выдача здесь не проверяется.')
        if payload['ok'] and payload.get('acme_http01'):
            print('Продление существующего IP-сертификата: HTTP-проверка через Nginx' +
                  (' будет настроена при установке.' if options.check_only else ' настроена.'))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
