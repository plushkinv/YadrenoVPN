"""Managed-release preflight and reversible UI activation, independent of the DB."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from web_tools.compatibility import current_capabilities, release_capabilities, verification_versions
from web_tools.package import verify_package
from web_tools.paths import PROJECT_ROOT, atomic_write, canonical, local_path
from web_tools.publication import activate, manifest_path, publication_lock, read_pointer
from web_tools.permissions import PathPermissionsError, check_release_paths, require_permissions


def target_capabilities(root, commit):
    if not re.fullmatch(r'[a-f0-9]{40,64}', commit):
        raise ValueError('invalid target commit')
    result = subprocess.run(['git', 'show', commit + ':web_tools/compatibility.json'],
                            cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        return None
    return release_capabilities(json.loads(result.stdout))


def publication(runtime, build_id, capabilities):
    selected = json.loads(manifest_path(runtime, build_id).read_bytes())
    trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
    content = local_path(runtime, 'publications/' + build_id + '/package.zip')
    if not content.exists():
        content = local_path(runtime, 'packages/' + selected['manifest']['content_hash'] + '.zip')
    data = content.read_bytes()
    signed, files = verify_package(data, trust, **verification_versions(capabilities))
    if signed != selected or signed['manifest']['build_id'] != build_id:
        raise ValueError('selected publication does not match its signed archive')
    folder = local_path(runtime, 'publications/' + build_id + '/files')
    if any(local_path(folder, name).read_bytes() != value for name, value in files.items()):
        raise ValueError('selected publication assets are corrupted')
    return signed, data, trust


def preflight(root, capabilities, *, modules=()):
    root = Path(root)
    runtime = local_path(root, 'web_runtime')
    pointer = read_pointer(runtime)
    modules = list(modules)
    if capabilities is None:
        if pointer['current'] or modules:
            raise ValueError('target has no Web Core compatibility declaration; active UI/modules cannot be verified')
        return {'format_version': 1, 'pointer': pointer, 'selected': None, 'capabilities': None}
    capabilities = release_capabilities(capabilities)
    check_release_paths(root)
    for module in modules:
        if module.get('api_version') != capabilities['module_api']:
            raise ValueError('incompatible saved shared module: ' + str(module.get('module_id', 'unknown')))
    errors = []
    for build_id in (pointer['current'],):
        if not build_id:
            continue
        try:
            signed, _, _ = publication(runtime, build_id, capabilities)
            return {'format_version': 1, 'pointer': pointer, 'selected': build_id,
                    'capabilities': capabilities, 'customization_version': signed['manifest']['customization_version'],
                    'diagnostics': errors}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({'build_id': build_id, 'error': str(exc)})
    if pointer['current']:
        raise ValueError('no compatible retained UI publication: ' + json.dumps(errors))
    return {'format_version': 1, 'pointer': pointer, 'selected': None, 'capabilities': capabilities}


def upgrade(root, plan_path):
    with publication_lock(Path(root) / 'web_runtime/source-lock'):
        return _upgrade_locked(root, plan_path)


def _upgrade_locked(root, plan_path):
    root, plan_path = Path(root), Path(plan_path)
    runtime = local_path(root, 'web_runtime')
    plan = json.loads(plan_path.read_bytes())
    if plan['capabilities'] != current_capabilities():
        raise ValueError('target compatibility changed after preflight')
    if plan['pointer'] != read_pointer(runtime):
        raise ValueError('UI publication changed after release preflight; repeat the update')
    from web_tools.platform_assets import install_platform
    install_platform(root, runtime)
    from web_tools.source_tree import ensure, unchanged, update_template, archive_bytes, inventory, template
    from web_tools.editor_files import scan_sources
    folder = ensure(root)
    before_sources = scan_sources(folder)
    if not unchanged(root, before_sources):
        # This also protects unpublished edits over a stock live publication.
        if plan.get('target_commit') and plan['selected']:
            from web_tools.release_build import record_status
            record_status(root, plan['target_commit'], plan['selected'], 'custom_preserved')
        return {'changed': False, 'build_id': plan['selected'], 'custom_preserved': True,
                'custom_rebuild_required': False, 'ui_release_code': 'custom_preserved'}
    from web_tools.distribution import install_base
    result = install_base(root, runtime)
    stage = local_path(runtime, 'staging/' + result['build_id'])
    content = local_path(stage, 'package.zip').read_bytes()
    trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
    change = {'before': plan['pointer'], 'after': {'current': result['build_id'], 'previous': None},
              'source_backup': 'web-ui-before-sources.zip', 'template_backup': 'web-ui-before-template.json',
              'sources_after': inventory(template(root))}
    atomic_write(plan_path.with_name(change['source_backup']), archive_bytes(before_sources))
    atomic_write(plan_path.with_name(change['template_backup']), (runtime / 'source-template.json').read_bytes())
    if plan['pointer']['current']:
        _, before_package, _ = publication(runtime, plan['pointer']['current'], current_capabilities())
        atomic_write(plan_path.with_name('web-ui-before-package.zip'), before_package)
        proof = runtime / 'publications' / plan['pointer']['current'] / 'source.json'
        if proof.exists():
            atomic_write(plan_path.with_name('web-ui-before-source.json'), proof.read_bytes())
    if plan.get('target_commit'):
        from web_tools.release_build import capture_status
        change['release_status_before'] = capture_status(root)
    change_path = plan_path.with_name('web-ui-applied.json')
    atomic_write(change_path, canonical(change))
    try:
        update_template(root)
        activate(runtime, content, trust, expected_pointer=plan['pointer'], **verification_versions(current_capabilities()))
    except BaseException:
        _restore_pointer_locked(root, change_path)
        raise
    if plan.get('target_commit'):
        from web_tools.release_build import record_status
        record_status(root, plan['target_commit'], result['build_id'], 'updated')
    return {'changed': True, 'build_id': result['build_id'], 'custom_rebuild_required': False, 'ui_release_code': 'updated'}


def ensure_base(root=PROJECT_ROOT):
    """Seed the working project and update only a verifiably unchanged template."""
    from web_tools.source_tree import ensure, unchanged
    root = Path(root)
    runtime = local_path(root, 'web_runtime', directory=True)
    from web_tools.platform_assets import install_platform
    install_platform(root, runtime)
    ensure(root)
    before = read_pointer(runtime)
    if before['current']:
        plan = preflight(root, current_capabilities())
        signed, _, _ = publication(runtime, plan['selected'], current_capabilities())
        if not unchanged(root):
            return {'changed': False, 'build_id': before['current'], 'custom_preserved': True}
        from web_tools.build import source_version
        if signed['manifest']['base_build_id'] == source_version(root)[0]:
            return {'changed': False, 'build_id': before['current']}
        # Use the same reversible release operation for an old installer's boot.
        plan_path = local_path(runtime, 'bootstrap-update/plan.json')
        atomic_write(plan_path, canonical(plan))
        return upgrade(root, plan_path)
    from web_tools.distribution import install_base
    result = install_base(root, runtime)
    content = local_path(runtime, 'staging/' + result['build_id'] + '/package.zip').read_bytes()
    trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
    return activate(runtime, content, trust, expected_pointer=before)


def restore_pointer(root, change_path):
    """Restore this update's UI and template from its existing protected snapshot."""
    with publication_lock(Path(root) / 'web_runtime/source-lock'):
        return _restore_pointer_locked(root, change_path)


