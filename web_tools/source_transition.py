"""One-time conversion of the unreleased overlay format to ordinary project files."""
from __future__ import annotations

import json
import posixpath
import re
import shutil
import uuid

from web_tools.editor_files import read_regular, scan_sources
from web_tools.paths import atomic_write, canonical, local_path
from web_tools.source_tree import archive_bytes, fingerprint, replace_tree, template, read_archive


def relative_import(name, folder='src/runtime'):
    path = posixpath.relpath(name, folder)
    return path if path.startswith('.') else './' + path


def materialized(root, overlay):
    """Freeze former replacements into the installation's own ordinary imports."""
    files = template(root)
    files.update(overlay)
    manifest = json.loads(overlay.get('manifest.json', b'{}'))
    metadata = {key: manifest.get(key, default) for key, default in {
        'format_version': 2, 'version': '1.0.0', 'api': {'min': 1, 'max': 1},
        'frontend_api': {'min': 1, 'max': 1}, 'environment_contract': 1, 'modules': [],
    }.items()}
    metadata['format_version'] = 2
    files['manifest.json'] = canonical(metadata)
    imports, components, pages = [], [], []
    for group, target in (('components', components), ('pages', pages)):
        for index, item in enumerate(manifest.get(group, [])):
            symbol = group + str(index)
            path = relative_import(item['file'])
            imports.append(f'import {symbol} from {json.dumps(path)};')
            if group == 'components':
                components.append(json.dumps(item['id']) + ': ' + symbol)
            else:
                fields = {key: value for key, value in item.items() if key != 'file'}
                pages.append('{...' + json.dumps(fields) + ', component: ' + symbol + '}')
    for name in manifest.get('styles', []):
        imports.append('import ' + json.dumps(relative_import(name)) + ';')
    site = "import type { ComponentType } from 'react';\n" + '\n'.join(imports)
    site += '\nexport const site = {components: {' + ','.join(components) + '}, pages: [' + ','.join(pages) + '] as {id: string; title?: string; module_id?: string; component: ComponentType}[]'
    if 'navigation' in manifest:
        site += ', navigation: ' + json.dumps(manifest['navigation'], ensure_ascii=False)
    site += '};\n'
    files['src/runtime/site.tsx'] = site.encode()
    files['src/runtime/registry.ts'] = (
        "import { basePages } from './pages';\n"
        "import { site } from './site';\n"
        "export const customization = {...__UI_BUILD__, ...site};\n"
        "export const registeredPages = basePages;\n"
    ).encode()
    # These ordinary source components belong to this converted customer tree.
    # The platform and future stock templates have no replacement resolver.
    files['src/runtime/overrides.tsx'] = (
        "import type { ComponentType, ReactNode } from 'react';\n"
        "import { site } from './site';\n"
        "export function replaceable<P extends object>(id: string, Base: ComponentType<P>) {\n"
        " return function View(props: P) {\n"
        " const map = site.components as unknown as Record<string, ComponentType<any>>;\n"
        " const tag = (props as Record<string, unknown>)['data-ui'];\n"
        " const View = typeof tag === 'string' && map[tag] || map[id];\n"
        " return View ? <View {...props} Base={Base}/> : <Base {...props}/>;\n };\n}\n"
        "export function UiOverrideProvider({children}: {components: unknown; children: ReactNode}) {return <>{children}</>;}\n"
    ).encode()
    registry = json.loads(files['src/runtime/view-registry.json'])
    registry['styles'] = list(dict.fromkeys([*registry['styles'], *manifest.get('styles', [])]))
    for group in ('pages', 'components'):
        for item in manifest.get(group, []):
            existing = next((entry for entry in registry[group] if entry['id'] == item['id']), None)
            entry = {**(existing or {}), **item, 'export': 'default'}
            if existing is not None:
                registry[group][registry[group].index(existing)] = entry
            else:
                registry[group].append(entry)
    files['src/runtime/view-registry.json'] = canonical(registry)
    if 'src/runtime/pages.ts' not in overlay:
        page_files = sorted({page['file'] for page in registry['pages']})
        bindings = [f'import * as PageFile{i} from {json.dumps(relative_import(name))};'
                    for i, name in enumerate(page_files)]
        modules = '{' + ','.join(json.dumps(name.replace('src/', '../')) + f': PageFile{i}'
                                 for i, name in enumerate(page_files)) + '}'
        text = files['src/runtime/pages.ts'].decode()
        text = '\n'.join(bindings) + '\n' + text.replace(
            "const modules = import.meta.glob<Record<string, ComponentType>>('../pages/**/*.tsx', { eager: true });",
            'const modules = ' + modules + ' as unknown as Record<string, Record<string, ComponentType>>;')
        files['src/runtime/pages.ts'] = text.encode()
    for filename, exports in {'src/components/Ui.tsx': {
        'Button': 'button', 'PageHeading': 'page.heading', 'Badge': 'badge',
        'RowButton': 'row.button', 'Dialog': 'dialog', 'CheckList': 'check.list'},
        'src/components/Shell.tsx': {'Shell': 'app.shell'}}.items():
        if filename in overlay:
            continue
        text = files[filename].decode()
        text = "import { replaceable } from '../runtime/overrides';\n" + text
        for name, identity in exports.items():
            text = text.replace(f'export const {name} = Base{name};',
                                f"export const {name} = replaceable('{identity}', Base{name});")
        files[filename] = text.encode()
    if 'navigation' in manifest and 'src/App.tsx' not in overlay:
        files['src/App.tsx'] = files['src/App.tsx'].replace(b'title={settings.title}', b'navigation={customization.navigation} title={settings.title}')
    for name in manifest.get('assets', []):
        files['public/assets/' + name] = overlay[name]
    # Normalize the one obsolete virtual import in formerly mirrored sources.
    for name, content in list(files.items()):
        if name.endswith(('.ts', '.tsx')) and b'virtual:yadreno-customization' in content:
            relative = relative_import('src/runtime/registry', posixpath.dirname(name))
            files[name] = re.sub(rb"import customization from ['\"]virtual:yadreno-customization['\"]",
                                 ('import { customization } from ' + json.dumps(relative)).encode(), content)
    files['src/customization.d.ts'] = template(root)['src/customization.d.ts']
    return files


