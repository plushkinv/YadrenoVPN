"""HTTP projection of the common phone verification service and callback hint."""
import json

from aiohttp import web
from core import phone_verification as verification
from core.results import CoreError
from web_api.auth import SESSION_KEY, _body, _text, client_ip


async def request_verification(request):
    body = await _body(request)
    result = await verification.request_verification(
        phone=_text(body, 'phone', max_length=64), purpose=_text(body, 'purpose', max_length=20),
        ip=client_ip(request), session=request.get(SESSION_KEY),
    )
    return web.json_response(result)


async def verification_status(request):
    body = await _body(request)
    return web.json_response(await verification.verification_status(
        challenge_id=_text(body, 'challenge_id', max_length=64),
        ip=client_ip(request), session=request.get(SESSION_KEY),
    ))


async def verify_verification(request):
    body = await _body(request)
    return web.json_response(await verification.verify_verification(
        challenge_id=_text(body, 'challenge_id', max_length=64),
        code=_text(body, 'code', optional=True, max_length=32),
        ip=client_ip(request), session=request.get(SESSION_KEY),
    ))


async def mobile_callback(request):
    # Provider callbacks carry no session authority and cannot issue a proof.
    try:
        if request.content_type != 'application/json':
            raise CoreError('invalid_request')
        verification.receive_mobile_callback(await request.json())
    except (CoreError, json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({'accepted': False}, status=400, headers={'Cache-Control': 'no-store'})
    return web.json_response({'accepted': True}, headers={'Cache-Control': 'no-store'})


def add_routes(app):
    for name, handler in (('request', request_verification), ('status', verification_status), ('verify', verify_verification)):
        app.router.add_post('/api/v1/auth/verification/' + name, handler)
    app.router.add_post(verification.CALLBACK_PATH, mobile_callback)
