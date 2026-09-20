"""HTTP adaptation only; identities and credentials are owned by core.auth."""
from __future__ import annotations

import ipaddress
import json
import logging

from aiohttp import web

from core import auth
from core.context import bind_account_context
from core.results import CoreError

logger = logging.getLogger(__name__)
SESSION_COOKIE = '__Host-yadreno_session'
CSRF_COOKIE = '__Host-yadreno_csrf'
SESSION_KEY = getattr(web, 'RequestKey', web.AppKey)('account_session', dict)


def client_ip(request: web.Request) -> str:
    remote = request.remote or 'unknown'
    try:
        if ipaddress.ip_address(remote).is_loopback:
            # The stage-3 proxy must overwrite this header, never append to it.
            forwarded = request.headers.get('X-Real-IP')
            if forwarded:
                return str(ipaddress.ip_address(forwarded))
        return str(ipaddress.ip_address(remote))
    except ValueError:
        return remote


def _error_status(error: CoreError) -> int:
    if error.code in ('invalid_request', 'phone_invalid', 'password_invalid', 'promo_invalid'):
        return 422
    if error.code in ('temporarily_unavailable', 'authentication_busy'):
        return 503
    if error.code == 'rate_limited':
        return 429
    if error.code in ('authentication_required', 'authentication_failed',
                      'telegram_authentication_failed', 'reauthentication_required'):
        return 401
    if error.code in ('csrf_failed', 'origin_invalid', 'access_denied'):
        return 403
    if error.code in ('phone_in_use', 'credentials_changed', 'credentials_already_exist'):
        return 409
    if error.retryable:
        return 503
    if error.code.endswith('_not_found'):
        return 404
    if error.code in ('idempotency_conflict', 'quote_changed', 'quote_expired', 'order_already_paid',
                      'order_unavailable', 'balance_insufficient', 'payment_method_unavailable',
                      'action_unavailable', 'tariff_unavailable', 'quote_unavailable', 'support_closed', 'attachment_too_large'):
        return 409
    return 400


@web.middleware
async def security_middleware(request: web.Request, handler):
    from database.connection import request_connection_scope
    with request_connection_scope():
        return await _security_response(request, handler)


async def _security_response(request: web.Request, handler):
    if not request.path.startswith('/api/v1/'):
        return await handler(request)
    try:
        from web_api.app import SETTINGS_KEY
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != request.app[SETTINGS_KEY].public_origin:
                raise CoreError('origin_invalid')
            if request.content_type != 'application/json':
                raise CoreError('invalid_request')
        token = request.cookies.get(SESSION_COOKIE)
        session = None
        if token:
            try:
                session = auth.authenticate_session(token)
            except CoreError:
                pass
        anonymous = request.path in {
            '/api/v1/bootstrap', '/api/v1/openapi.json',
            '/api/v1/auth/settings', '/api/v1/auth/register', '/api/v1/auth/login',
            '/api/v1/auth/telegram', '/api/v1/auth/sms/request',
            '/api/v1/auth/sms/verify', '/api/v1/auth/password/reset',
        }
        if not anonymous and session is None:
            raise CoreError('authentication_required')
        from web_api.schemas import validate_request, validate_response
        if session is not None:
            request[SESSION_KEY] = session
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                auth.verify_csrf(session, request.headers.get('X-CSRF-Token'))
            with bind_account_context(auth.session_context(session)):
                await validate_request(request)
                response = await handler(request)
        else:
            await validate_request(request)
            response = await handler(request)
        validate_response(request, response)
    except CoreError as exc:
        response = web.json_response(exc.as_dict(), status=_error_status(exc))
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = web.json_response(CoreError('invalid_request').as_dict(), status=400)
    except web.HTTPException as exc:
        code = {404: 'route_not_found', 405: 'method_not_allowed', 413: 'request_too_large'}.get(exc.status, 'invalid_request')
        response = web.json_response(CoreError(code).as_dict(), status=exc.status)
        if exc.headers.get('Allow'):
            response.headers['Allow'] = exc.headers['Allow']
    except Exception as exc:
        # Never include request bodies, full URLs, phones, initData or tokens.
        logger.error('Web API failed type=%s', type(exc).__name__)
        response = web.json_response(CoreError('internal_error', retryable=True).as_dict(), status=500)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