def materialize(root, *, overlay=None):
    """Called under installation lock; keep evidence and all task source archives."""
    folder = local_path(root, 'custom_web')
    before = scan_sources(folder)
    runtime = local_path(root, 'web_runtime')
    marker = runtime / 'source-transition.json'
    if not marker.exists():
        backup_id = 'web-' + uuid.uuid4().hex
        atomic_write(local_path(root, 'backup/' + backup_id + '.zip'), archive_bytes(before))
        after = materialized(root, before if overlay is None else overlay)
        atomic_write(runtime / 'source-transition-after.zip', archive_bytes(after))
        atomic_write(marker, canonical({'backup_id': backup_id}))
    else:
        record = json.loads(read_regular(runtime, marker.name))
        if not re.fullmatch(r'web-[a-f0-9]{32}', record['backup_id']):
            raise ValueError('invalid source transition metadata')
        before = read_archive(read_regular(root / 'backup', record['backup_id'] + '.zip'))
        after = read_archive(read_regular(runtime, 'source-transition-after.zip'))
        actual = scan_sources(folder)
        if any(actual.get(name) not in (before.get(name), after.get(name))
               for name in set(actual) | set(before) | set(after)):
            raise ValueError('working files changed during source transition')
    published_sources = [before, *([overlay] if overlay is not None else [])]
    tasks = runtime / 'editor_tasks'
    obsolete_trees = []
    if tasks.exists():
        for task in tasks.iterdir():
            old = task / 'editor/custom_web'
            if re.fullmatch(r'[a-f0-9]{32}', task.name) and old.is_dir() and not old.is_symlink():
                draft = scan_sources(old)
                published_sources.append(draft)
                baseline = task / 'editor/baseline'
                if baseline.exists():
                    published_sources.append(scan_sources(baseline))
                content = materialized(root, draft)
                archive = local_path(root, 'backup/web-' + task.name + '.zip')
                state_path = task / 'editor/state.json'
                old_state = json.loads(state_path.read_bytes()) if state_path.exists() else {}
                if old_state.get('format_version') == 2 and archive.exists():
                    content = read_archive(read_regular(archive.parent, archive.name))
                else:
                    atomic_write(archive, archive_bytes(content))
                from web_tools.build import source_version
                atomic_write(task / 'editor/state.json', canonical({
                    'format_version': 2,
                    'source_revision': {'base': source_version(root, include_commit=False)[0], 'custom': fingerprint(after)},
                    'backup': {'created': True, 'id': 'web-' + task.name, 'path': str(archive), 'source_hash': fingerprint(content)},
                }))
                for name in ('candidate.json', 'publication.json'):
                    (task / 'editor' / name).unlink(missing_ok=True)
                for name in ('custom_web', 'baseline'):
                    obsolete = local_path(task, 'editor/' + name)
                    if obsolete.exists():
                        obsolete_trees.append(obsolete)
    replace_tree(folder, after)
    # Baseline is deliberately not invented for a formerly modified overlay.
    from web_tools.publication import read_pointer
    selected = read_pointer(runtime)['current']
    if selected:
        publication = local_path(runtime, 'publications/' + selected)
        proof_path = publication / 'source.json'
        if proof_path.exists():
            proof = json.loads(read_regular(publication, 'source.json'))
            for candidate in published_sources:
                if proof.get('custom_fingerprint') == fingerprint(candidate):
                    atomic_write(publication / 'sources.zip', archive_bytes(materialized(root, candidate)))
                    break
            if proof.get('custom_fingerprint') is None and proof.get('customization_version') == 'base':
                from web_tools.build import source_version
                if proof.get('base_build_id') == source_version(root)[0]:
                    atomic_write(publication / 'sources.zip', archive_bytes(template(root)))
    legacy_journal = runtime / 'editor-activation.json'
    if legacy_journal.exists():
        journal = json.loads(read_regular(runtime, legacy_journal.name))
        if journal.get('format_version') == 1:
            atomic_write(legacy_journal, canonical({**journal, 'completed_source_hash': fingerprint(after)}))
    # All recovery material and the current publication snapshot are durable
    # before any obsolete task tree is removed.
    for obsolete in obsolete_trees:
        shutil.rmtree(obsolete)
    marker.unlink()
    (runtime / 'source-transition-after.zip').unlink(missing_ok=True)


