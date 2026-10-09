"""HTTP adaptation only; identities and credentials are owned by core.auth."""
from __future__ import annotations

import ipaddress
import json
import logging

from aiohttp import web

from core import auth
from core.context import bind_account_context
from core.results import CoreError
from bot.services import yadreno_admin_web_diagnostics as editor_diagnostics

logger = logging.getLogger(__name__)
SESSION_COOKIE = '__Host-yadreno_session'
CSRF_COOKIE = '__Host-yadreno_csrf'
TELEGRAM_LOGIN_COOKIE = '__Host-yadreno_telegram_login'
SESSION_KEY = getattr(web, 'RequestKey', web.AppKey)('account_session', dict)


def client_ip(request: web.Request) -> str:
    remote = request.remote or 'unknown'
    try:
        from web_api.app import SETTINGS_KEY
        peer = ipaddress.ip_address(remote)
        if any(peer in ipaddress.ip_network(value) for value in request.app[SETTINGS_KEY].trusted_proxies):
            # The configured proxy overwrites this single address, never appends.
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
    if error.code in ('conflict', 'phone_in_use', 'credentials_changed', 'credentials_already_exist'):
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
        operation = editor_diagnostics.route_operation(request.path)
        if operation is not None:
            with editor_diagnostics.scope(operation):
                return await _security_response(request, handler)
        return await _security_response(request, handler)


async def _security_response(request: web.Request, handler):
    if not request.path.startswith('/api/v1/'):
        return await handler(request)
    try:
        from web_api.app import SETTINGS_KEY
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != request.app[SETTINGS_KEY].public_origin:
                raise CoreError('origin_invalid')
            expected_type = ('multipart/form-data' if request.path == '/api/v1/admin/ui/editor/uploads'
                             else 'application/json')
            if request.content_type != expected_type:
                raise CoreError('invalid_request')
        token = request.cookies.get(SESSION_COOKIE)
        session = None
        if token:
            try:
                session = auth.authenticate_session(token)
            except CoreError:
                pass
        anonymous = request.path in {
            '/api/v1/bootstrap', '/api/v1/openapi.json', '/api/v1/ui/settings', '/api/v1/ui/manifest',
            '/api/v1/auth/settings', '/api/v1/auth/register', '/api/v1/auth/login',
            '/api/v1/auth/telegram', '/api/v1/auth/verification/request', '/api/v1/auth/verification/status',
            '/api/v1/auth/telegram/web/start', '/api/v1/auth/telegram/web/finish',
            '/api/v1/auth/verification/verify', '/api/v1/auth/password/reset',
            '/api/v1/client-import/resolve',
        }
        anonymous = anonymous or request.method in {'GET', 'HEAD'} and request.path.startswith('/api/v1/ui/packages/')
        if not anonymous and session is None:
            raise CoreError('authentication_required')
        from web_api.schemas import validate_request, validate_response
        if session is not None:
            request[SESSION_KEY] = session
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                auth.verify_csrf(session, request.headers.get('X-CSRF-Token'))
            with bind_account_context(auth.session_context(session)):
                editor_diagnostics.update(stage='validation')
                await validate_request(request)
                response = await handler(request)
        else:
            await validate_request(request)
            response = await handler(request)
        validate_response(request, response)
    except CoreError as exc:
        editor_diagnostics.report(exc)
        response = web.json_response(exc.as_dict(), status=_error_status(exc))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        error = CoreError('invalid_request')
        editor_diagnostics.report(exc, error)
        response = web.json_response(error.as_dict(), status=400)
    except web.HTTPException as exc:
        code = {404: 'route_not_found', 405: 'method_not_allowed', 413: 'request_too_large'}.get(exc.status, 'invalid_request')
        error = CoreError(code)
        editor_diagnostics.report(exc, error)
        response = web.json_response(error.as_dict(), status=exc.status)
        if exc.headers.get('Allow'):
            response.headers['Allow'] = exc.headers['Allow']
    except Exception as exc:
        # Never include request bodies, full URLs, phones, initData or tokens.
        error = CoreError('internal_error', retryable=True)
        if editor_diagnostics.report(exc, error) is None:
            logger.error('Web API failed type=%s', type(exc).__name__)
        response = web.json_response(error.as_dict(), status=500)
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


def _session_response(result, *, payload=None):
    response = web.json_response(payload if payload is not None else {key: value for key, value in result.items() if key != 'token'})
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


async def telegram_web_start(request):
    from core import telegram_web_auth
    result, browser = telegram_web_auth.start(ip=client_ip(request))
    response = web.json_response(result)
    response.set_cookie(TELEGRAM_LOGIN_COOKIE, browser, max_age=300, httponly=True,
                        secure=True, samesite='Lax', path='/')
    return response


async def telegram_web_finish(request):
    from core import telegram_web_auth
    body = await _body(request)
    result = await telegram_web_auth.finish(id_token=_text(body, 'id_token', max_length=16384),
        browser=request.cookies.get(TELEGRAM_LOGIN_COOKIE), ip=client_ip(request))
    response = _session_response(result)
    response.del_cookie(TELEGRAM_LOGIN_COOKIE, path='/', secure=True, samesite='Lax')
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
        ('auth/telegram/web/start', telegram_web_start, 'POST'),
        ('auth/telegram/web/finish', telegram_web_finish, 'POST'),
        ('auth/logout', logout, 'POST'), ('auth/session', session_info, 'GET'),
        ('account/credentials', credentials, 'POST'), ('auth/password/reset', password_reset, 'POST'),
    ):
        app.router.add_route(method, '/api/v1/' + path, handler)
