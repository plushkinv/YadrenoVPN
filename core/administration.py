"""Private administrator settings; never exposed through the user facade."""


def web_diagnostics(application=None):
    from core.telegram_web_auth import login_settings
    from core.phone_verification_settings import admin_verification_settings
    from core.extensions.registry import inspect_modules
    from runtime.readiness import is_active
    from runtime.application import get_web_setup_error
    from web_api.settings import get_web_settings
    from web_tools.release_build import release_status
    try:
        settings = get_web_settings()
        configured, enabled, origin = bool(settings.public_origin), settings.enabled, settings.public_origin
        error = get_web_setup_error()
    except ValueError:
        configured, enabled, origin, error = False, False, '', 'invalid_web_settings'
    return {'api_version': 1, 'core_active': is_active(), 'configured': configured,
            'enabled': enabled, 'public_origin': origin, 'error': error,
            'listener_running': application.web_server is not None if application else None,
            'verification': admin_verification_settings(),
            'telegram_login': login_settings(),
            'modules': inspect_modules(), 'ui': ui_publication_status(), 'ui_release': release_status()}


def ui_publication_status():
    """Read-only presentation status; no signing material or build operations."""
    from web_tools.compatibility import current_capabilities
    from web_tools.paths import PROJECT_ROOT
    from web_tools.publication import read_pointer
    from web_tools.release import publication
    runtime = PROJECT_ROOT / 'web_runtime'
    try:
        build_id = read_pointer(runtime)['current']
        if not build_id:
            return {'state': 'not_published'}
        signed, _, _ = publication(runtime, build_id, current_capabilities())
        manifest = signed['manifest']
        result = {'state': 'ready', 'build_id': manifest['build_id'], 'customization_version': manifest['customization_version']}
        from runtime.application import get_ui_bootstrap_error
        if get_ui_bootstrap_error():
            result['warning'] = 'ui_bootstrap_failed'
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'unavailable'}
