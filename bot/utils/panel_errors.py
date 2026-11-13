"""Plain-text panel authentication diagnostics for administrator callbacks."""

from localization import text as _tr
from bot.services.panels.base import PanelErrorKind, PanelRequestError, VPNAPIError


def admin_panel_error(error: VPNAPIError, server: dict | None = None) -> str:
    """Keep typed technical details in logs, not in authentication alerts."""
    if isinstance(error, PanelRequestError) and error.kind in {
        PanelErrorKind.UNAUTHORIZED, PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
    }:
        server = server or {}
        has_credentials = bool(
            str(server.get('login') or '').strip()
            and str(server.get('password') or '').strip()
        )
        if not server.get('api_token') and not has_credentials:
            return (
                _tr('panel_errors.admin_panel_error.1')
            )
        if error.kind == PanelErrorKind.UNAUTHORIZED:
            return (
                _tr('panel_errors.admin_panel_error.2')
            )
        return (
            _tr('panel_errors.admin_panel_error.3')
        )
    return _tr('system.do_clear_logs.2', p0=f'{error}')
