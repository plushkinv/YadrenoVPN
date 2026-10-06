"""Context replacements for the existing file writer, validated as one batch."""
from __future__ import annotations

from pathlib import Path

from web_tools.errors import WebSourceError
from web_tools.paths import atomic_write


def invalid_edits(message: str) -> WebSourceError:
    """Explain a rejected batch without repeating file contents or writing files."""
    return WebSourceError(
        'file_edits_invalid', message + ' No files were changed.',
        next_action='Correct the indicated argument and retry the batch. Put path inside each edits item; '
                    'omit top-level path/content. Example shape (illustrative values): '
                    '{"edits":[{"path":"file.txt","old_text":"before","new_text":"after"}]}',
    )


def edit_files(edits, resolve):
    """Check every exact fragment before writing; roll back writes on I/O failure."""
    if not isinstance(edits, list):
        raise invalid_edits('arguments.edits must be an array.')
    if not edits:
        raise invalid_edits('arguments.edits must contain at least 1 item; received 0.')
    original, updated = {}, {}
    for index, edit in enumerate(edits):
        address = f'arguments.edits[{index}]'
        if not isinstance(edit, dict):
            raise invalid_edits(f'{address} must be an object.')
        fields = ('path', 'old_text', 'new_text')
        missing = [f'{address}.{key}' for key in fields if key not in edit]
        if missing:
            raise invalid_edits('Missing required field(s): ' + ', '.join(missing) + '.')
        for key in edit:
            if key not in fields:
                raise invalid_edits(f'{address}.{key} is not supported; allowed fields: path, old_text, new_text.')
        for key in fields:
            if not isinstance(edit[key], str):
                raise invalid_edits(f'{address}.{key} must be a string.')
        if not edit['old_text']:
            raise invalid_edits(f'{address}.old_text must contain at least 1 character; received 0.')
        path = Path(resolve(edit['path']))
        if path not in original:
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise WebSourceError('file_missing', f'Edit {index + 1} requires an existing regular file.', file=edit['path'],
                                     next_action='Read the file first. Use path plus content to create a new file.')
            original[path] = path.read_bytes()
            try:
                updated[path] = original[path].decode('utf-8')
            except UnicodeDecodeError:
                raise WebSourceError('file_not_text', 'Context replacement requires a UTF-8 text file.', file=edit['path'],
                                     next_action='Copy binary assets with the existing file transport.') from None
        old, new = edit['old_text'], edit['new_text']
        count = updated[path].count(old)
        if (count == 0 and '\n' in old and '\r' not in old and '\r' not in new
                and '\r\n' in updated[path]
                and not any(char in updated[path].replace('\r\n', '') for char in '\r\n')):
            # Accept LF snippets in a consistently CRLF file without changing its
            # line endings. Literal matches and explicit CRLF edits take priority.
            old, new = old.replace('\n', '\r\n'), new.replace('\n', '\r\n')
            count = updated[path].count(old)
        if count != 1:
            raise WebSourceError('file_edit_conflict',
                                 f'{address}.old_text occurs {count} times; no files were changed.',
                                 file=edit['path'], next_action='Read this file and include enough exact surrounding text for one match.')
        updated[path] = updated[path].replace(old, new, 1)
    # Detect an independent writer between validation and the first write.
    for path, content in original.items():
        if path.read_bytes() != content:
            raise WebSourceError('file_edit_conflict', 'A file changed during validation; nothing was written.', file=str(path),
                                 next_action='Read the changed file and retry the corrected batch.')
    written = []
    try:
        for path, content in updated.items():
            encoded = content.encode('utf-8')
            if encoded != original[path]:
                atomic_write(path, encoded, mode=path.stat().st_mode & 0o777)
                written.append(path)
    except OSError:
        for path in reversed(updated):
            if path.read_bytes() != original[path]:
                atomic_write(path, original[path], mode=path.stat().st_mode & 0o777)
        raise
    return {'status': 'ok', 'changed': bool(written), 'changed_files': [str(path) for path in written],
            'edits_applied': len(edits), 'publication_changed': False}
