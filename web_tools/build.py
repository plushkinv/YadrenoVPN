"""Local staged builds: custom sources are never copied over core source files."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import quote

from web_tools.package import create_package, verify_package, signing_identity, digest
from web_tools.paths import CUSTOM_SOURCE_IGNORED, atomic_write, canonical, local_path
from web_tools.publication import publication_lock
from web_tools.view_inventory import write_source_provenance


def admin_directory(path):
    path = Path(path)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError('build paths must not contain symlinks')
    if path.exists() and os.name != 'nt' and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022):
        raise ValueError('build directory must be owned by the invoking administrator and not writable by others')
    return path.resolve()


def source_version(root, *, include_commit=True):
    root = Path(root)
    source = hashlib.sha256()
    web = local_path(root, 'web')
    root = web.parent
    sources = local_path(root, 'web/src')
    names = []
    # Validate directories before walking them and every input before reading
    # any content. rglob/is_file alone can follow an escaped source symlink.
    if sources.exists():
        if not sources.is_dir():
            raise ValueError('frontend source root must be a directory')
        for directory, subdirs, files in os.walk(sources, followlinks=False):
            for name in subdirs:
                local_path(root, (Path(directory) / name).relative_to(root).as_posix(), hidden=True)
            names.extend(Path(directory) / name for name in files)
    names.extend(web / item for item in ('package.json', 'package-lock.json', 'vite.app.config.ts', 'customization.mjs', 'index.html', 'preview.html'))
    names.extend(web.glob('tsconfig*.json'))
    names.extend(root / 'web_tools' / item for item in ('service_worker.js', 'compatibility.json', 'toolchain.json'))
    inputs = []
    for file in sorted(names):
        checked = local_path(root, file.relative_to(root).as_posix(), hidden=True)
        if not checked.exists():
            continue
        info = checked.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('frontend sources must be local regular files without hardlinks')
        inputs.append(checked)
    for file in inputs:
        content = file.read_bytes()
        if file.suffix in {'.ts', '.tsx', '.js', '.mjs', '.css', '.json', '.html', '.svg'}:
            content = content.replace(b'\r\n', b'\n')
        source.update(file.relative_to(root).as_posix().encode()); source.update(content)
    if not include_commit:
        return source.hexdigest(), 'unversioned-local-source'
    git = shutil.which('git')
    commit = ''
    if git:
        top = subprocess.run([git, '-C', str(root), 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
        if top and Path(top).resolve() == root.resolve():
            commit = subprocess.run([git, '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            status = subprocess.run([git, '-C', str(root), 'status', '--porcelain', '--untracked-files=all', '--',
                                     *[file.relative_to(root).as_posix() for file in inputs]],
                                    capture_output=True, text=True).stdout.strip()
            if commit and status:
                commit += '-worktree.' + source.hexdigest()[:16]
    return source.hexdigest(), commit or 'unversioned-local-source'


def custom_inventory_fingerprint(inventory):
    """Hash the canonical source inventory shared by bounded workspace scans."""
    return digest(canonical(inventory))


def custom_source_fingerprint(custom, *, copy_to=None):
    """Fingerprint all local custom inputs, optionally making a private copy."""
    custom = admin_directory(custom)
    if not custom.exists():
        return None
    if not custom.is_dir():
        raise ValueError('custom UI source must be a directory')
    inventory = {}
    for path in sorted(custom.rglob('*')):
        if any(part in CUSTOM_SOURCE_IGNORED for part in path.relative_to(custom).parts):
            continue
        name = path.relative_to(custom).as_posix()
        if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.resolve().is_relative_to(custom):
            raise ValueError('custom UI sources must not contain symbolic links')
        checked = path
        info = checked.stat()
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise ValueError('custom UI sources must be administrator-owned and not writable by others')
        if checked.is_dir():
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('custom UI sources must be regular files without hardlinks')
        content = checked.read_bytes()
        inventory[name] = {'sha256': digest(content), 'size': len(content)}
        if copy_to is not None:
            target = admin_directory(copy_to) / path.relative_to(custom)
            if any(parent.is_symlink() for parent in (target, *target.parents)):
                raise ValueError('custom UI copy must not contain symbolic links')
            atomic_write(target, content)
    return custom_inventory_fingerprint(inventory)


def require_toolchain(root):
    """Only source compilation needs Node; installation of prepared assets does not."""
    if local_path(root, 'web_tools/toolchain.json').is_file():
        from web_tools.toolchain import resolve
        return resolve(root)
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node or not npm:
        raise ValueError('Node.js and npm are required for local UI builds')
    web = root / 'web'
    if not (web / 'node_modules' / '.bin' / ('vite.cmd' if os.name == 'nt' else 'vite')).is_file():
        raise ValueError('install pinned web dependencies with npm ci before building')
    return node, npm


def compile_files(root, runtime, custom, stage, *, build_id, instance_id, toolchain=None, run=None):
    """Compile the same frontend for local customs and portable base distribution."""
    run = subprocess.run if run is None else run
    node, npm = toolchain or require_toolchain(root)
    web = root / 'web'
    env = {**os.environ, 'YADRENO_CUSTOM_WEB': str(custom), 'YADRENO_UI_OUT': str(stage / 'files'),
           'YADRENO_VITE_CACHE': str(local_path(runtime, 'cache', directory=True)), 'YADRENO_UI_BUILD': build_id,
           'YADRENO_UI_BASE': '/ui/versions/' + build_id + '/', 'YADRENO_UI_INSTANCE': instance_id,
           'NODE_OPTIONS': '--max-old-space-size=512'}
    env['PATH'] = str(Path(node).parent) + os.pathsep + env.get('PATH', os.defpath)
    declaration = run([node, '--input-type=module', '-e',
        "import {readCustomization} from './customization.mjs'; console.log(JSON.stringify(readCustomization(process.env.YADRENO_CUSTOM_WEB)));"],
        cwd=web, env=env, capture_output=True, text=True, timeout=180)
    if declaration.returncode:
        raise ValueError('Invalid custom UI manifest: ' + declaration.stderr.strip()[-4000:])
    customization = json.loads(declaration.stdout)
    # Resolve paths before invoking the compiler. The type checker sees custom
    # source as well as the published SDK; Vite validates the same manifest again.
    tsconfig = {'extends': str(web / 'tsconfig.json'),
                'compilerOptions': {'rootDirs': [str(custom / 'src'), str(web / 'src')]},
                'include': [str(web / 'src' / '**' / '*'), str(custom / '**' / '*.tsx'), str(custom / '**' / '*.ts')]}
    atomic_write(stage / 'tsconfig.json', canonical(tsconfig))
    with (stage / 'build.log').open('wb') as log:
        try:
            run([npm, 'exec', '--no', '--', 'tsc', '--project', str(stage / 'tsconfig.json')], cwd=web, env=env, check=True, stdout=log, stderr=log, timeout=180)
            run([npm, 'exec', '--no', '--', 'vite', 'build', '--config', 'vite.app.config.ts'], cwd=web, env=env, check=True, stdout=log, stderr=log, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.flush()
            detail = (stage / 'build.log').read_text(encoding='utf-8', errors='replace')[-4000:]
            raise ValueError(f'UI build failed; active publication is unchanged. Log: {stage / "build.log"}\n{detail}') from exc
    return customization


def write_service_worker(root, files, *, build_id, instance_id):
    base = '/ui/versions/' + build_id + '/'
    assets = sorted(base + quote(p.relative_to(files).as_posix(), safe='/')
                    for p in files.rglob('*') if p.is_file() and p != files / 'sw.js')
    worker = (root / 'web_tools/service_worker.js').read_text(encoding='utf-8')
    worker = worker.replace('__CACHE_NAME__', json.dumps('yadreno.shell.' + instance_id + '.' + build_id)).replace('__ASSET_URLS__', json.dumps(assets)).replace('__INDEX_URL__', json.dumps(base + 'index.html'))
    atomic_write(files / 'sw.js', worker.encode())


def build(root, runtime, custom, *, product_version=None, toolchain=None, compiler=None):
    root, runtime, custom = admin_directory(root), admin_directory(runtime), admin_directory(custom)
    toolchain = toolchain or require_toolchain(root)
    source_fingerprint = custom_source_fingerprint(custom)
    build_id = uuid.uuid4().hex
    stage = local_path(runtime, 'staging/' + build_id, directory=True)
    base_hash, detected_version = source_version(root)
    product_version = detected_version if product_version is None else product_version
    with publication_lock(runtime):
        key, identity = signing_identity(runtime)
    compile_source = compile_files if compiler is None else compiler
    customization = compile_source(root, runtime, custom, stage, build_id=build_id,
                                   instance_id=identity['instance_id'], toolchain=toolchain)
    write_service_worker(root, stage / 'files', build_id=build_id, instance_id=identity['instance_id'])
    if source_version(root)[0] != base_hash or custom_source_fingerprint(custom) != source_fingerprint:
        raise ValueError('UI sources changed during compilation; rebuild before publication')
    with publication_lock(runtime):
        signed, content = create_package(stage / 'files', key=key, identity=identity, product_version=product_version,
            base_build_id=base_hash, build_id=build_id, customization_version=customization['version'],
            requirements=customization['requirements'], core_api=customization['api'])
        verify_package(content, identity)
        atomic_write(stage / 'manifest.json', canonical(signed))
        atomic_write(stage / 'package.zip', content)
        write_source_provenance(stage, signed, root=root, custom=custom, declaration=customization,
                                fingerprint=source_fingerprint)
    return {'build_id': build_id, 'stage': str(stage), 'content_hash': signed['manifest']['content_hash'],
            'customization_version': customization['version'], 'activated': False,
            'ownership': {'pages': [item['id'] for item in customization['pages']],
                          'components': [item['id'] for item in customization['components']],
                          'navigation': 'custom' if 'navigation' in customization else 'base'},
            'page_update_policy': 'Custom page replacements are preserved; base page changes are not merged into them.'}
