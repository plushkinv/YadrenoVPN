"""Only declared module operations are reachable by authenticated visitors."""
from aiohttp import web

from core.extensions.operations import invoke_user_operation
from web_api.auth import _body
from web_api.payments import account


async def invoke(request):
    return web.json_response(await invoke_user_operation(
        account(request), request.match_info['module_id'], request.match_info['name'],
        await _body(request), request.headers.get('Idempotency-Key')))


def add_routes(app):
    app.router.add_post('/api/v1/modules/{module_id}/operations/{name}', invoke)
    from web_api.support import add_routes as add_support_routes
    add_support_routes(app)
