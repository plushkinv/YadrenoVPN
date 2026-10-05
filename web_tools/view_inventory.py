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
    """Read the one registry in the selected complete source tree; never fall back."""
    try:
        return _build_inventory(root, custom, declaration)
    except (ValueError, KeyError, TypeError, OSError) as error:
        if custom is None:
            raise
        from web_tools.errors import WebSourceError
        raise WebSourceError('web_registry_invalid', str(error), file=REGISTRY,
                             next_action='Correct the working page/component registry and its referenced files, then run web.build.') from error


def _build_inventory(root, custom, declaration):
    folder = Path(custom) if custom is not None else Path(root) / 'web'
    registry = local_path(folder, REGISTRY)
    if not registry.exists():
        raise ValueError('src/runtime/view-registry.json is missing from the working project')
    if not registry.is_file() or registry.stat().st_nlink != 1:
        raise ValueError('UI registry must be a regular local file')
    data = json.loads(registry.read_bytes())
    kind = 'custom' if custom is not None else 'stock'

    def source(item):
        name = item['file'] if isinstance(item, dict) else item
        file = local_path(folder, name)
        if not file.is_file() or file.stat().st_nlink != 1:
            raise ValueError('Registered working source is missing: ' + name)
        return {'kind': kind, 'file': name, **({'export': item['export']} if isinstance(item, dict) else {})}

    result = {'format_version': 1,
              'pages': [{'id': item['id'], 'source': source(item),
                         **{key: item[key] for key in ('title', 'preview_parameter', 'module_id') if key in item}}
                        for item in data['pages']],
              'components': [{'id': item['id'], 'source': source(item)} for item in data['components']],
              'styles': [source(item) for item in data['styles']], 'scenarios': list(data['scenarios'])}
    modules = (declaration or {}).get('requirements', {}).get('modules')
    if modules is None:
        modules = json.loads((folder / 'manifest.json').read_bytes()).get('modules', [])
    return validate_inventory(result, modules={item['id'] for item in modules})


def write_source_provenance(stage, signed, *, root, custom=None, declaration=None, fingerprint=None):
    """Keep local build ownership outside public assets and signed package format."""
    from web_tools.package import digest
    from web_tools.paths import atomic_write, canonical, source_provenance
    from web_tools.build import custom_source_fingerprint, source_version

    inventory = build_inventory(root, custom, declaration)
    if custom is None:
        from web_tools.source_tree import fingerprint as source_fingerprint, template
        fingerprint = source_fingerprint(template(root))
    value = {'format_version': 1, 'manifest_hash': digest(canonical(signed)), 'custom_fingerprint': fingerprint}
    if inventory is not None:
        if (source_version(root)[0] != signed['manifest']['base_build_id']
                or custom is not None and fingerprint is not None and custom_source_fingerprint(custom) != fingerprint):
            raise ValueError('UI sources changed during source inventory capture')
        value.update(format_version=2, base_build_id=signed['manifest']['base_build_id'],
                     customization_version=signed['manifest']['customization_version'], inventory=inventory)
    elif signed['manifest']['customization_version'] == 'base':
        return
    atomic_write(local_path(stage, 'source.json'), canonical(value))
    if source_provenance(stage, signed) is None:
        raise ValueError('invalid built UI source provenance')
