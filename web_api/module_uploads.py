"""Stream one authenticated multipart file; binary data never enters JSON."""
import os
import uuid

from aiohttp import BodyPartReader, web

from core import uploads
from core.results import CoreError
from database import requests as db
from web_api.payments import account
from web_tools.editor_files import directory_handle, remove_regular

MAX_BODY = uploads.MAX_BYTES + 64 * 1024


def _limit(request):
    size = max(request.content_length or 0, request.content.total_bytes)
    if size > MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=size)


async def upload(request):
    actor, module_id = account(request), request.match_info['module_id']
    uploads.authorize(actor, module_id)
    _limit(request)
    file_id = uuid.uuid4().hex
    relative = f'{uploads.TEMP_DIRECTORY}/{file_id}'
    created = False
    try:
        reader = await request.multipart()
        part = await reader.next()
        if (not isinstance(part, BodyPartReader) or part.name != 'file' or not part.filename
                or part.headers.get('Content-Transfer-Encoding') or part.headers.get('Content-Encoding')):
            raise ValueError('file')
        filename = part.filename.replace('\\', '/').rsplit('/', 1)[-1]
        if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise ValueError('filename')
        content_type = part.headers.get('Content-Type', 'application/octet-stream').split(';')[0][:255]
        with directory_handle(uploads.PROJECT_ROOT, uploads.TEMP_DIRECTORY, create=True) as (folder, descriptor):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            fd = (os.open(file_id, flags, 0o600, dir_fd=descriptor) if descriptor is not None
                  else os.open(folder / file_id, flags, 0o600))
            created = True
            size = 0
            with os.fdopen(fd, 'wb') as stream:
                while not part.at_eof():
                    chunk = await part.read_chunk(64 * 1024)
                    size += len(chunk)
                    _limit(request)
                    if size > uploads.MAX_BYTES:
                        raise web.HTTPRequestEntityTooLarge(max_size=uploads.MAX_BYTES, actual_size=size)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if await reader.next() is not None:
            raise ValueError('only one file is accepted')
        while await request.content.read(64 * 1024):
            _limit(request)
        _limit(request)
        # Recheck the account after streaming, including concurrent logout/merge.
        from core.auth import authenticate_session
        from web_api.auth import SESSION_COOKIE
        session = authenticate_session(request.cookies.get(SESSION_COOKIE))
        if session['user_id'] != actor.account_id:
            raise CoreError('authentication_required')
        uploads.authorize(actor, module_id)
        row = db.create_module_upload(file_id, actor.account_id, module_id, filename, content_type, size, relative)
        return web.json_response(uploads.describe(row))
    except BaseException as error:
        if created:
            remove_regular(uploads.PROJECT_ROOT, relative)
        if isinstance(error, (ValueError, TypeError, AssertionError)):
            raise CoreError('invalid_request') from None
        raise


async def download(request):
    actor, module_id = account(request), request.match_info['module_id']
    uploads.authorize(actor, module_id)
    row = uploads.record(actor, module_id, request.match_info['file_id'])
    return web.Response(body=uploads.read_record(row), content_type='application/octet-stream', headers={
        'Content-Disposition': f'attachment; filename="{row["id"]}.bin"'})


def add_routes(app):
    app.router.add_post('/api/v1/modules/{module_id}/files', upload)
    app.router.add_get('/api/v1/modules/{module_id}/files/{file_id}', download)
