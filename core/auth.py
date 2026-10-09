"""Channel-independent credentials and browser sessions for existing accounts."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import phonenumbers

from core.context import AccountContext
from core.passwords import hash_password, verify_password
from core.results import CoreError
from core.phone_verification_settings import public_auth_settings, verification_settings
from database import requests as db
from runtime.readiness import require_active

SESSION_SECONDS = 7 * 86400


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def normalize_phone(value: str) -> str:
    try:
        if not isinstance(value, str) or len(value) > 64 or not value.strip().startswith('+'):
            raise ValueError()
        number = phonenumbers.parse(value.strip(), None)
        if number.extension or not phonenumbers.is_valid_number(number):
            raise ValueError()
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (ValueError, phonenumbers.NumberParseException):
        raise CoreError('phone_invalid', details={'format': 'E.164'}) from None


def _limit(kind: str, ip: str, phone: str | None = None, *, now: int | None = None):
    now = int(time.time()) if now is None else now
    buckets = [(kind + ':ip:' + secret_hash(ip), 30, 60), (kind + ':global', 300, 60)]
    if phone:
        buckets.append((kind + ':phone:' + secret_hash(phone), 10, 300))
    if not db.consume_auth_limits(buckets, now):
        raise CoreError('rate_limited', retryable=True)


def create_session(user_id: int, source: str, version: int, *, now: int | None = None,
                   authentication_method: str | None = None) -> dict:
    require_active()
    now = int(time.time()) if now is None else now
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    user = db.create_account_session(user_id=user_id, source=source, expected_version=version,
                                     token_hash=secret_hash(token), csrf_hash=secret_hash(csrf),
                                     now=now, expires_at=now + SESSION_SECONDS,
                                     authentication_method=authentication_method or ('telegram' if source == 'mini_app' else 'password'))
    return {'token': token, 'csrf': csrf, 'expires_at': now + SESSION_SECONDS,
            'account_id': user['id'], 'telegram_id': user['telegram_id'], 'source': source}


def authenticate_session(token: str | None, *, now: int | None = None) -> dict:
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise CoreError('authentication_required')
    session = db.get_account_session(secret_hash(token), int(time.time()) if now is None else now)
    if session is None:
        raise CoreError('authentication_required')
    return session


def session_context(session: dict) -> AccountContext:
    return AccountContext(account_id=session['user_id'], telegram_id=session['telegram_id'],
                          source=session['source'])


def verify_csrf(session: dict, csrf: str | None) -> None:
    if not isinstance(csrf, str) or len(csrf) > 128 or not hmac.compare_digest(session['csrf_hash'], secret_hash(csrf)):
        raise CoreError('csrf_failed')


async def register(*, phone: str, password: str, ip: str, proof: str | None = None, referral_code: str | None = None) -> dict:
    require_active()
    phone = normalize_phone(phone)
    if referral_code is not None and (not isinstance(referral_code, str) or not 1 <= len(referral_code) <= 128
                                      or not referral_code.isascii() or not referral_code.isalnum()):
        raise CoreError('invalid_request', details={'field': 'referral_code'})
    _limit('register', ip, phone)
    settings = verification_settings()
    if settings['available'] and not proof:
        raise CoreError('verification_proof_required')
    encoded = await hash_password(password)
    user = db.create_account_credentials(phone=phone, password_hash=encoded, now=int(time.time()),
                                         verified=bool(proof), proof_hash=secret_hash(proof) if proof else None,
                                         referral_code=referral_code)
    if db.get_user_referrer(user['id']):
        from runtime.delivery import notify_new_referral
        notify_new_referral(user['id'])
    return create_session(user['id'], 'site', 1)


async def login(*, phone: str, password: str, ip: str) -> dict:
    require_active()
    phone = normalize_phone(phone)
    _limit('login', ip, phone)
    credentials = db.get_phone_credentials(phone)
    valid = await verify_password(credentials['password_hash'] if credentials else None, password)
    if not valid or not credentials or credentials['is_banned']:
        raise CoreError('authentication_failed')
    return create_session(credentials['user_id'], 'site', credentials['version'])


def telegram_login(*, init_data: str, bot_token: str, ip: str) -> dict:
    require_active()
    _limit('telegram', ip)
    from core.telegram_auth import validate_init_data
    actor = validate_init_data(init_data, bot_token, now=int(time.time()))
    user, is_new = db.get_or_create_user(actor['id'], actor.get('username'), actor.get('first_name'), actor.get('last_name'))
    from urllib.parse import parse_qsl
    from bot.services.referral_attribution import attribute_start_referral
    if attribute_start_referral(user, is_new=is_new, args=dict(parse_qsl(init_data)).get('start_param')):
        from runtime.delivery import notify_new_referral
        notify_new_referral(user['id'])
    credentials = db.get_account_credentials(user['id'])
    return create_session(user['id'], 'mini_app', credentials['version'] if credentials else 0)


def logout(session: dict) -> None:
    require_active()
    db.revoke_account_session(session['token_hash'], int(time.time()))


async def set_credentials(
    *, session: dict, phone: str, password: str, ip: str,
    current_password: str | None = None, proof: str | None = None,
) -> dict:
    require_active()
    phone = normalize_phone(phone)
    _limit('credentials', ip, phone)
    old = db.get_account_credentials(session['user_id'])
    settings = verification_settings()
    changed_phone = old is None or old['phone'] != phone
    if changed_phone and settings['available'] and not proof:
        raise CoreError('verification_proof_required')
    if old is not None and not await verify_password(old['password_hash'], current_password):
        raise CoreError('authentication_failed')
    encoded = await hash_password(password)
    now = int(time.time())
    if old is None:
        user = db.create_account_credentials(
            phone=phone, password_hash=encoded, now=now, verified=bool(proof),
            proof_hash=secret_hash(proof) if proof else None, user_id=session['user_id'],
            session_hash=session['token_hash'],
        )
        version = 1
    else:
        db.change_account_credentials(
            user_id=old['user_id'], expected_version=old['version'], phone=phone,
            password_hash=encoded, now=now,
            verified=bool(proof) if changed_phone else bool(old['phone_verified']),
            proof_hash=secret_hash(proof) if proof else None, session_hash=session['token_hash'],
        )
        user, version = {'id': old['user_id']}, old['version'] + 1
    return create_session(user['id'], session['source'], version)


async def reset_password(*, phone: str, password: str, proof: str, ip: str) -> None:
    require_active()
    phone = normalize_phone(phone)
    _limit('reset', ip, phone)
    if not verification_settings()['available']:
        raise CoreError('verification_unavailable')
    encoded = await hash_password(password)
    db.reset_account_password(phone=phone, password_hash=encoded, proof_hash=secret_hash(proof), now=int(time.time()))
