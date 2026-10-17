"""Plain-text panel authentication diagnostics for administrator callbacks."""
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
                '❌ API-ключ панели не указан. Откройте раздел «Сервера» '
                'и сохраните API-ключ панели.'
            )
        if error.kind == PanelErrorKind.UNAUTHORIZED:
            return (
                '❌ Панель не принимает API-ключ. Повторите попытку позже '
                'или проверьте подключение в разделе «Сервера».'
            )
        return (
            '❌ Не удалось войти в панель. Проверьте подключение '
            'и данные входа в разделе «Сервера».'
        )
    return f'❌ Ошибка: {error}'
