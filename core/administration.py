"""Private administrator settings; never exposed through the user facade."""
from core.results import CoreError
from database import requests as db


def web_diagnostics(application=None):
    from core.auth import public_auth_settings
    from core.extensions.registry import inspect_modules
    from runtime.readiness import is_active
    from web_api.settings import get_web_settings
    try:
        settings = get_web_settings()
        configured, enabled, origin = bool(settings.public_origin), settings.enabled, settings.public_origin
        error = None
    except ValueError:
        configured, enabled, origin, error = False, False, '', 'invalid_web_settings'
    return {'api_version': 1, 'core_active': is_active(), 'configured': configured,
            'enabled': enabled, 'public_origin': origin, 'error': error,
            'listener_running': application.web_server is not None if application else None,
            'sms': {**public_auth_settings(), 'key_configured': bool(db.get_setting('web_sms_api_key', ''))},
            'modules': inspect_modules()}


def set_sms_option(name, value):
    from runtime.readiness import require_active
    require_active()
    if name == 'api_key':
        if not isinstance(value, str) or len(value) > 512 or any(char.isspace() for char in value):
            raise CoreError('invalid_request')
        if not value and db.get_setting('web_sms_enabled', '0') == '1':
            raise CoreError('sms_enabled')
    elif name in ('enabled', 'registration_required') and type(value) is bool:
        if value and not db.get_setting('web_sms_api_key', ''):
            raise CoreError('sms_not_configured')
        if name == 'registration_required' and value and db.get_setting('web_sms_enabled', '0') != '1':
            raise CoreError('sms_not_configured')
        value = '1' if value else '0'
    else:
        raise CoreError('invalid_request')
    db.set_setting('web_sms_' + name, value)


def set_module_enabled(module_id, enabled):
    from core.extensions.registry import MODULES, MANIFESTS
    from runtime.readiness import require_active
    require_active()
    if type(enabled) is not bool or module_id not in set(MODULES) | set(MANIFESTS):
        raise CoreError('module_not_found')
    db.set_setting('core_module_enabled.' + module_id, '1' if enabled else '0')
