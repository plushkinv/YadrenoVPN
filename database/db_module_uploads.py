"""Ownership and durable Telegram references for module uploads."""
import time

from .connection import get_db

__all__ = ['create_module_upload', 'get_module_upload', 'save_module_upload_path',
           'get_support_uploaded_file', 'set_module_upload_telegram_file']


def create_module_upload(file_id, user_id, module_id, filename, content_type, size, relative_path):
    with get_db() as conn:
        conn.execute('INSERT INTO module_uploads(id,user_id,module_id,filename,content_type,size,relative_path,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?)',
                     (file_id, user_id, module_id, filename, content_type, size, relative_path, int(time.time())))
    return get_module_upload(file_id, user_id, module_id)


def get_module_upload(file_id, user_id, module_id=None):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM module_uploads WHERE id=? AND user_id=? '
                           'AND (? IS NULL OR module_id=?)', (file_id, user_id, module_id, module_id)).fetchone()
        return dict(row) if row else None


def save_module_upload_path(file_id, relative_path):
    with get_db() as conn:
        conn.execute('UPDATE module_uploads SET relative_path=?,persistent=1 WHERE id=?', (relative_path, file_id))


def get_support_uploaded_file(message_id):
    with get_db() as conn:
        row = conn.execute('SELECT u.* FROM module_uploads u JOIN support_messages m ON m.uploaded_file_id=u.id '
                           'WHERE m.id=?', (message_id,)).fetchone()
        return dict(row) if row else None


def set_module_upload_telegram_file(file_id, telegram_file_id):
    with get_db() as conn:
        conn.execute('UPDATE module_uploads SET telegram_file_id=? WHERE id=?', (telegram_file_id, file_id))
        conn.execute("UPDATE support_messages SET media_type='document',media_file_id=? WHERE uploaded_file_id=?",
                     (telegram_file_id, file_id))
