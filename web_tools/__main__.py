"""Local UI commands. Draft build/validation never activate a publication."""
import argparse
import json
import sys
from pathlib import Path

from web_tools.build import build
from web_tools.package import verify_package
from web_tools.paths import PROJECT_ROOT, local_path
from web_tools.publication import activate, rollback


def main():
    parser = argparse.ArgumentParser(description='Локальные build/validate/preview и подписанные пакеты UI')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--custom', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build')
    commands.add_parser('build-base', help='Собрать готовую базовую поставку на машине разработчика')
    commands.add_parser('install-base', help='Подготовить подписанный кандидат из готовой поставки без Node')
    restore = commands.add_parser('restore-local', help='Восстановить UI из защищённой локальной копии в чистые каталоги')
    restore.add_argument('backup', type=Path)
    restore.add_argument('--system', action='store_true', help='Также восстановить собственные системные настройки без запуска сервисов')
    commands.add_parser('trust')
    commands.add_parser('rollback')
    for name in ('validate', 'publish', 'preview'):
        child = commands.add_parser(name)
        child.add_argument('build_id')
        if name == 'preview':
            child.add_argument('--port', type=int, default=5176)
    consumer = commands.add_parser('consume')
    consumer.add_argument('package', type=Path)
    consumer.add_argument('--trust', type=Path, required=True)
    consumer.add_argument('--api-version', type=int, default=1)
    consumer.add_argument('--environment-version', type=int, default=1)
    args = parser.parse_args()
    runtime = args.runtime or args.root / 'web_runtime'
    restored_ui = None
    try:
        if args.command == 'build':
            result = build(args.root, runtime, args.custom or args.root / 'custom_web')
        elif args.command in {'build-base', 'install-base'}:
            from web_tools.distribution import build_base, install_base
            result = (build_base if args.command == 'build-base' else install_base)(args.root, runtime)
        elif args.command == 'restore-local':
            from web_tools.paths import restore_local
            if args.system:
                from web_tools.system_backup import restore_system
                restore_system(args.backup, args.root, check_only=True)
            result = restore_local(args.backup, args.root)
            restored_ui = result.copy()
            if args.system:
                from web_tools.system_backup import restore_system
                result['system'] = restore_system(args.backup, args.root)
        elif args.command == 'consume':
            # Explicit trust input is provisioned independently of this package.
            result = activate(runtime, args.package.read_bytes(), json.loads(args.trust.read_bytes()),
                              api_version=args.api_version, environment_version=args.environment_version)
        else:
            trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
            if args.command == 'trust':
                result = trust
            elif args.command == 'rollback':
                result = rollback(runtime, trust)
            else:
                stage = local_path(runtime, 'staging/' + args.build_id)
                content = local_path(stage, 'package.zip').read_bytes()
                signed, _ = verify_package(content, trust)
                if args.command == 'validate':
                    result = {'valid': True, 'manifest': signed['manifest'], 'activated': False}
                elif args.command == 'publish':
                    result = activate(runtime, content, trust)
                else:
                    from web_tools.preview import preview
                    preview(stage, trust, args.port)
                    return 0
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        failure = {'error': type(exc).__name__, 'message': str(exc)}
        if restored_ui is not None:
            failure['ui_restore'] = restored_ui
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
