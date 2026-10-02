"""Addressed draft operations under an authenticated existing Hub request."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bot.services.yadreno_admin_customization_tools import _json_result
from bot.services.yadreno_admin_web_binding import WebEditorBinding
from core.results import CoreError
from web_tools.compiler_sandbox import IsolatedCompileError
from web_tools.editor_workspace import StaleRevision

WEB_INSPECT_SCOPES = frozenset({'web.context', 'web.workspace', 'web.source', 'web.candidate', 'web.publication'})
WEB_APPLY_FIELDS = {
    'web.file.write': {'operation', 'file', 'content', 'expected_revision'},
    'web.asset.write': {'operation', 'file', 'source_path', 'expected_revision'},
    'web.file.delete': {'operation', 'file', 'expected_revision'},
    'web.build': {'operation', 'expected_revision'},
    'web.publish': {'operation', 'expected_revision', 'build_id'},
    'web.rollback': {'operation', 'expected_revision', 'build_id'},
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
        raise ValueError('invalid inspection pagination')
    return value


def _source(workspace, args):
    kind, key = args.get('kind'), args.get('key')
    if not isinstance(key, str) or not key or kind not in {'custom', *_KINDS}:
        raise ValueError('web.source requires kind and exact key from the inventory')
    record = workspace.read_custom(key) if kind == 'custom' else workspace.read_effective(_KINDS[kind], key)
    text = record.pop('content').decode('utf-8')
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
        raise ValueError('unsupported Web inspection arguments')
    cursor = _integer(args.get('cursor', 0), 0, 2**31 - 1)
    limit = _integer(args.get('limit', 20), 1, 50)
    if scope == 'web.context':
        targets = workspace.inspect_targets()
        return _paginated({'status': 'ok', **binding.runtime_context(api_key),
                'inspect_scopes': sorted(WEB_INSPECT_SCOPES), 'apply_operations': list(WEB_APPLY_FIELDS),
                'asset_sources': ['source_path'],
                'publication_available': True, 'revision': targets['revision']}, targets['targets'], cursor, limit)
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
                       'source_revision': snapshot['source_revision']}, files, cursor, limit)


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
    operation = args.get('operation')
    fields = WEB_APPLY_FIELDS.get(operation)
    if fields is None or set(args) != fields:
        raise ValueError('unsupported Web mutation arguments')
    expected = args['expected_revision']
    if operation in {'web.publish', 'web.rollback'}:
        from bot.services.yadreno_admin_web_dialog import authorize_administrator
        from web_tools.editor_publication import apply, undo
        context = binding.runtime_context(api_key)['web_editor']

        def current_authority():
            identity = binding.authority(api_key)
            if authorize_administrator(identity['telegram_id']) != api_key:
                raise CoreError('access_denied')

        if operation == 'web.rollback':
            return undo(workspace, expected_revision=expected, build_id=args['build_id'], authorize=current_authority)
        return apply(workspace, expected_revision=expected, build_id=args['build_id'],
                     base_build_id=context['publication']['build_id'],
                     rollback=operation == 'web.rollback', authorize=current_authority)
    if operation == 'web.build':
        return workspace.build_candidate(expected_revision=expected)
    if operation == 'web.file.write':
        return workspace.write_custom(args['file'], args['content'], expected_revision=expected)
    if operation == 'web.file.delete':
        return workspace.delete_custom(args['file'], expected_revision=expected)
    if 'source_path' in args:
        from bot.services.temporary_files import UPLOAD_MAX_BYTES, UPLOAD_RELATIVE
        from web_tools.editor_files import read_regular
        source = args['source_path']
        if not isinstance(source, str) or not source or not Path(source).is_absolute():
            raise ValueError('asset source requires an absolute uploaded-file path')
        root = binding.project_root / UPLOAD_RELATIVE
        name = Path(source).relative_to(root).as_posix()
        content = read_regular(root, name, maximum=UPLOAD_MAX_BYTES)
        return workspace.write_asset(args['file'], content, expected_revision=expected)


def execute_web_customization_tool(tool: str, args: dict, binding: WebEditorBinding, api_key: str) -> str:
    """Use trusted binding only; no roots, actor IDs or publish permission in args."""
    try:
        from web_tools.editor_publication import recover
        recover(binding.project_root)
        workspace = binding.workspace(api_key)
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
        return _json_result({'status': 'error', **error.as_dict()})
    except StaleRevision as error:
        message = 'Inspect the current source revision before retrying.'
        if str(error) == 'UI publication changed since task capture':
            message = ('The publication changed or this task already published. Inspect web.publication; '
                       'further changes need a new request bound to the current publication. '
                       'Repeating this publish call cannot rebase the task.')
        return _json_result({'status': 'error', 'code': 'conflict', 'error': message})
    except IsolatedCompileError as error:
        message = (str(error) + '\nDiagnostics refer to the task draft. Read the affected file with '
                   'web.source (kind="custom", key=the path relative to custom_web), then fix that draft. '
                   'The installed custom_web file is the published version and may differ.')
        return _json_result({'status': 'error', 'code': 'web_build_failed', 'error': message})
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        # Compiler and path exceptions may contain private absolute paths.
        return _json_result({'status': 'error', 'code': 'web_operation_failed',
                            'error': 'The draft operation failed. Inspect its revision, file format and compiler readiness.'})
