"""User-facing Yadreno Admin error messages."""
from __future__ import annotations

from bot.services.yadreno_admin import YadrenoAdminError
from bot.utils.telegram_links import build_telegram_link
from bot.utils.text import escape_html


def _is_api_key_error(error: YadrenoAdminError) -> bool:
    """Return True when the hub rejected the configured API key."""
    technical_message = str(error).casefold()
    return error.status_code == 401 or "invalid api_key" in technical_message


def yadreno_admin_error_alert(error: YadrenoAdminError) -> str:
    """Build a short plain-text error for a Telegram callback alert."""
    if _is_api_key_error(error):
        return (
            "Текущий ключ Yadreno Admin больше не подходит. "
            "Если вы меняли сервер, выпустите новый ключ в @YadrenoAdmin_Bot."
        )
    if error.user_message:
        return error.user_message[:180]
    return "Не удалось связаться с Yadreno Admin. Попробуйте ещё раз чуть позже."


def format_yadreno_admin_error(
    error: YadrenoAdminError,
    *,
    title: str = "Не удалось подключиться к Yadreno Admin",
) -> str:
    """Build a safe, actionable Telegram HTML error without hub internals."""
    if _is_api_key_error(error):
        bot_link = build_telegram_link("YadrenoAdmin_Bot")
        body = (
            "Текущий ключ доступа больше не подходит.\n\n"
            "Возможно, вы меняли сервер. Выпустите новый ключ в "
            f'<a href="{bot_link}">@YadrenoAdmin_Bot</a>, затем замените его '
            "в настройках Yadreno Admin."
        )
    elif error.user_message:
        body = escape_html(error.user_message)
    else:
        body = (
            "Сервис временно не отвечает. Проверьте подключение к интернету "
            "и попробуйте ещё раз чуть позже."
        )

    return f"❌ <b>{escape_html(title)}</b>\n\n{body}"
