"""Private phone-verification configuration and its public availability projection."""
from __future__ import annotations

import hashlib
import json
import re

from core.results import CoreError
from database import requests as db

PREFIX = 'web_verification_'
METHOD_FIELDS = {
    'ucaller': ('ucaller_service_id', 'ucaller_secret_key'),
    'smsaero_mobile': ('smsaero_email', 'smsaero_api_key', 'smsaero_mobile_sign'),
    'smsaero_sms': ('smsaero_email', 'smsaero_api_key', 'smsaero_sms_sign'),
}
SECRET_FIELDS = frozenset(('ucaller_secret_key', 'smsaero_api_key'))
FIELDS = frozenset(field for fields in METHOD_FIELDS.values() for field in fields)


def validate_field(name: str, value: str) -> str:
    if name not in FIELDS or not isinstance(value, str):
        raise CoreError('invalid_request')
    value = value.strip()
    if not value:
        return ''
    valid = len(value) <= 512 and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    if name == 'ucaller_service_id':
        valid = valid and len(value) <= 20 and value.isascii() and value.isdecimal() and int(value) > 0
    elif name == 'smsaero_email':
        valid = valid and len(value) <= 254 and re.fullmatch(r'[^:\s@]+@[^:\s@]+\.[^:\s@]+', value) is not None
    elif name.endswith('_sign'):
        valid = valid and len(value) <= 64
    else:
        valid = valid and value.isascii() and not any(char.isspace() for char in value)
    if not valid:
        raise CoreError('invalid_request')
    return value


def provider_configuration(method: str) -> dict:
    fields = METHOD_FIELDS.get(method, ())
    values = {name: db.get_setting(PREFIX + name, '') or '' for name in fields}
    try:
        configured = bool(fields) and all(validate_field(name, value) for name, value in values.items())
    except CoreError:
        configured = False
    fingerprint = hashlib.sha256(json.dumps([method, values], sort_keys=True).encode()).hexdigest()
    return {'values': values, 'configured': configured, 'fingerprint': fingerprint}


def verification_settings() -> dict:
    method = db.get_setting(PREFIX + 'method', 'ucaller')
    enabled = db.get_setting(PREFIX + 'enabled', '0') == '1'
    configured = provider_configuration(method)['configured']
    return {'enabled': enabled, 'method': method if method in METHOD_FIELDS else None,
            'configured': configured, 'available': enabled and configured}


def public_auth_settings() -> dict:
    from core.telegram_web_auth import login_settings
    settings = verification_settings()
    return {'phone_format': 'E.164', 'verification_available': settings['available'],
            'verification_required': settings['available'], 'verification_method': settings['method'],
            'password_recovery_available': settings['available'],
            'unverified_phone_warning_required': not settings['available'],
            'telegram_login_available': login_settings()['available']}


def set_verification_option(name: str, value) -> None:
    from runtime.readiness import require_active
    require_active()
    if name == 'enabled' and type(value) is bool:
        value = '1' if value else '0'
    elif name == 'method' and isinstance(value, str) and value in METHOD_FIELDS:
        pass
    elif name in FIELDS:
        value = validate_field(name, value)
    else:
        raise CoreError('invalid_request')
    db.set_setting(PREFIX + name, value)


def admin_verification_settings() -> dict:
    """Only presence indicators leave the private credential boundary."""
    settings = verification_settings()
    return {**settings, 'fields': {name: bool(db.get_setting(PREFIX + name, '')) for name in FIELDS}}
