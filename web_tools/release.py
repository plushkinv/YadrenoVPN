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
    for module in modules:
        if module.get('api_version') != capabilities['module_api']:
            raise ValueError('incompatible saved shared module: ' + str(module.get('module_id', 'unknown')))
    errors = []
    for build_id in (pointer['current'], pointer['previous']):
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
    root, plan_path = Path(root), Path(plan_path)
    runtime = local_path(root, 'web_runtime')
    plan = json.loads(plan_path.read_bytes())
    if plan['capabilities'] != current_capabilities():
        raise ValueError('target compatibility changed after preflight')
    if plan['pointer'] != read_pointer(runtime):
        raise ValueError('UI publication changed after release preflight; repeat the update')
    selected = plan['selected']
    diagnostic = 'updated'
    if selected and plan.get('customization_version') != 'base':
        from web_tools.release_build import prepared_candidate
        candidate, diagnostic = prepared_candidate(root, plan)
        if candidate is None:
            # Keep a compatible publication if tooling/source proof is unavailable.
            signed, content, trust = publication(runtime, selected, current_capabilities())
        else:
            signed, content, trust = candidate
        next_id = signed['manifest']['build_id']
    else:
        from web_tools.distribution import install_base
        result = install_base(root, runtime)
        content = local_path(runtime, 'staging/' + result['build_id'] + '/package.zip').read_bytes()
        trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
        next_id = result['build_id']
    expected = (plan['pointer'] if next_id == plan['pointer']['current'] else
                {'current': next_id, 'previous': plan['pointer']['current']})
    change = {'before': plan['pointer'], 'after': expected}
    if plan.get('target_commit'):
        from web_tools.release_build import capture_status
        change['release_status_before'] = capture_status(root)
    atomic_write(plan_path.with_name('web-ui-applied.json'), canonical(change))
    activate(runtime, content, trust, expected_pointer=plan['pointer'], **verification_versions(current_capabilities()))
    applied = read_pointer(runtime)
    if plan.get('target_commit'):
        from web_tools.release_build import record_status
        record_status(root, plan['target_commit'], applied['current'], diagnostic)
    return {'changed': applied != plan['pointer'], 'build_id': applied['current'],
            'custom_rebuild_required': diagnostic != 'updated', 'ui_release_code': diagnostic}


def ensure_base(root=PROJECT_ROOT):
    """Bootstrap old installers and refresh stock UI; never overwrite a custom."""
    root = Path(root)
    runtime = local_path(root, 'web_runtime')
    before = read_pointer(runtime)
    capabilities = current_capabilities()
    if before['current']:
        plan = preflight(root, capabilities)
        signed, content, trust = publication(runtime, plan['selected'], capabilities)
        if signed['manifest']['customization_version'] != 'base':
            result = activate(runtime, content, trust, expected_pointer=before)
            return {**result, 'custom_preserved': True}
        from web_tools.build import source_version
        if signed['manifest']['base_build_id'] == source_version(root)[0]:
            return activate(runtime, content, trust, expected_pointer=before)
    from web_tools.distribution import install_base
    result = install_base(root, runtime)
    content = local_path(runtime, 'staging/' + result['build_id'] + '/package.zip').read_bytes()
    trust = json.loads(local_path(runtime, 'identity.json').read_bytes())
    return activate(runtime, content, trust, expected_pointer=before)


def restore_pointer(root, change_path):
    """Only undo this update's pointer; preserve all assets, customs and money."""
    runtime, change_path = local_path(root, 'web_runtime'), Path(change_path)
    if not change_path.exists():
        return {'changed': False}
    change = json.loads(change_path.read_bytes())
    with publication_lock(runtime):
        current = read_pointer(runtime)
        if current == change['before']:
            if 'release_status_before' in change:
                from web_tools.release_build import restore_status
                restore_status(root, change['release_status_before'])
            return {'changed': False}
        if current != change['after']:
            raise ValueError('another UI publication followed this update; refusing to overwrite it')
        # This pointer was captured before mutation. Original archives are retained.
        for build_id in change['before'].values():
            if build_id:
                manifest_path(runtime, build_id)
        atomic_write(local_path(runtime, 'active.json'), canonical(change['before']))
        if 'release_status_before' in change:
            from web_tools.release_build import restore_status
            restore_status(root, change['release_status_before'])
        return {'changed': True}


def prepare_core_rollback(root, snapshot, target, change_path):
    """Select a UI that the restored core can serve, without restoring UI sources."""
    root, snapshot, change_path = Path(root), Path(snapshot), Path(change_path)
    runtime = local_path(root, 'web_runtime')
    before = read_pointer(runtime)
    capabilities = target_capabilities(root, target)
    saved = snapshot / 'web-ui-update.json'
    preferred = json.loads(saved.read_bytes())['pointer']['current'] if saved.exists() else None
    if preferred is None:
        from web_tools.paths import read_local_backup
        try:
            files, _ = read_local_backup(snapshot)
            preferred = json.loads(files.get('web_runtime/active.json', b'{}')).get('current')
        except FileNotFoundError:
            pass  # Older snapshots have no UI material.
    if capabilities is None and preferred is None:
        # A pre-Web-Core target has no web listener; its ignored UI data stays.
        return {'changed': False, 'web_supported': False}
    selected = None
    if preferred:
        try:
            selected = publication(runtime, preferred, capabilities or current_capabilities())
        except (OSError, ValueError, KeyError, TypeError):
            selected = None
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
    after = before if next_id == before['current'] else {'current': next_id, 'previous': before['current']}
    atomic_write(change_path, canonical({'before': before, 'after': after}))
    activate(runtime, content, trust, expected_pointer=before,
             **verification_versions(capabilities or current_capabilities()))
    return {'changed': before != after, 'build_id': next_id}


def main():
    parser = argparse.ArgumentParser(description='Managed UI release checks')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('ensure-base')
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
    elif args.action == 'preflight':
        modules = []
        if (args.root / 'database/vpn_bot.db').is_file():
            from database import connection, requests as db
            connection.DB_PATH = args.root / 'database/vpn_bot.db'
            modules = db.get_core_module_manifests().values()
        result = preflight(args.root, target_capabilities(args.root, args.target), modules=modules)
        from web_tools.release_build import prepare_custom
        result = prepare_custom(args.root, args.target, args.save.parent, result)
        atomic_write(args.save, canonical(result))
    elif args.action == 'upgrade':
        result = upgrade(args.root, args.plan)
    elif args.action == 'restore-pointer':
        result = restore_pointer(args.root, args.change)
    else:
        result = prepare_core_rollback(args.root, args.snapshot, args.target, args.change)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
