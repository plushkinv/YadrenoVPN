"""Addressed draft operations under an authenticated existing Hub request."""
from __future__ import annotations

import asyncio
import json

from bot.services.yadreno_admin_customization_tools import _json_result
from bot.services.yadreno_admin_web_binding import WebEditorBinding
from core.results import CoreError
from web_tools.errors import WebSourceError

WEB_INSPECT_SCOPES = frozenset({'web.context', 'web.workspace', 'web.source', 'web.candidate', 'web.publication'})
WEB_APPLY_FIELDS = {
    'web.build': {'operation'},
    'web.publish': {'operation', 'build_id'},
    'web.restore': {'operation', 'source', 'publish'},
}

_KINDS = {'web_page': 'pages', 'web_component': 'components', 'web_style': 'styles'}


async def run_web_customization_tool(tool, args, binding, api_key):
    """Drain bounded draft work before poll shutdown releases its guards."""
    worker = asyncio.create_task(asyncio.to_thread(execute_web_customization_tool, tool, args, binding, api_key))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling to_thread's awaiter cannot stop its filesystem/process work.
        # Keep the enclosing task/guard alive until that bounded operation ends.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


def is_web_operation(tool: str, args: dict) -> bool:
    field = 'scope' if tool == 'satellite_customization_inspect' else 'operation'
    return tool in {'satellite_customization_inspect', 'satellite_customization_apply'} and (
        isinstance(args.get(field), str) and args[field].startswith('web.'))


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WebSourceError('web_arguments_invalid', 'Invalid inspection pagination.',
                             next_action='Use the returned cursor/offset and a limit within the exposed schema.')
    return value


