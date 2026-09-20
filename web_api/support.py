"""Authenticated attachment proxy; Telegram tokens never leave the server."""
import asyncio
import io

from aiohttp import web

from core.results import CoreError
from core.support import attachment
from runtime.delivery import get_bot
from web_api.payments import account

_downloads = asyncio.Semaphore(2)
_max_bytes = 20 * 1024 * 1024


class _LimitedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > _max_bytes:
            raise CoreError('attachment_too_large')
        return super().write(data)


async def download(request):
    try:
        message_id = int(request.match_info['message_id'])
    except ValueError:
        raise CoreError('invalid_request') from None
    record = attachment(account(request), request.match_info['module_id'], message_id)
    bot = get_bot()
    if bot is None:
        raise CoreError('attachment_unavailable', retryable=True)
    try:
        await asyncio.wait_for(_downloads.acquire(), 0.1)
    except TimeoutError:
        raise CoreError('attachment_unavailable', retryable=True) from None
    try:
        async with asyncio.timeout(15):
            file = await bot.get_file(record['media_file_id'])
            if (file.file_size or 0) > _max_bytes:
                raise CoreError('attachment_too_large')
            if not file.file_path:
                raise CoreError('attachment_unavailable', retryable=True)
            with _LimitedBuffer() as buffer:
                await bot.download_file(file.file_path, destination=buffer, timeout=10)
                body = buffer.getvalue()
    except CoreError:
        raise
    except Exception:
        raise CoreError('attachment_unavailable', retryable=True) from None
    finally:
        _downloads.release()
    return web.Response(body=body, content_type='application/octet-stream', headers={
        'Content-Disposition': f'attachment; filename="support-{message_id}.bin"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def add_routes(app):
    app.router.add_get('/api/v1/modules/{module_id}/support/attachments/{message_id}', download)
