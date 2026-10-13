"""Private uploaded files, scoped to an account and an installed module."""
import os
from pathlib import Path
import re

from core.accounts import require_account
from core.results import CoreError
from database import requests as db
from web_tools.editor_files import directory_handle, read_regular, remove_regular
from web_tools.paths import relative_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20 * 1024 * 1024
TEMP_DIRECTORY = 'tmp/user_uploads'


def authorize(account, module_id):
    from core.extensions.registry import module_state
    require_account(account)
    if module_state(module_id) != 'available':
        raise CoreError('module_unavailable', retryable=True)


def record(account, module_id, file_id):
    require_account(account)
    if not isinstance(file_id, str) or not re.fullmatch(r'[a-f0-9]{32}', file_id):
        raise CoreError('file_not_found')
    row = db.get_module_upload(file_id, account.account_id, module_id)
    if row is None:
        raise CoreError('file_not_found')
    return row


def describe(row):
    from bot.services.temporary_files import TEMP_RETENTION_DAYS
    return {'file_id': row['id'], 'filename': row['filename'], 'content_type': row['content_type'],
            'size': row['size'], 'persistent': bool(row['persistent']),
            'expires_at': None if row['persistent'] else row['created_at'] + TEMP_RETENTION_DAYS * 86400}


def read_record(row):
    try:
        return read_regular(PROJECT_ROOT, row['relative_path'], maximum=MAX_BYTES)
    except FileNotFoundError:
        raise CoreError('file_expired') from None
    except (ValueError, OSError):
        raise CoreError('file_unavailable', retryable=True) from None


def read(account, module_id, file_id):
    return read_record(record(account, module_id, file_id))


def save(account, module_id, file_id, path):
    """Copy into the module's persistent tree before switching the durable reference."""
    from bot.utils.extension_core import _ensure_new_mutation_allowed
    _ensure_new_mutation_allowed('save_uploaded_file')
    row = record(account, module_id, file_id)
    try:
        name = relative_name(path, hidden=True)
    except (ValueError, TypeError):
        raise CoreError('invalid_request', details={'field': 'path'}) from None
    destination = f'user_files/{module_id}/{name}'
    if row['relative_path'] == destination and row['persistent']:
        return describe(row)
    content = read_record(row)
    parent, _, leaf = destination.rpartition('/')
    try:
        with directory_handle(PROJECT_ROOT, parent, create=True) as (folder, descriptor):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            fd = (os.open(leaf, flags, 0o600, dir_fd=descriptor) if descriptor is not None
                  else os.open(folder / leaf, flags, 0o600))
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                db.save_module_upload_path(file_id, destination)
            except BaseException:
                remove_regular(PROJECT_ROOT, destination)
                raise
    except FileExistsError:
        raise CoreError('file_path_in_use') from None
    except (ValueError, OSError):
        raise CoreError('file_unavailable', retryable=True) from None
    # Keep the old temporary copy until the normal seven-day sweep. Never remove
    # a previously explicitly saved file when saving a second copy elsewhere.
    return describe(db.get_module_upload(file_id, account.account_id, module_id))
