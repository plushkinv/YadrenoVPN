"""Internal, server-verified viewed targets for the administrator's Web editor."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from core.results import CoreError
from core.web_ui import require_administrator, require_preview_administrator, validate_setting
from web_tools.build import admin_directory, custom_source_fingerprint, source_version
from web_tools.compatibility import current_capabilities
from web_tools.paths import local_path, source_provenance
from web_tools.publication import read_pointer
from web_tools.release import publication

_FIELDS = {'contract_version', 'route', 'scenario', 'preset', 'theme', 'ui_version', 'customization_version'}
_ROUTE = re.compile(r'[a-z][a-z0-9_.-]{0,79}(?:/[a-zA-Z0-9_.:-]{1,128})?')


def _unavailable(reason):
    return CoreError('conflict', details={'reason': reason})


def _validate_viewed_context(value):
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value['contract_version']) is not int or value['contract_version'] != 1
            or any(not isinstance(value[name], str) for name in _FIELDS - {'contract_version'})
            or not _ROUTE.fullmatch(value['route']) or len(value['scenario']) > 80
            or not re.fullmatch(r'[a-f0-9]{32}', value['ui_version']) or len(value['customization_version']) > 128):
        raise CoreError('invalid_request')
    for name in ('preset', 'theme'):
        if validate_setting(name, value[name]) != value[name]:
            raise CoreError('invalid_request')
    return dict(value)


def capture_view_context(session, viewed_context, *, root):
    """Resolve a browser hint against a complete retained publication and sources.

    The caller supplies an already authenticated server session and trusted
    installation root. This helper creates no session, task or publication.
    Missing legacy provenance affects editor readiness only; it never mutates
    the existing viewer, customs, or active publication.
    """
    require_preview_administrator(session)
    viewed = _validate_viewed_context(viewed_context)
    return capture_editor_context(session['telegram_id'], root=root, viewed=viewed)


def capture_editor_context(telegram_id, *, root, viewed=None):
    """Capture installed sources for a trusted administrator, with an optional view hint."""
    require_administrator(telegram_id)
    try:
        root = admin_directory(Path(root))
        runtime = admin_directory(local_path(root, 'web_runtime'))
        custom = local_path(root, 'custom_web')
        pointer = read_pointer(runtime)
        build_id = pointer['current']
        if viewed is not None and viewed['ui_version'] != build_id:
            raise _unavailable('ui_publication_changed')
        signed, _, _ = publication(runtime, build_id, current_capabilities())
        manifest = signed['manifest']
        if viewed is not None and viewed['customization_version'] != manifest['customization_version']:
            raise _unavailable('ui_publication_changed')
        proof = source_provenance(local_path(runtime, 'publications/' + build_id), signed)
        if proof is None or proof['format_version'] != 2:
            raise _unavailable('ui_source_unavailable')
        inventory = proof['inventory']
        page = (next((item for item in inventory['pages'] if item['id'] == viewed['route'].split('/')[0]), None)
                if viewed is not None else None)
        if viewed is not None and (page is None or viewed['scenario'] not in inventory['scenarios']):
            raise CoreError('invalid_request')
        base_revision = source_version(root, include_commit=False)[0]
        custom_revision = custom_source_fingerprint(custom)
        if (base_revision != proof['base_build_id'] or proof['custom_fingerprint'] is not None
                and custom_revision != proof['custom_fingerprint']):
            raise _unavailable('ui_source_changed')
        # A valid local inventory never makes an escaped/symlinked source readable.
        for source in [*[item['source'] for item in ([page] if page else inventory['pages'])],
                       *[item['source'] for item in inventory['components']], *inventory['styles']]:
            source_root = root / 'web' if source['kind'] == 'stock' else custom
            if not local_path(source_root, source['file']).is_file():
                raise _unavailable('ui_source_unavailable')
        if (read_pointer(runtime) != pointer or source_version(root, include_commit=False)[0] != base_revision
                or custom_source_fingerprint(custom) != custom_revision):
            raise _unavailable('ui_source_changed')
        require_administrator(telegram_id)
        # Browser data cannot supply source ownership, hashes, files or modules.
        # Components are registered global replacements, not a claimed DOM trace.
        return copy.deepcopy({'viewed': viewed, 'publication': {
            'build_id': build_id, 'base_build_id': manifest['base_build_id'],
            'customization_version': manifest['customization_version'], 'manifest_hash': proof['manifest_hash'],
        }, 'source_revision': {'base': base_revision, 'custom': custom_revision},
            'effective_source': {'page': page, 'components': inventory['components'], 'styles': inventory['styles']}})
    except CoreError:
        raise
    except (ValueError, OSError, KeyError, TypeError):
        # Paths, raw manifest contents and installation secrets never enter errors.
        raise _unavailable('ui_source_unavailable') from None
