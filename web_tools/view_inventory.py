"""Build-time source ownership shared with the frontend's declarative registry."""
from __future__ import annotations

import json
import re
from pathlib import Path

from web_tools.paths import local_path, relative_name

REGISTRY = 'src/runtime/view-registry.json'
_ID = re.compile(r'[a-z][a-z0-9_.-]{0,79}')


def validate_inventory(value, *, modules=()):
    """Validate stored declarations without inventing a second route registry."""
    if (not isinstance(value, dict) or set(value) != {'format_version', 'pages', 'components', 'styles', 'scenarios'}
            or type(value['format_version']) is not int or value['format_version'] != 1):
        raise ValueError('invalid UI source inventory')
    for name in ('pages', 'components', 'styles', 'scenarios'):
        if not isinstance(value[name], list) or len(value[name]) > 2000:
            raise ValueError('invalid UI source inventory list')
    if not value['pages'] or not value['scenarios']:
        raise ValueError('incomplete UI source inventory')

    def source(item, extensions):
        if (not isinstance(item, dict) or not {'kind', 'file'} <= set(item)
                or set(item) - {'kind', 'file', 'export'} or item['kind'] not in {'stock', 'custom'}
                or not isinstance(item['file'], str) or len(item['file']) > 512
                or Path(relative_name(item['file'])).suffix not in extensions
                or item['kind'] == 'stock' and not item['file'].startswith('src/')):
            raise ValueError('invalid UI source reference')
        if 'export' in item and (not isinstance(item['export'], str)
                                or not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]{0,127}', item['export'])):
            raise ValueError('invalid UI source export')

    for name in ('pages', 'components'):
        seen = set()
        for item in value[name]:
            optional = {'module_id', 'preview_parameter', 'title'} if name == 'pages' else set()
            if (not isinstance(item, dict) or not {'id', 'source'} <= set(item)
                    or set(item) - {'id', 'source'} - optional or not isinstance(item['id'], str)
                    or not _ID.fullmatch(item['id']) or item['id'] in seen):
                raise ValueError('invalid/duplicate UI source registration')
            seen.add(item['id'])
            if 'title' in item and (not isinstance(item['title'], str) or len(item['title']) > 1024):
                raise ValueError('invalid UI preview page title')
            source(item['source'], {'.ts', '.tsx'})
            if 'module_id' in item and (not isinstance(item['module_id'], str)
                    or item['module_id'] not in modules or not item['id'].startswith(item['module_id'] + '.')
                    or item['source']['kind'] != 'custom'):
                raise ValueError('invalid UI module source registration')
            if 'preview_parameter' in item and (not isinstance(item['preview_parameter'], str)
                    or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,128}', item['preview_parameter'])):
                raise ValueError('invalid UI preview parameter')
    for item in value['styles']:
        source(item, {'.css'})
    if (any(not isinstance(item, str) or not _ID.fullmatch(item) for item in value['scenarios'])
            or len(set(value['scenarios'])) != len(value['scenarios'])):
        raise ValueError('invalid/duplicate UI preview scenario')
    return value


def build_inventory(root, custom=None, declaration=None):
    """Project canonical build data; legacy source trees have no editor proof."""
    root = Path(root)
    registry = local_path(root / 'web', REGISTRY)
    if not registry.exists():
        return None
    if not registry.is_file() or registry.stat().st_nlink != 1:
        raise ValueError('UI registry must be a local regular file without hardlinks')
    stock = json.loads(registry.read_bytes())
    declaration = declaration or {}
    for name in ('pages', 'components'):
        identifiers = [item['id'] for item in stock[name]]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('duplicate stock UI source registration')

    def stock_source(item):
        source = {'kind': 'stock', 'file': item['file'], 'export': item['export']}
        file = local_path(root / 'web', source['file'])
        if not file.is_file() or file.stat().st_nlink != 1:
            raise ValueError('registered stock UI source is missing')
        if custom is not None:
            override = local_path(custom, source['file'])
            if override.exists():
                if not override.is_file() or override.stat().st_nlink != 1:
                    raise ValueError('mirrored UI source must be a regular file')
                source['kind'] = 'custom'
        return source

    def custom_source(item):
        # readCustomization has already validated ownership, imports and files.
        name = Path(item['file']).relative_to(Path(custom).resolve()).as_posix()
        file = local_path(custom, name)
        if not file.is_file() or file.stat().st_nlink != 1:
            raise ValueError('registered custom UI source is missing')
        return {'kind': 'custom', 'file': name, 'export': 'default'}

    pages = {item['id']: {'id': item['id'], 'source': stock_source(item),
                        **({'preview_parameter': item['preview_parameter']} if 'preview_parameter' in item else {})}
             for item in stock['pages']}
    for item in declaration.get('pages', []):
        pages[item['id']] = {**pages.get(item['id'], {}), 'id': item['id'], 'source': custom_source(item),
                             **({'title': item['title']} if item.get('title') is not None else {}),
                             **({'module_id': item['module_id']} if item.get('module_id') else {})}
    replacements = {item['id']: item for item in declaration.get('components', [])}
    components = []
    for item in stock['components']:
        replacement = replacements.get(item['id']) or replacements.get(item.get('fallback'))
        components.append({'id': item['id'], 'source': custom_source(replacement) if replacement else stock_source(item)})
    styles = [{'kind': ('custom' if custom is not None and local_path(custom, name).exists() else 'stock'),
               'file': name} for name in stock['styles']]
    styles.extend({'kind': 'custom', 'file': Path(name).relative_to(Path(custom).resolve()).as_posix()}
                  for name in declaration.get('styles', []))
    for item in styles:
        file = local_path(root / 'web' if item['kind'] == 'stock' else custom, item['file'])
        if not file.is_file() or file.stat().st_nlink != 1:
            raise ValueError('registered UI stylesheet is missing')
    result = {'format_version': 1, 'pages': list(pages.values()), 'components': components,
              'styles': styles, 'scenarios': list(stock['scenarios'])}
    return validate_inventory(result, modules={item['id'] for item in declaration.get('modules', [])})


def write_source_provenance(stage, signed, *, root, custom=None, declaration=None, fingerprint=None):
    """Keep local build ownership outside public assets and signed package format."""
    from web_tools.package import digest
    from web_tools.paths import atomic_write, canonical, source_provenance
    from web_tools.build import custom_source_fingerprint, source_version

    inventory = build_inventory(root, custom, declaration)
    value = {'format_version': 1, 'manifest_hash': digest(canonical(signed)), 'custom_fingerprint': fingerprint}
    if inventory is not None:
        if (source_version(root)[0] != signed['manifest']['base_build_id']
                or fingerprint is not None and custom_source_fingerprint(custom) != fingerprint):
            raise ValueError('UI sources changed during source inventory capture')
        value.update(format_version=2, base_build_id=signed['manifest']['base_build_id'],
                     customization_version=signed['manifest']['customization_version'], inventory=inventory)
    elif signed['manifest']['customization_version'] == 'base':
        return
    atomic_write(local_path(stage, 'source.json'), canonical(value))
    if source_provenance(stage, signed) is None:
        raise ValueError('invalid built UI source provenance')
