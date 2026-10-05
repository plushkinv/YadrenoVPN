"""Small presentation settings and a read-only installation preview projection."""
from __future__ import annotations

import time
import json
from copy import deepcopy

from core.results import CoreError
from database import requests as db
from database.web_ui_defaults import WEB_UI_DEFAULTS as DEFAULTS


PRESENTATION_CONSTRAINTS = {
    'preset': {'enum': ['clear', 'signal', 'friendly']},
    'theme': {'enum': ['light', 'dark']},
    'sync_interval_seconds': {'ascii_digits': True, 'minimum': 30, 'maximum': 86400},
}


def presentation_setting_contract(name: str) -> dict:
    """Expose installed value facts using the same limits as the setter."""
    if name not in PRESENTATION_CONSTRAINTS:
        raise CoreError('invalid_request')
    return {'value_type': 'integer_string' if name == 'sync_interval_seconds' else 'string',
            'normalization': 'strip', 'constraints': deepcopy(PRESENTATION_CONSTRAINTS[name])}

def public_settings():
    from core.bot_profile import presentation
    values = {key: db.get_setting('web_ui_' + key, default) for key, default in DEFAULTS.items()}
    # Invalid saved values are diagnosed by the setter; read safe stock defaults.
    for key in values:
        try:
            values[key] = validate_setting(key, values[key])
        except CoreError:
            values[key] = DEFAULTS[key]
    return {**values, **presentation(),
            'sync_interval_seconds': int(values['sync_interval_seconds'])}


def validate_setting(name, value):
    if name not in DEFAULTS or not isinstance(value, str):
        raise CoreError('invalid_request')
    value = value.strip()
    constraints = PRESENTATION_CONSTRAINTS[name]
    if 'enum' in constraints and value not in constraints['enum']:
        raise CoreError('invalid_request')
    if name == 'sync_interval_seconds' and (not value.isascii() or not value.isdecimal()
            or not constraints['minimum'] <= int(value) <= constraints['maximum']):
        raise CoreError('invalid_request')
    return value


def set_presentation_setting(name, value):
    from runtime.readiness import require_active
    require_active()
    value = validate_setting(name, value)
    db.set_setting('web_ui_' + name, value)
    return public_settings()


def require_administrator(telegram_id):
    from bot.utils.admin import is_admin
    if type(telegram_id) is not int or telegram_id <= 0 or not is_admin(telegram_id):
        raise CoreError('access_denied')


def require_preview_administrator(session):
    if session.get('source') != 'mini_app':
        raise CoreError('access_denied')
    require_administrator(session.get('telegram_id'))


def public_modules():
    from core.extensions.registry import inspect_modules
    return [{key: item[key] for key in ('module_id', 'version', 'api_version', 'state')} for item in inspect_modules()]


def preview_snapshot(session):
    """Configuration only: no impersonation, pricing hooks or user transactions."""
    require_preview_administrator(session)
    tariffs = []
    for tariff in db.get_all_tariffs(include_hidden=False):
        if tariff.get('system_type') is not None or not tariff.get('is_active'):
            continue
        tariffs.append({key: tariff.get(key) for key in ('id', 'name', 'group_id', 'duration_days',
                                                         'price_minor', 'traffic_limit_gb', 'max_ips')})
    trials = []
    for offer in db.get_all_trial_offers():
        if offer['is_enabled']:
            trials.append({key: offer.get(key) for key in ('offer_id', 'tariff_name', 'duration_days', 'traffic_limit_gb')})
    from core.payment_offers import balance_spending_enabled
    return {'settings': public_settings(), 'captured_at': int(time.time()), 'currency': db.get_base_currency(), 'modules': public_modules(),
            'tariffs': tariffs, 'trial_offers': trials,
            'features': {'subscriptions': True, 'trial': bool(trials), 'promotions': db.has_available_promo_codes(),
                         'referrals': db.is_referral_enabled(), 'balance': balance_spending_enabled(),
                         'subscription_import': True, 'support_chat': False}}


def stock_web_button_position():
    """Return the stock anchor only while the saved effective layout keeps it."""
    from database.web_ui_defaults import WEB_CABINET_BUTTON_ID
    row = db.get_page('main')
    try:
        defaults = json.loads(row['buttons_default'] or '[]')
        customs = json.loads(row['buttons_custom'] or '[]')
        base = next(button for button in defaults if button['id'] == WEB_CABINET_BUTTON_ID)
        current = next((button for button in customs if button['id'] == WEB_CABINET_BUTTON_ID), base)
        if (base.get('action_type') == 'web_app' and base.get('action_value') == '%web_app_url%'
                and current.get('action_type') == 'web_app'
                and current.get('row') == base.get('row') and current.get('col') == base.get('col') == 0):
            return current['row']
    except (KeyError, TypeError, ValueError, StopIteration):
        pass
    return None


def enable_web_cabinet_button():
    """Installation completion can reveal the owned button without resetting a custom layout."""
    from bot.services.yadreno_admin_page_patch import PagePatch
    from database.web_ui_defaults import WEB_CABINET_BUTTON_ID
    from runtime.readiness import require_active
    require_active()
    row = db.get_page('main')
    try:
        patch = PagePatch(row)
        base = next(button for button in patch.defaults if button['id'] == WEB_CABINET_BUTTON_ID)
        current = next(button for button in patch.buttons() if button['id'] == WEB_CABINET_BUTTON_ID)
        if base.get('action_type') != 'web_app' or base.get('action_value') != '%web_app_url%' or current.get('action_type') != 'web_app':
            raise ValueError('button identity collision')
        from bot.utils.web_app_buttons import public_web_app_url, validate_web_app_url
        validate_web_app_url(public_web_app_url())
        validate_web_app_url(current.get('action_value'), template=True)
        patch.apply([{'type': 'button.update', 'id': WEB_CABINET_BUTTON_ID, 'patch': {'is_hidden': False}}])
        changes = patch.patch()
    except (KeyError, TypeError, ValueError, StopIteration):
        return {'changed': False, 'code': 'web_button_unavailable'}
    if 'buttons_custom' in changes:
        db.update_page_custom('main', buttons=json.dumps(changes['buttons_custom'], ensure_ascii=False))
    return {'changed': bool(changes), 'code': 'ready'}
