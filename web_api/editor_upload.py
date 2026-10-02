"""Stream authenticated editor attachments into the installation's temporary tree."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import uuid

from aiohttp import BodyPartReader, web

from bot.services.temporary_files import UPLOAD_MAX_BYTES, UPLOAD_MAX_FILES, UPLOAD_RELATIVE
from core.results import CoreError
from web_api.schemas import SCHEMAS, validate
from web_tools.editor_files import directory_handle, remove_regular

MAX_VOICE_BYTES = UPLOAD_MAX_BYTES
MAX_METADATA_BYTES = 64 * 1024
MAX_UPLOAD_BODY = UPLOAD_MAX_FILES * UPLOAD_MAX_BYTES + MAX_METADATA_BYTES
logger = logging.getLogger(__name__)


def voice_format(content, content_type):
    """Recognize supported containers; never relabel WebM as OGG."""
    if content.startswith(b'OggS') and content_type in {'audio/ogg', 'application/ogg', 'audio/opus'}:
        return 'ogg', 'audio/ogg'
    if (content.startswith(b'RIFF') and content[8:12] == b'WAVE'
            and content_type in {'audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave'}):
        return 'wav', 'audio/wav'
    raise CoreError('invalid_request', details={'reason': 'unsupported_audio'})


def _body_limit(request):
    actual = max(request.content_length or 0, request.content.total_bytes)
    if actual > MAX_UPLOAD_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD_BODY, actual_size=actual)


async def _part_bytes(part, maximum):
    content = bytearray()
    while not part.at_eof():
        chunk = await part.read_chunk(8192)
        if len(content) + len(chunk) > maximum:
            raise web.HTTPRequestEntityTooLarge(max_size=maximum, actual_size=len(content) + len(chunk))
        content.extend(chunk)
    return bytes(content)


async def _save_file(request, part, root, paths, *, voice):
    from bot.services.yadreno_admin import YadrenoAdminUpload

    filename = 'voice' if voice else (part.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not filename or '\x00' in filename or len(filename) > 255:
        raise ValueError('filename')
    mime = part.headers.get('Content-Type', 'application/octet-stream').split(';')[0].lower()
    suffix = Path(filename).suffix.lower()
    if suffix and not re.fullmatch(r'\.[a-z0-9]{1,16}', suffix):
        raise ValueError('extension')
    name = uuid.uuid4().hex + ('.upload' if voice else suffix)
    with directory_handle(root, UPLOAD_RELATIVE, create=True) as (folder, descriptor):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = (os.open(name, flags, 0o600, dir_fd=descriptor) if descriptor is not None
              else os.open(folder / name, flags, 0o600))
        path = folder / name
        paths.append(path)
        size, header = 0, b''
        with os.fdopen(fd, 'wb') as stream:
            while not part.at_eof():
                chunk = await part.read_chunk(64 * 1024)
                size += len(chunk)
                _body_limit(request)
                if size > UPLOAD_MAX_BYTES:
                    raise web.HTTPRequestEntityTooLarge(max_size=UPLOAD_MAX_BYTES, actual_size=size)
                header = (header + chunk)[:12] if len(header) < 12 else header
                stream.write(chunk)
        if voice:
            extension, mime = voice_format(header, mime)
            filename = 'voice.' + extension
        return YadrenoAdminUpload(path, filename, mime, audio_kind='voice' if voice else None)


async def read_uploads(request):
    """Run only after session, administrator, Origin and CSRF validation."""
    from bot.services.yadreno_admin import PROJECT_ROOT

    _body_limit(request)
    fields, uploads, paths = {}, [], []
    metadata_size, has_voice = 0, False
    try:
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            if (not isinstance(part, BodyPartReader) or part.headers.get('Content-Transfer-Encoding')
                    or part.headers.get('Content-Encoding')):
                raise ValueError('part')
            if part.name in {'files', 'voice', 'file'}:
                voice = part.name != 'files'
                if len(uploads) >= UPLOAD_MAX_FILES or voice and has_voice:
                    raise CoreError('invalid_request', details={'message': 'Можно отправить до пяти файлов, включая одну голосовую запись.'})
                uploads.append(await _save_file(request, part, PROJECT_ROOT, paths, voice=voice))
                has_voice = has_voice or voice
            elif part.name in {'message', 'viewed'} and part.name not in fields:
                content = await _part_bytes(part, MAX_METADATA_BYTES - metadata_size)
                metadata_size += len(content)
                fields[part.name] = content.decode('utf-8')
            else:
                raise ValueError('field')
            _body_limit(request)
        # Multipart EOF can precede a trailing epilogue. Count the entire decoded
        # body, including chunked input, before admitting any files to the Hub.
        while await request.content.read(64 * 1024):
            _body_limit(request)
        _body_limit(request)
        if set(fields) != {'message', 'viewed'} or not uploads:
            raise ValueError('fields')
        viewed = json.loads(fields['viewed'])
        validate(viewed, SCHEMAS['EditorViewed'])
        if len(fields['message']) > 8192:
            raise ValueError('message')
        return fields['message'], viewed, uploads
    except BaseException as error:
        for path in paths:
            try:
                remove_regular(PROJECT_ROOT, path.relative_to(PROJECT_ROOT).as_posix())
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as cleanup_error:
                logger.warning('Incomplete upload cleanup failed: %s', type(cleanup_error).__name__)
        if isinstance(error, (ValueError, TypeError, AssertionError)):
            raise CoreError('invalid_request') from None
        raise
