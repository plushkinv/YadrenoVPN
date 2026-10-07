"""Check public upload admission without a session or an editor operation."""
from __future__ import annotations

import asyncio
import json
import ssl

import aiohttp

from web_api.editor_upload import MAX_UPLOAD_BODY
from web_tools.setup_options import SetupError

UPLOAD_PATH = '/api/v1/admin/ui/editor/uploads'
CONTROL_BYTES = 128
RESPONSE_BYTES = 4096
BOUNDARY = 'yadreno-web-setup-probe'


async def _body(size):
    prefix = ('--' + BOUNDARY + '\r\nContent-Disposition: form-data; name="probe"\r\n\r\n').encode()
    suffix = ('\r\n--' + BOUNDARY + '--\r\n').encode()
    yield prefix
    remaining = size - len(prefix) - len(suffix)
    while remaining:
        chunk = b'x' * min(remaining, 64 * 1024)
        remaining -= len(chunk)
        yield chunk
        await asyncio.sleep(0)
    yield suffix


def _failure(size, *, status=None, reason='unexpected_response'):
    details = {'path': UPLOAD_PATH, 'body_bytes': size, 'reason': reason}
    if status is not None:
        details['http_status'] = status
    if status == 413:
        return SetupError('upload_limit_too_small',
            'HTTPS-прокси отклонил допустимый размер загрузки вложений (HTTP 413).',
            stage='https', exit_code=4, details=details)
    return SetupError('upload_check_failed',
        'HTTPS-проверка допуска загрузки вложений не получила штатный ответ API об отсутствии авторизации.',
        stage='https', exit_code=4, details=details)


async def _probe(origin, size, timeout):
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context()),
                timeout=aiohttp.ClientTimeout(total=timeout, ceil_threshold=float('inf')),
                auto_decompress=False) as client:
            async with client.post(origin + UPLOAD_PATH, headers={
                    'Origin': origin, 'Content-Type': 'multipart/form-data; boundary=' + BOUNDARY,
                    'Content-Length': str(size), 'Accept-Encoding': 'identity'},
                    data=_body(size), expect100=True, allow_redirects=False) as response:
                if response.status != 401 or response.content_type != 'application/json':
                    raise _failure(size, status=response.status)
                content = bytearray()
                async for chunk in response.content.iter_chunked(RESPONSE_BYTES):
                    if len(content) + len(chunk) > RESPONSE_BYTES:
                        raise _failure(size, status=response.status, reason='response_too_large')
                    content.extend(chunk)
                value = json.loads(content)
                if not isinstance(value, dict) or value.get('code') != 'authentication_required':
                    raise _failure(size, status=response.status)
    except SetupError:
        raise
    except asyncio.TimeoutError:
        raise _failure(size, reason='timeout') from None
    except (aiohttp.ClientError, OSError, ValueError):
        # Do not expose response bodies, headers or external exception text.
        raise _failure(size, reason='transport_or_response') from None


async def verify_uploads(origin):
    """Probe once per size; neither redirects, credentials nor retries are allowed."""
    await _probe(origin, CONTROL_BYTES, 15)
    await _probe(origin, MAX_UPLOAD_BODY, 60)
