"""User-facing Yadreno Admin error messages."""
from __future__ import annotations

from localization import text as _tr

from bot.services.yadreno_admin import YadrenoAdminError
from bot.utils.telegram_links import build_telegram_link
from bot.utils.text import escape_html


def _is_api_key_error(error: YadrenoAdminError) -> bool:
    """Return True when the hub rejected the configured API key."""
    technical_message = str(error).casefold()
    return (
        error.kind == "authentication"
        or error.status_code in {401, 403}
        or "invalid api_key" in technical_message
    )


def yadreno_admin_error_alert(error: YadrenoAdminError) -> str:
    """Build a short plain-text error for a Telegram callback alert."""
    if error.kind == "configuration":
        return (
            _tr('yadreno_admin_errors.yadreno_admin_error_alert.1')
        )
    if _is_api_key_error(error):
        return (
            _tr('yadreno_admin_errors.yadreno_admin_error_alert.2')
        )
    if error.user_message:
        return error.user_message[:180]
    return (
        _tr('yadreno_admin_errors.yadreno_admin_error_alert.3')
    )


def format_yadreno_admin_error(
    error: YadrenoAdminError,
    *,
    title: str | None = None,
) -> str:
    """Build a safe, actionable Telegram HTML error without hub internals."""
    if error.kind == "configuration":
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.1')
        icon = "❌"
        body = (
            _tr('yadreno_admin_errors.format_yadreno_admin_error.2')
        )
    elif _is_api_key_error(error):
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.3')
        icon = "❌"
        bot_link = build_telegram_link("YadrenoAdmin_Bot")
        body = (
            _tr('yadreno_admin_errors.format_yadreno_admin_error.4', p0=f'{bot_link}')
        )
    elif error.kind == "maintenance":
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.5')
        icon = "⏳"
        body = escape_html(
            error.user_message
            or _tr('yadreno_admin_errors.format_yadreno_admin_error.6')
        )
    elif error.kind == "service_unavailable":
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.7')
        icon = "⏳"
        body = escape_html(
            error.user_message
            or _tr('yadreno_admin_errors.format_yadreno_admin_error.8')
        )
    elif error.user_message:
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.9')
        icon = "⚠️"
        body = escape_html(error.user_message)
    else:
        default_title = _tr('yadreno_admin_errors.format_yadreno_admin_error.7')
        icon = "⏳"
        body = (
            _tr('yadreno_admin_errors.format_yadreno_admin_error.10')
        )

    return f"{icon} <b>{escape_html(title or default_title)}</b>\n\n{body}"
