"""Validate Mini App initData locally for this installation's bot."""
from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from core.results import CoreError


def validate_init_data(init_data: str, bot_token: str, *, now: int) -> dict:
    try:
        if not isinstance(init_data, str) or len(init_data) > 8192:
            raise ValueError()
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError()
        signature = fields.pop('hash')
        secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        check = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        age = now - int(fields['auth_date'])
        if not -30 <= age <= 300:
            raise ValueError()
        user = json.loads(fields['user'])
        if not isinstance(user, dict) or type(user.get('id')) is not int or not 0 < user['id'] < 2**52:
            raise ValueError()
        for key in ('username', 'first_name', 'last_name'):
            if user.get(key) is not None and not isinstance(user[key], str):
                raise ValueError()
        return user
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise CoreError('telegram_authentication_failed') from None
