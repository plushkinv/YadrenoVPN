"""Installed typed-setting discovery and validation; no Hub-owned key catalog."""
from __future__ import annotations

from typing import Any

from core.web_ui import PRESENTATION_CONSTRAINTS, presentation_setting_contract, validate_setting
from core.results import CoreError
from database.requests import (
    EXPIRED_KEY_PANEL_CLEANUP_DELAY_DAYS_MAX,
    REFERRAL_ATTRIBUTION_WINDOW_HOURS_MAX,
)


_TEXT_KEYS = (
    'key_name_prefix', 'my_keys_item_template', 'notification_text',
    'traffic_notification_text', 'referral_new_ref_notification_text',
    'referral_purchase_notification_text',
)
_INTEGER_LIMITS = {
    'web_telegram_login_enabled': 1,
    'referral_attribution_window_hours': REFERRAL_ATTRIBUTION_WINDOW_HOURS_MAX,
    'expired_key_panel_cleanup_delay_days': EXPIRED_KEY_PANEL_CLEANUP_DELAY_DAYS_MAX,
}
CUSTOM_SETTING_KEYS = (*_TEXT_KEYS, *_INTEGER_LIMITS,
                       *('web_ui_' + name for name in PRESENTATION_CONSTRAINTS))
MAX_SETTING_LENGTH = 50_000
MAX_KEY_PREFIX_LENGTH = 30


def setting_contract(key: str) -> dict[str, Any]:
    """Return detached facts; meanings and examples remain in the shared KB."""
    if key not in CUSTOM_SETTING_KEYS:
        raise ValueError(f'setting is not allowlisted: {key}')
    if key.startswith('web_ui_'):
        return presentation_setting_contract(key.removeprefix('web_ui_'))
    constraints: dict[str, Any] = {'non_blank': True, 'max_length': MAX_SETTING_LENGTH}
    if key in _INTEGER_LIMITS:
        constraints.update(ascii_digits=True, minimum=0, maximum=_INTEGER_LIMITS[key])
        return {'value_type': 'integer_string', 'normalization': 'decimal', 'constraints': constraints}
    if key == 'key_name_prefix':
        constraints.update(no_line_breaks=True, trimmed_max_length=MAX_KEY_PREFIX_LENGTH)
    return {'value_type': 'string', 'normalization': 'strip' if key == 'key_name_prefix' else 'none',
            'constraints': constraints}


def validate_setting_value(key: str, value: Any) -> str:
    """Keep the existing string storage, supported keys and normalization."""
    if key not in CUSTOM_SETTING_KEYS:
        raise ValueError(f'setting is not allowlisted: {key}')
    if not isinstance(value, str):
        raise TypeError('setting value must be a string')
    if key.startswith('web_ui_'):
        try:
            return validate_setting(key.removeprefix('web_ui_'), value)
        except CoreError as exc:
            raise ValueError('invalid web presentation setting') from exc
    if not value.strip():
        raise ValueError('setting value must not be empty')
    if len(value) > MAX_SETTING_LENGTH:
        raise ValueError('setting value is too large')
    if key == 'key_name_prefix':
        if '\n' in value or '\r' in value:
            raise ValueError('key_name_prefix must be a single line')
        if len(value.strip()) > MAX_KEY_PREFIX_LENGTH:
            raise ValueError('key_name_prefix exceeds the key-name limit')
        return value.strip()
    if key in _INTEGER_LIMITS:
        normalized = value.strip()
        maximum = _INTEGER_LIMITS[key]
        if not normalized.isascii() or not normalized.isdecimal() or int(normalized) > maximum:
            raise ValueError(f'{key} must be a whole number from 0 to {maximum}')
        return str(int(normalized))
    return value