def _source(workspace, args):
    kind, key = args.get('kind'), args.get('key')
    if not isinstance(key, str) or not key or kind not in {'custom', *_KINDS}:
        raise WebSourceError('web_arguments_invalid', 'web.source requires kind and exact key from the inventory.',
                             next_action='Read the tool schema or use ordinary file search/read in custom_web.')
    record = workspace.read_custom(key) if kind == 'custom' else workspace.read_effective(_KINDS[kind], key)
    try:
        text = record.pop('content').decode('utf-8')
    except UnicodeDecodeError:
        raise WebSourceError('web_file_not_text', 'This file is not UTF-8 text.', file=record['file'],
                             next_action='Use the existing binary asset/attachment tools for this file.') from None
    offset = _integer(args.get('offset', 0), 0, len(text))
    length = _integer(args.get('length', 8000), 1, 8000)
    # Preserve exact source and continuation; never silently truncate a file.
    chunk = text[offset:offset + length]
    while True:
        end = offset + len(chunk)
        result = {'status': 'ok', **record, 'content': chunk, 'offset': offset,
                  'next_offset': end if end < len(text) else None, 'total_characters': len(text)}
        if len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 12000:
            return result
        if not chunk:
            raise ValueError('source metadata exceeds the response budget')
        chunk = chunk[:len(chunk) // 2]


def _inspect(binding, workspace, api_key, args):
    scope = args.get('scope')
    common = {'scope', 'cursor', 'limit'}
    allowed = common | {'kind', 'key', 'offset', 'length'} if scope == 'web.source' else common
    if scope not in WEB_INSPECT_SCOPES or set(args) - allowed:
        raise WebSourceError('web_arguments_invalid', 'Unsupported Web inspection arguments.',
                             next_action='Use only fields listed for this scope in the exposed schema.')
    cursor = _integer(args.get('cursor', 0), 0, 2**31 - 1)
    limit = _integer(args.get('limit', 20), 1, 50)
    if scope == 'web.context':
        targets = workspace.inspect_targets()
        return _paginated({'status': 'ok', **binding.runtime_context(api_key),
                'inspect_scopes': sorted(WEB_INSPECT_SCOPES), 'apply_operations': list(WEB_APPLY_FIELDS),
                'publication_available': True}, targets['targets'], cursor, limit)
    if scope == 'web.publication':
        from web_tools.editor_publication import inspect
        return {'status': 'ok', **inspect(workspace)}
    if scope == 'web.source':
        return _source(workspace, args)
    snapshot = workspace.inspect()
    if scope == 'web.candidate':
        return {'status': 'ok', **workspace.candidate(expected_revision=snapshot['revision'])}
    files = [{'file': name, **facts} for name, facts in snapshot['files'].items()]
    return _paginated({'status': 'ok', 'revision': snapshot['revision'],
                       'source_revision': snapshot['source_revision'], 'backup': snapshot['backup'],
                       'working_directory': snapshot['working_directory']}, files, cursor, limit)


def _paginated(metadata, items, cursor, limit):
    end = min(cursor + limit, len(items))
    while True:
        result = {**metadata, 'items': items[cursor:end], 'total': len(items), 'cursor': cursor,
                  'next_cursor': end if end < len(items) else None}
        if len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 12000:
            return result
        end -= 1
        if end <= cursor:
            raise ValueError('file metadata exceeds the response budget')


def _apply(binding, workspace, api_key, args):
    from web_tools.errors import WebSourceError
    operation = args.get('operation')
    fields = WEB_APPLY_FIELDS.get(operation)
    if fields is None or set(args) != fields:
        raise WebSourceError('web_arguments_invalid', 'Unsupported Web operation arguments.',
                             next_action='Use web.build, web.publish(build_id), or web.restore(source, publish).')
    if operation == 'web.build':
        return {**workspace.build_candidate(), 'preview_available': True}
    from bot.services.yadreno_admin_web_dialog import authorize_administrator
    from web_tools.editor_publication import apply, restore
    context = binding.runtime_context(api_key)['web_editor']

    def current_authority():
        identity = binding.authority(api_key)
        if authorize_administrator(identity['telegram_id']) != api_key:
            raise CoreError('access_denied')

    if operation == 'web.restore':
        if type(args['publish']) is not bool:
            raise WebSourceError('web_arguments_invalid', 'publish must be a boolean.',
                                 next_action='Use publish=false for draft restoration or true for an authorized live rollback.')
        return restore(workspace, source=args['source'], publish=args['publish'],
                       base_build_id=context['publication']['build_id'], authorize=current_authority)
    return apply(workspace, build_id=args['build_id'],
                 base_build_id=context['publication']['build_id'], authorize=current_authority)


def execute_web_customization_tool(tool: str, args: dict, binding: WebEditorBinding, api_key: str) -> str:
    """Use trusted binding only; no roots, actor IDs or publish permission in args."""
    from web_tools.publication import read_pointer
    from web_tools.source_tree import fingerprint
    from web_tools.editor_files import scan_sources
    before_pointer = before_sources = None
    try:
        from web_tools.editor_publication import recover
        recover(binding.project_root)
        workspace = binding.workspace(api_key)
        before_pointer = read_pointer(binding.project_root / 'web_runtime')
        before_sources = fingerprint(scan_sources(workspace._custom))
        if tool == 'satellite_customization_inspect':
            result = _inspect(binding, workspace, api_key, args)
            if args.get('scope') == 'web.source':
                return json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        elif tool == 'satellite_customization_apply':
            result = {'status': 'ok', **_apply(binding, workspace, api_key, args)}
        else:
            raise ValueError('unsupported Web tool')
        return _json_result(result)
    except CoreError as error:
        return _json_result({'status': 'error', **error.as_dict(), 'changed': False, 'publication_changed': False,
                            'next_action': 'Reopen the Mini App as an administrator or inspect the current publication if this is a conflict.'})
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError) as error:
        from web_tools.errors import failure
        result = failure(error, operation=str(args.get('operation') or args.get('scope')), root=binding.project_root)
        try:
            recover(binding.project_root)
            result['publication_changed'] = (read_pointer(binding.project_root / 'web_runtime') != before_pointer
                                             if before_pointer is not None else None)
            result['changed'] = (fingerprint(scan_sources(binding.project_root / 'custom_web')) != before_sources
                                 if before_sources is not None else None)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            result.update(changed=None, publication_changed=None)
        return _json_result(result)
