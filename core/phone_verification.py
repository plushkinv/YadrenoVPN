"""Shared phone ownership checks for registration, credential changes and reset."""
from __future__ import annotations

import asyncio
import secrets
import time
import uuid
import weakref

from bot.services.phone_verification import smsaero, ucaller
from bot.services.phone_verification.http import InvalidProviderCode
from core.auth import _limit, normalize_phone, secret_hash
from core.phone_verification_settings import provider_configuration, verification_settings
from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active

CALLBACK_PATH = '/webhooks/phone-verification/smsaero'
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock(challenge_id):
    lock = _LOCKS.get(challenge_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[challenge_id] = lock
    return lock


def _active():
    require_active()
    settings = verification_settings()
    if not settings['available']:
        raise CoreError('verification_unavailable')
    return settings


def _session_hash(row, session):
    if row['purpose'] != 'credentials':
        return None
    return session['token_hash'] if session else None


def _challenge(challenge_id, session):
    _active()
    if not isinstance(challenge_id, str) or not 1 <= len(challenge_id) <= 64:
        raise CoreError('verification_code_invalid')
    row = db.get_auth_challenge(challenge_id)
    if row is None or row['session_hash'] != _session_hash(row, session):
        raise CoreError('verification_code_invalid')
    configuration = provider_configuration(row['method'])
    if not configuration['configured'] or configuration['fingerprint'] != row['provider_config_hash']:
        raise CoreError('verification_expired')
    return row, configuration['values']


def _projection(row, proof=None):
    if row['expires_at'] <= int(time.time()):
        state = 'expired'
    elif row['state'] in ('failed', 'verified', 'used'):
        state = 'verified' if row['state'] in ('verified', 'used') else 'failed'
    elif row['method'] != 'smsaero_mobile':
        state = 'code_required'
    elif row['state'] == 'unknown' and not row['provider_request_id']:
        state = 'unknown'
    else:
        state = row['provider_state']
    return {'challenge_id': row['id'], 'method': row['method'], 'state': state,
            'send_state': row['state'] if row['state'] in ('sent', 'failed', 'unknown') else 'sent',
            'expires_at': row['expires_at'], 'resend_at': row['created_at'] + 60,
            'code_length': {'ucaller': 4, 'smsaero_sms': 6}.get(row['method']), 'proof': proof}


async def request_verification(*, phone: str, purpose: str, ip: str, session: dict | None = None) -> dict:
    settings = _active()
    phone = normalize_phone(phone)
    if purpose not in ('register', 'reset', 'credentials'):
        raise CoreError('invalid_request')
    if purpose == 'credentials' and session is None:
        raise CoreError('authentication_required')
    method = settings['method']
    configuration = provider_configuration(method)
    callback_url = ''
    if method == 'smsaero_mobile':
        origin = db.get_setting('web_public_origin', '') or ''
        if not origin:
            raise CoreError('verification_unavailable')
        callback_url = origin + CALLBACK_PATH
    now = int(time.time())
    _limit('verification', ip, phone, now=now)
    if not db.consume_auth_limits([
        ('verification:send:' + secret_hash(phone), 1, 60),
        ('verification:daily:' + secret_hash(phone), 10, 86400),
        ('verification:installation_hour', 100, 3600),
        ('verification:installation_day', 500, 86400),
    ], now):
        raise CoreError('rate_limited', retryable=True)
    challenge_id = secrets.token_urlsafe(24)
    code = (f'{secrets.randbelow(9999) + 1:04}' if method == 'ucaller' else
            f'{secrets.randbelow(1000000):06}' if method == 'smsaero_sms' else None)
    db.create_auth_challenge(
        challenge_id=challenge_id, phone=phone, purpose=purpose, method=method,
        code_hash=secret_hash(challenge_id + ':' + code) if code is not None else None,
        provider_config_hash=configuration['fingerprint'], now=now,
        session_hash=session['token_hash'] if purpose == 'credentials' else None,
        user_id=session['user_id'] if purpose == 'credentials' else None,
    )
    from core.web_ui import public_settings
    send = {'ucaller': ucaller.send, 'smsaero_mobile': smsaero.send_mobile, 'smsaero_sms': smsaero.send_sms}[method]
    result = await send(configuration=configuration['values'], phone=phone, code=code,
                        unique=str(uuid.uuid4()), callback_url=callback_url, title=public_settings()['title'])
    db.finish_auth_challenge_send(challenge_id, result.delivery, result.state, result.request_id)
    return _projection(db.get_auth_challenge(challenge_id))


async def _refresh(row, configuration):
    now = int(time.time())
    if row['method'] == 'smsaero_mobile' and db.claim_auth_provider_check(row['id'], now):
        result = await smsaero.mobile_status(configuration=configuration, phone=row['phone'],
                                             identifier=row['provider_request_id'])
        if result.delivery != 'unknown':
            db.update_auth_provider_result(row['id'], result.state, int(time.time()))
    return db.get_auth_challenge(row['id'])


async def verification_status(*, challenge_id: str, ip: str, session: dict | None = None) -> dict:
    _active()
    _limit('verification_status', ip)
    async with _lock(challenge_id):
        row, configuration = _challenge(challenge_id, session)
        row = await _refresh(row, configuration)
        _challenge(challenge_id, session)
        return _projection(row)


async def verify_verification(*, challenge_id: str, ip: str, code: str | None = None,
                              session: dict | None = None) -> dict:
    _active()
    _limit('verification_verify', ip)
    async with _lock(challenge_id):
        row, configuration = _challenge(challenge_id, session)
        now = int(time.time())
        if row['expires_at'] <= now:
            raise CoreError('verification_expired')
        if row['state'] not in ('sent', 'unknown', 'sending'):
            raise CoreError('verification_code_invalid')
        proof = secrets.token_urlsafe(32)
        session_hash = _session_hash(row, session)
        if row['method'] != 'smsaero_mobile':
            if not isinstance(code, str) or len(code) > 32:
                raise CoreError('verification_code_invalid')
            if not db.verify_auth_challenge(
                challenge_id=challenge_id, code_hash=secret_hash(challenge_id + ':' + code),
                proof_hash=secret_hash(proof), now=now, session_hash=session_hash,
            ):
                raise CoreError('verification_code_invalid')
        else:
            if not row['provider_request_id']:
                raise CoreError('verification_unavailable')
            if code is not None:
                if (row['provider_state'] != 'code_required'
                        or not db.reserve_auth_challenge_attempt(challenge_id, now, session_hash)):
                    raise CoreError('verification_code_invalid')
                try:
                    if not isinstance(code, str) or not 1 <= len(code) <= 32 or not code.isascii() or not code.isdecimal():
                        raise InvalidProviderCode()
                    result = await smsaero.verify_mobile(configuration=configuration, phone=row['phone'],
                        identifier=row['provider_request_id'], code=code)
                except InvalidProviderCode:
                    db.fail_exhausted_auth_challenge(challenge_id)
                    raise CoreError('verification_code_invalid') from None
                if result.delivery != 'unknown':
                    db.update_auth_provider_result(challenge_id, result.state, int(time.time()))
                row = db.get_auth_challenge(challenge_id)
            else:
                row = await _refresh(row, configuration)
            _challenge(challenge_id, session)
            if row['provider_state'] != 'confirmed':
                return _projection(row)
            if not db.verify_auth_provider_challenge(
                challenge_id=challenge_id, proof_hash=secret_hash(proof), now=int(time.time()), session_hash=session_hash,
            ):
                raise CoreError('verification_code_invalid')
        return _projection(db.get_auth_challenge(challenge_id), proof)


def receive_mobile_callback(body):
    """Accept only a wake-up hint; an authenticated provider read attests success."""
    require_active()
    from bot.services.phone_verification.http import request_id
    if not isinstance(body, dict) or set(body) != {'id', 'status', 'number'}:
        raise CoreError('invalid_request')
    identifier = request_id(body['id'])
    number = str(body['number'])
    if (identifier is None or type(body['status']) is not int or body['status'] not in (0, 8, 3, 1, 2, 16)
            or not number.isascii() or not number.isdecimal() or not 7 <= len(number) <= 15):
        raise CoreError('invalid_request')
    db.hint_auth_provider_check(identifier, '+' + number, int(time.time()))
