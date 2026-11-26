"""Authenticated Mini App adapter for the existing customization conversation."""
from aiohttp import web
from aiohttp.helpers import content_disposition_header

from bot.services.yadreno_admin_web_dialog import WebEditorDialog, authorize
from bot.services import yadreno_admin_web_diagnostics as diagnostics
from core.content import safe_html
from web_api.auth import SESSION_KEY, _body

EDITOR_KEY = web.AppKey('web_editor_dialog', WebEditorDialog)


def _response(value):
    """Keep legacy numeric IDs; encode larger Hub IDs without browser rounding."""
    def encode(item):
        if isinstance(item, dict):
            return {key: str(child) if key in {'request_id', 'closed_session_id'}
                    and type(child) is int and child > 2**52 - 1 else encode(child)
                    for key, child in item.items()}
        return item

    result = encode(value)
    latest = result.get('latest')
    if latest and latest.get('final'):
        latest['final']['content_html'] = safe_html(latest['final']['content'])
    return web.json_response(result)


async def state(request):
    return _response(await request.app[EDITOR_KEY].state(request[SESSION_KEY]))


async def download(request):
    async with request.app[EDITOR_KEY].download_image(request[SESSION_KEY], **request.match_info) as (image, original):
        response = web.StreamResponse(headers={
            'Content-Type': image.mime_type, 'Content-Length': str(image.size_bytes),
            'Content-Disposition': content_disposition_header('attachment', filename=image.filename),
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
        })
        with original.open('rb') as stream:
            await response.prepare(request)
            while chunk := stream.read(65536):
                await response.write(chunk)
        await response.write_eof()
        return response


async def start(request):
    body = await _body(request)
    return _response(await request.app[EDITOR_KEY].start(request[SESSION_KEY], body['message'], body['viewed']))


async def upload(request):
    from web_api.editor_upload import read_uploads
    authorize(request[SESSION_KEY])
    diagnostics.update(stage='read_uploads')
    message, viewed, uploads = await read_uploads(request)
    return _response(await request.app[EDITOR_KEY].start(request[SESSION_KEY], message, viewed, uploads=uploads))


async def resume(request):
    return _response(await request.app[EDITOR_KEY].resume(request[SESSION_KEY]))


async def cancel(request):
    return _response(await request.app[EDITOR_KEY].cancel(request[SESSION_KEY]))


async def new_chat(request):
    return _response(await request.app[EDITOR_KEY].new_chat(request[SESSION_KEY]))


async def cleanup(app):
    await app[EDITOR_KEY].close()


def add_routes(app):
    app[EDITOR_KEY] = WebEditorDialog()
    from web_api.editor_preview import add_routes as add_preview_routes
    add_preview_routes(app, app[EDITOR_KEY])
    app.on_cleanup.append(cleanup)
    prefix = '/api/v1/admin/ui/editor'
    app.router.add_get(prefix, state)
    app.router.add_get(prefix + '/requests/{request_id}/files/{file_id}', download)
    app.router.add_post(prefix + '/uploads', upload)
    for suffix, handler in (('turns', start), ('resume', resume), ('cancel', cancel), ('new-chat', new_chat)):
        app.router.add_post(prefix + '/' + suffix, handler)
