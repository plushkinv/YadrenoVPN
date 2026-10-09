"""Telegram Login JWT validation; browser login never grants Mini App authority."""
from __future__ import annotations

import asyncio
import secrets
import time

import jwt

from core import auth
from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active

_KEYS = jwt.PyJWKClient('https://oauth.telegram.org/.well-known/jwks.json', timeout=5, lifespan=300)


def login_settings() -> dict:
    from core.account_links import installation_bot_id
    enabled = db.get_setting('web_telegram_login_enabled', '0') == '1'
    try:
        client_id = installation_bot_id()
    except CoreError:
        client_id = 0
    return {'enabled': enabled, 'available': enabled and client_id > 0, 'client_id': client_id}


def start(*, ip: str) -> tuple[dict, str]:
    require_active()
    settings = login_settings()
    if not settings['available']:
        raise CoreError('telegram_login_unavailable')
    auth._limit('telegram_web', ip)
    browser, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    db.create_telegram_login_challenge(auth.secret_hash(browser), auth.secret_hash(nonce), now)
    return {'client_id': settings['client_id'], 'nonce': nonce, 'expires_at': now + 300}, browser


def _claims(id_token: str, client_id: int) -> dict:
    if jwt.get_unverified_header(id_token).get('alg') != 'RS256':
        raise jwt.InvalidAlgorithmError('unsupported Telegram signing algorithm')
    key = _KEYS.get_signing_key_from_jwt(id_token)
    return jwt.decode(id_token, key.key, algorithms=['RS256'], audience=str(client_id),
                      issuer='https://oauth.telegram.org',
                      options={'require': ['iss', 'aud', 'exp', 'iat', 'nonce', 'id']})


async def finish(*, id_token: str, browser: str | None, ip: str) -> dict:
    require_active()
    settings = login_settings()
    if not settings['available']:
        raise CoreError('telegram_login_unavailable')
    auth._limit('telegram_web_finish', ip)
    if not isinstance(browser, str) or len(browser) != 43 or not isinstance(id_token, str) or len(id_token) > 16384:
        raise CoreError('telegram_authentication_failed')
    try:
        claims = await asyncio.to_thread(_claims, id_token, settings['client_id'])
    except jwt.PyJWKClientConnectionError:
        raise CoreError('temporarily_unavailable', retryable=True) from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise CoreError('telegram_authentication_failed') from None
    actor_id, nonce = claims.get('id'), claims.get('nonce')
    if type(actor_id) is not int or not 0 < actor_id < 2**52 or not isinstance(nonce, str) or len(nonce) != 43:
        raise CoreError('telegram_authentication_failed')
    if not db.consume_telegram_login_challenge(auth.secret_hash(browser), auth.secret_hash(nonce), int(time.time())):
        raise CoreError('telegram_authentication_failed')
    def field(name):
        value = claims.get(name)
        return value[:256] if isinstance(value, str) else None
    user, _ = db.get_or_create_user(actor_id, field('preferred_username'),
                                     field('given_name') or field('name'), field('family_name'))
    credentials = db.get_account_credentials(user['id'])
    return auth.create_session(user['id'], 'site', credentials['version'] if credentials else 0,
                               authentication_method='telegram')
