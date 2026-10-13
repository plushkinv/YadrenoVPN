"""Only declared module operations are reachable by authenticated visitors."""
from aiohttp import web

import json

from core.extensions.operations import invoke_user_operation, invoke_user_query, user_operation_status
from core.results import CoreError
from web_api.auth import _body
from web_api.payments import account


async def invoke(request):
    return web.json_response(await invoke_user_operation(
        account(request), request.match_info['module_id'], request.match_info['name'],
        await _body(request), request.headers.get('Idempotency-Key')))


async def query(request):
    try:
        if set(request.query) - {'inputs'} or len(request.query.getall('inputs', [])) > 1:
            raise ValueError()
        inputs = json.loads(request.query.get('inputs', '{}'))
    except (ValueError, TypeError):
        raise CoreError('invalid_request', details={'field': 'inputs'}) from None
    return web.json_response({'result': await invoke_user_query(
        account(request), request.match_info['module_id'], request.match_info['name'], inputs)})


async def status(request):
    return web.json_response(user_operation_status(
        account(request), request.match_info['module_id'], request.match_info['operation_id']))


def add_routes(app):
    app.router.add_post('/api/v1/modules/{module_id}/operations/{name}', invoke)
    app.router.add_get('/api/v1/modules/{module_id}/queries/{name}', query)
    app.router.add_get('/api/v1/modules/{module_id}/operations/{operation_id}', status)
    from web_api.support import add_routes as add_support_routes
    add_support_routes(app)
    from web_api.module_uploads import add_routes as add_upload_routes
    add_upload_routes(app)
