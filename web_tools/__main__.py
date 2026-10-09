"""Local UI commands. Draft build/validation never activate a publication."""
import argparse
import json
import sys
from pathlib import Path

from web_tools.build import build
from web_tools.package import verify_package
from web_tools.paths import PROJECT_ROOT, local_path
from web_tools.publication import activate, publication_lock


def main():
    parser = argparse.ArgumentParser(description='Локальные build/validate/preview и подписанные пакеты UI')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--runtime', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build')
    commands.add_parser('build-base', help='Собрать готовую базовую поставку на машине разработчика')
    commands.add_parser('install-base', help='Подготовить подписанный кандидат из готовой поставки без Node')
    restore = commands.add_parser('restore-local', help='Восстановить UI из защищённой локальной копии в чистые каталоги')
    restore.add_argument('backup', type=Path)
    restore.add_argument('--system', action='store_true', help='Также восстановить собственные системные настройки без запуска сервисов')
    commands.add_parser('trust')
    reset = commands.add_parser('restore', help='Восстановить исходники; --publish также проверит и применит их')
    reset.add_argument('source', help='published или идентификатор web-… из архива запроса')
    reset.add_argument('--publish', action='store_true')
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
            from web_tools.source_tree import ensure
            from web_tools.compiler_sandbox import compile_isolated
            with publication_lock(runtime / 'source-lock'):
                result = build(args.root, runtime, ensure(args.root), compiler=compile_isolated)
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
            elif args.command == 'restore':
                import uuid
                from web_tools.editor_workspace import EditorWorkspace
                from web_tools.editor_publication import restore
                from web_tools.publication import read_pointer
                if runtime.resolve() != (args.root / 'web_runtime').resolve():
                    raise ValueError('source restore uses the installation web_runtime directory')
                task = local_path(runtime, 'editor_tasks/' + uuid.uuid4().hex, directory=True)
                workspace = EditorWorkspace.create(args.root, task)
                result = restore(workspace, source=args.source, publish=args.publish,
                                 base_build_id=read_pointer(runtime)['current'], authorize=lambda: None)
            else:
                stage = local_path(runtime, 'staging/' + args.build_id)
                content = local_path(stage, 'package.zip').read_bytes()
                signed, _ = verify_package(content, trust)
                if args.command == 'validate':
                    result = {'valid': True, 'manifest': signed['manifest'], 'activated': False}
                elif args.command == 'publish':
                    from web_tools.source_tree import fingerprint
                    from web_tools.editor_files import scan_sources
                    from web_tools.paths import source_provenance
                    from web_tools.build import source_version
                    from web_tools.editor_workspace import StaleRevision
                    with publication_lock(runtime / 'source-lock'):
                        proof = source_provenance(stage, signed)
                        if (proof is None or proof['custom_fingerprint'] != fingerprint(scan_sources(args.root / 'custom_web'))
                                or signed['manifest']['base_build_id'] != source_version(args.root, include_commit=False)[0]):
                            raise StaleRevision('Working files or compiler inputs changed after this candidate was checked.')
                        result = activate(runtime, content, trust)
                else:
                    from web_tools.preview import preview
                    from web_tools.platform_assets import install_platform
                    install_platform(args.root, runtime)
                    preview(stage, trust, args.port, runtime=runtime)
                    return 0
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        from web_tools.errors import failure as describe_failure
        failure = describe_failure(exc, operation=args.command, root=args.root)
        if restored_ui is not None:
            failure['ui_restore'] = restored_ui
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
