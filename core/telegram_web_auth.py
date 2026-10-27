"""Telegram Login JWT validation; browser login never grants Mini App authority."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import secrets
import time
import zlib
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt

from core import auth
from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active

_ISSUER = 'https://oauth.telegram.org'
logger = logging.getLogger(__name__)


def _oauth_urls() -> tuple[str, ...]:
    """Read installation transport configuration without adding fallback hosts."""
    import config

    value = getattr(config, 'TELEGRAM_OAUTH_URL', _ISSUER)
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)) or not values:
        raise jwt.PyJWKClientConnectionError('Invalid Telegram OAuth transport configuration')
    urls: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise jwt.PyJWKClientConnectionError('Invalid Telegram OAuth transport configuration')
        url = item.strip().rstrip('/')
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise jwt.PyJWKClientConnectionError('Invalid Telegram OAuth transport configuration') from None
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or \
                parsed.password or parsed.query or parsed.fragment:
            raise jwt.PyJWKClientConnectionError('Invalid Telegram OAuth transport configuration')
        if url not in urls:
            urls.append(url)
    return tuple(urls)


class _OAuthJWKClient(jwt.PyJWKClient):
    """Keep PyJWT key lookup/cache; vary only the JWKS HTTP transport."""

    def fetch_data(self) -> dict:
        data = None
        try:
            urls = _oauth_urls()
            for index, base in enumerate(urls):
                try:
                    request = Request(base + '/.well-known/jwks.json', headers={'Accept-Encoding': 'gzip'})
                    with urlopen(request, timeout=self.timeout) as response:
                        body = response.read()
                        if response.headers.get('Content-Encoding', '').strip().lower() == 'gzip':
                            body = gzip.decompress(body)
                    data = json.loads(body)
                    return data
                except (URLError, TimeoutError, gzip.BadGzipFile, EOFError, zlib.error) as error:
                    if index == len(urls) - 1:
                        raise jwt.PyJWKClientConnectionError('Failed to fetch Telegram signing keys') from error
            raise jwt.PyJWKClientConnectionError('No Telegram OAuth endpoint available')
        finally:
            if self.jwk_set_cache is not None:
                self.jwk_set_cache.put(data)


_KEYS = _OAuthJWKClient(_ISSUER + '/.well-known/jwks.json', timeout=5, lifespan=300)


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
                      issuer=_ISSUER,
                      options={'require': ['iss', 'aud', 'exp', 'iat', 'nonce', 'id']})


async def finish(*, id_token: str, browser: str | None, ip: str) -> dict:
    require_active()
    settings = login_settings()
    if not settings['available']:
        raise CoreError('telegram_login_unavailable')
    auth._limit('telegram_web_finish', ip)
    if not isinstance(browser, str) or len(browser) != 43:
        logger.warning('telegram_web_login_failed stage=browser_cookie')
        raise CoreError('telegram_authentication_failed')
    if not isinstance(id_token, str) or len(id_token) > 16384:
        logger.warning('telegram_web_login_failed stage=token_input')
        raise CoreError('telegram_authentication_failed')
    try:
        claims = await asyncio.to_thread(_claims, id_token, settings['client_id'])
    except jwt.PyJWKClientConnectionError as error:
        logger.warning('telegram_web_login_failed stage=jwks error_type=%s cause_type=%s',
                       type(error).__name__, type(error.__cause__).__name__)
        raise CoreError('temporarily_unavailable', retryable=True) from None
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        missing = getattr(error, 'claim', None) if isinstance(error, jwt.MissingRequiredClaimError) else None
        missing = missing if missing in ('iss', 'aud', 'exp', 'iat', 'nonce', 'id') else None
        logger.warning('telegram_web_login_failed stage=jwt error_type=%s missing_claim=%s',
                       type(error).__name__, missing)
        raise CoreError('telegram_authentication_failed') from None
    actor_id, nonce = claims.get('id'), claims.get('nonce')
    if type(actor_id) is not int or not 0 < actor_id < 2**52:
        logger.warning('telegram_web_login_failed stage=identity id_type=%s', type(actor_id).__name__)
        raise CoreError('telegram_authentication_failed')
    if not isinstance(nonce, str) or len(nonce) != 43:
        logger.warning('telegram_web_login_failed stage=nonce_format nonce_type=%s', type(nonce).__name__)
        raise CoreError('telegram_authentication_failed')
    if not db.consume_telegram_login_challenge(auth.secret_hash(browser), auth.secret_hash(nonce), int(time.time())):
        logger.warning('telegram_web_login_failed stage=challenge')
        raise CoreError('telegram_authentication_failed')
    def field(name):
        value = claims.get(name)
        return value[:256] if isinstance(value, str) else None
    user, _ = db.get_or_create_user(actor_id, field('preferred_username'),
                                     field('given_name') or field('name'), field('family_name'))
    credentials = db.get_account_credentials(user['id'])
    return auth.create_session(user['id'], 'site', credentials['version'] if credentials else 0,
                               authentication_method='telegram')