async def _body(request):
    body = await request.json()
    if not isinstance(body, dict):
        raise CoreError('invalid_request')
    return body


def _text(body, key, *, optional=False, max_length=8192):
    value = body.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise CoreError('invalid_request', details={'field': key})
    return value


def _session_response(result):
    response = web.json_response({key: value for key, value in result.items() if key != 'token'})
    for name, value, http_only in ((SESSION_COOKIE, result['token'], True), (CSRF_COOKIE, result['csrf'], False)):
        response.set_cookie(name, value, max_age=auth.SESSION_SECONDS, httponly=http_only,
                            secure=True, samesite='Lax', path='/')
    return response


async def settings(request):
    return web.json_response(auth.public_auth_settings())


async def register(request):
    body = await _body(request)
    result = await auth.register(phone=_text(body, 'phone', max_length=64),
                                 password=_text(body, 'password', max_length=128),
                                 proof=_text(body, 'proof', optional=True, max_length=128),
                                 referral_code=_text(body, 'referral_code', optional=True, max_length=128),
                                 ip=client_ip(request))
    return _session_response(result)


async def login(request):
    body = await _body(request)
    result = await auth.login(phone=_text(body, 'phone', max_length=64),
                              password=_text(body, 'password', max_length=128), ip=client_ip(request))
    return _session_response(result)


async def telegram(request):
    from config import BOT_TOKEN
    body = await _body(request)
    return _session_response(auth.telegram_login(init_data=_text(body, 'init_data'),
                                                 bot_token=BOT_TOKEN, ip=client_ip(request)))


async def logout(request):
    auth.logout(request[SESSION_KEY])
    response = web.json_response({'logged_out': True})
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.del_cookie(name, path='/', secure=True, samesite='Lax')
    return response


async def session_info(request):
    session = request[SESSION_KEY]
    return web.json_response({'account_id': session['user_id'], 'telegram_id': session['telegram_id'],
                              'source': session['source'], 'expires_at': session['expires_at']})


async def credentials(request):
    body = await _body(request)
    result = await auth.set_credentials(
        session=request[SESSION_KEY], phone=_text(body, 'phone', max_length=64),
        password=_text(body, 'password', max_length=128),
        current_password=_text(body, 'current_password', optional=True, max_length=128),
        proof=_text(body, 'proof', optional=True, max_length=128), ip=client_ip(request),
    )
    return _session_response(result)


async def sms_request(request):
    body = await _body(request)
    result = await auth.request_sms(phone=_text(body, 'phone', max_length=64),
                                    purpose=_text(body, 'purpose', max_length=20),
                                    ip=client_ip(request), session=request.get(SESSION_KEY))
    return web.json_response(result)


async def sms_verify(request):
    body = await _body(request)
    result = auth.verify_sms(challenge_id=_text(body, 'challenge_id', max_length=64),
                              code=_text(body, 'code', max_length=6), ip=client_ip(request),
                              session=request.get(SESSION_KEY))
    return web.json_response(result)


async def password_reset(request):
    body = await _body(request)
    await auth.reset_password(phone=_text(body, 'phone', max_length=64),
                              password=_text(body, 'password', max_length=128),
                              proof=_text(body, 'proof', max_length=128), ip=client_ip(request))
    return web.json_response({'password_reset': True})


def add_routes(app: web.Application) -> None:
    for path, handler, method in (
        ('auth/settings', settings, 'GET'), ('auth/register', register, 'POST'),
        ('auth/login', login, 'POST'), ('auth/telegram', telegram, 'POST'),
        ('auth/logout', logout, 'POST'), ('auth/session', session_info, 'GET'),
        ('account/credentials', credentials, 'POST'), ('auth/sms/request', sms_request, 'POST'),
        ('auth/sms/verify', sms_verify, 'POST'), ('auth/password/reset', password_reset, 'POST'),
    ):
        app.router.add_route(method, '/api/v1/' + path, handler)