def _restore_pointer_locked(root, change_path):
    from web_tools.source_tree import read_archive, replace_tree
    from web_tools.publication import _activate_verified
    root, change_path = Path(root), Path(change_path)
    runtime = local_path(root, 'web_runtime')
    if not change_path.exists():
        return {'changed': False}
    change = json.loads(change_path.read_bytes())
    with publication_lock(runtime):
        current = read_pointer(runtime)
        if current not in (change['before'], change['after']):
            raise ValueError('another UI publication followed this update; refusing to overwrite it')
        if 'source_backup' in change:
            if change['source_backup'] != 'web-ui-before-sources.zip' or change.get('template_backup') != 'web-ui-before-template.json':
                raise ValueError('invalid update source recovery files')
            source_bytes = change_path.with_name('web-ui-before-sources.zip').read_bytes()
            from web_tools.source_tree import inventory
            from web_tools.editor_files import scan_sources
            sources_before = read_archive(source_bytes)
            original = inventory(sources_before)
            actual = inventory(scan_sources(root / 'custom_web'))
            after = change.get('sources_after', original)
            if any(actual.get(name) not in (original.get(name), after.get(name))
                   for name in set(actual) | set(original) | set(after)):
                raise ValueError('working files changed after this update; refusing to discard those changes')
            replace_tree(root / 'custom_web', sources_before)
            atomic_write(runtime / 'source-template.json', change_path.with_name('web-ui-before-template.json').read_bytes())
            prior = change['before']['current']
            if prior and current != change['before']:
                content = change_path.with_name('web-ui-before-package.zip').read_bytes()
                trust = json.loads((runtime / 'identity.json').read_bytes())
                signed, files = verify_package(content, trust)
                if signed['manifest']['build_id'] != prior:
                    raise ValueError('update recovery package does not match its saved publication')
                stage = local_path(runtime, 'staging/' + prior, directory=True)
                saved_proof = change_path.with_name('web-ui-before-source.json')
                if saved_proof.exists():
                    atomic_write(stage / 'source.json', saved_proof.read_bytes())
                    atomic_write(stage / 'sources.zip', source_bytes)
                _activate_verified(runtime, content, trust, signed, files)
                atomic_write(local_path(runtime, 'publications/' + prior + '/sources.zip'), source_bytes)
        atomic_write(runtime / 'active.json', canonical(change['before']))
        if 'release_status_before' in change:
            from web_tools.release_build import restore_status
            restore_status(root, change['release_status_before'])
        return {'changed': current != change['before']}