def recover_legacy(root, journal):
    """Finish the single old write-ahead operation before removing its editor trees."""
    from web_tools.publication import read_pointer
    runtime = root / 'web_runtime'
    if (journal.get('format_version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', journal.get('task_id', ''))
            or journal.get('operation') not in {'publish', 'rollback'}):
        raise ValueError('invalid legacy publication recovery metadata')
    pointer = read_pointer(runtime)
    if pointer not in (journal['before'], journal['after']):
        raise ValueError('another publication followed the legacy interrupted operation')
    if (runtime / 'source-transition.json').exists():
        materialize(root)
        (runtime / 'editor-activation.json').unlink()
        return
    if journal.get('completed_source_hash'):
        if fingerprint(scan_sources(root / 'custom_web')) != journal['completed_source_hash']:
            raise ValueError('sources changed after legacy recovery')
        (runtime / 'editor-activation.json').unlink()
        return
    task = local_path(runtime, 'editor_tasks/' + journal['task_id'] + '/editor')
    baseline = scan_sources(task / 'baseline') if (task / 'baseline').exists() else {}
    draft = scan_sources(task / 'custom_web')
    before, after = (baseline, draft) if journal['operation'] == 'publish' else (draft, baseline)
    folder = local_path(root, 'custom_web', directory=True)
    current = scan_sources(folder)
    if any(current.get(name) not in (before.get(name), after.get(name))
           for name in set(current) | set(before) | set(after)):
        raise ValueError('sources changed outside the legacy interrupted operation')
    materialize(root, overlay=after if pointer == journal['after'] else before)
    (runtime / 'editor-activation.json').unlink()