def prepare_core_rollback(root, snapshot, target, change_path):
    """Select a UI that the restored core can serve, without restoring UI sources."""
    root, snapshot, change_path = Path(root), Path(snapshot), Path(change_path)
    runtime = local_path(root, 'web_runtime')
    before = read_pointer(runtime)
    capabilities = target_capabilities(root, target)
    from web_tools.paths import read_local_backup
    try:
        protected, _ = read_local_backup(snapshot)
    except FileNotFoundError:
        protected = {}
    saved = snapshot / 'web-ui-update.json'
    preferred = json.loads(saved.read_bytes())['pointer']['current'] if saved.exists() else None
    if preferred is None:
        preferred = json.loads(protected.get('web_runtime/active.json', b'{}')).get('current')
    if capabilities is None and preferred is None:
        # A pre-Web-Core target has no web listener; its ignored UI data stays.
        return {'changed': False, 'web_supported': False}
    selected = None
    if preferred:
        try:
            selected = publication(runtime, preferred, capabilities or current_capabilities())
        except (OSError, ValueError, KeyError, TypeError):
            selected = None
        saved_package = snapshot / 'web-ui-before-package.zip'
        snapshot_package = protected.get('web_runtime/publications/' + preferred + '/package.zip')
        if snapshot_package is None and saved_package.is_file():
            snapshot_package = saved_package.read_bytes()
        if selected is None and snapshot_package is not None:
            trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
            content = snapshot_package
            signed, _ = verify_package(content, trust, **verification_versions(capabilities or current_capabilities()))
            if signed['manifest']['build_id'] == preferred:
                selected = signed, content, trust
    if selected is None and capabilities is not None:
        plan = preflight(root, capabilities)
        if plan['selected']:
            selected = publication(runtime, plan['selected'], capabilities)
    if selected is None:
        if capabilities is None:
            return {'changed': False, 'web_supported': False}
        if before['current']:
            raise ValueError('no compatible UI for core rollback')
        return {'changed': False}
    signed, content, trust = selected
    next_id = signed['manifest']['build_id']
    # A core-update snapshot is independent of the expiring public-asset cache.
    # Restore its source proof as well, so the restored core can open the editor.
    stage = local_path(runtime, 'staging/' + next_id, directory=True)
    prefix = 'web_runtime/publications/' + next_id + '/'
    for name, fallback in (('source.json', 'web-ui-before-source.json'), ('sources.zip', 'web-ui-before-sources.zip')):
        data = protected.get(prefix + name)
        if data is None and next_id == preferred and (snapshot / fallback).is_file():
            data = (snapshot / fallback).read_bytes()
        if data is not None:
            atomic_write(stage / name, data)
    after = before if next_id == before['current'] else {'current': next_id, 'previous': None}
    atomic_write(change_path, canonical({'before': before, 'after': after}))
    activate(runtime, content, trust, expected_pointer=before,
             **verification_versions(capabilities or current_capabilities()))
    return {'changed': before != after, 'build_id': next_id}


def main():
    parser = argparse.ArgumentParser(description='Managed UI release checks')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('ensure-base')
    paths = actions.add_parser('check-paths')
    path_mode = paths.add_mutually_exclusive_group(required=True)
    path_mode.add_argument('--target')
    path_mode.add_argument('--git-owner', action='store_true')
    check = actions.add_parser('preflight')
    check.add_argument('--target', required=True)
    check.add_argument('--save', type=Path, required=True)
    apply = actions.add_parser('upgrade')
    apply.add_argument('--plan', type=Path, required=True)
    restore = actions.add_parser('restore-pointer')
    restore.add_argument('--change', type=Path, required=True)
    core_rollback = actions.add_parser('rollback-core')
    core_rollback.add_argument('--snapshot', type=Path, required=True)
    core_rollback.add_argument('--target', required=True)
    core_rollback.add_argument('--change', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'ensure-base':
        result = ensure_base(args.root)
    elif args.action == 'check-paths':
        if args.git_owner:
            require_permissions(args.root)
        elif target_capabilities(args.root, args.target) is not None:
            check_release_paths(args.root)
        result = {'ok': True}
    elif args.action == 'preflight':
        modules = []
        if (args.root / 'database/vpn_bot.db').is_file():
            from database import connection, requests as db
            connection.DB_PATH = args.root / 'database/vpn_bot.db'
            modules = db.get_core_module_manifests().values()
        result = preflight(args.root, target_capabilities(args.root, args.target), modules=modules)
        from web_tools.release_build import prepare_sources
        result = prepare_sources(args.root, args.target, args.save.parent, result)
        atomic_write(args.save, canonical(result))
    elif args.action == 'upgrade':
        result = upgrade(args.root, args.plan)
    elif args.action == 'restore-pointer':
        result = restore_pointer(args.root, args.change)
    else:
        result = prepare_core_rollback(args.root, args.snapshot, args.target, args.change)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except PathPermissionsError as error:
        # The installed worker renders the structured stdout; stderr remains a full journal trace.
        import traceback
        print(json.dumps({'format_version': 1, 'code': 'ui_path_permissions', 'issues': error.issues}))
        traceback.print_exc()
        raise SystemExit(1)
