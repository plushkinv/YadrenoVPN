"""
Blocking update.

This file comes with the blocking update.
Once the conditions are met and the next normal update occurs, the file will be overwritten
with a standard plug - and the lock will be removed automatically.

=== INSTRUCTIONS FOR DEVELOPER ===

When creating a blocking update:

1. The commit must begin with '!' is a blocking commit marker.

2. In this file, define two variables:

   BLOCKING_MESSAGE (str) — the text of the message that the administrator will see.
   If not specified, the default text is shown.

   check_unblock_conditions() - function called every time updates are checked.
   Should return True if the conditions are met and the lock can be removed.
   If not defined, the lock is NOT removed automatically.

3. When installing a blocking update, the system automatically:
   - Sets the update_blocked flag in settings
   - Calls check_unblock_conditions() on each check
   - If the function returns True, the flag is removed

=== EXAMPLE ===

BLOCKING_MESSAGE = (
    "🔒 <b>Action required!</b>\\n\\n"
    "Go to the Referral system section and set up levels.\\n"
    "After this, updates will continue automatically."
)

def check_unblock_conditions():
    from database.requests import get_setting
    return get_setting('referral_enabled', '0') == '1'
"""

from bot.utils.panel_version import (
    MINIMUM_SUPPORTED_3X_UI_VERSION,
    panel_version_at_least,
)


BLOCKING_MESSAGE = (
    "🔒 <b>Перед следующим обновлением обновите панели 3X-UI</b>\n\n"
    "Следующие версии бота поддерживают только официальную <b>3X-UI 3.3.0 "
    "или новее</b>. Обновления приостановлены, пока совместимая версия не "
    "будет подтверждена для каждого добавленного сервера.\n\n"
    "<b>Что необходимо сделать:</b>\n"
    "1. Обновите все добавленные панели до <b>3X-UI 3.3.0+</b>.\n"
    "2. Откройте в боте раздел <b>Серверы</b> и дождитесь успешной проверки "
    "подключения, чтобы бот сохранил актуальную версию каждой панели.\n"
    "3. Снова откройте раздел <b>Обновления</b>. Блокировка снимется "
    "автоматически.\n\n"
    "Сервер с версией ниже 3.3.0 или с неопределённой версией продолжит "
    "блокировать обновление. Если сервер больше не используется, удалите его "
    "из бота.\n\n"
    "Текущая версия бота продолжит работать без изменений. Аварийные способы "
    "обновления предназначены только для восстановления, а не для обхода "
    "этого требования."
)


def check_unblock_conditions() -> bool:
    """Unlock later releases only when every saved panel is 3X-UI 3.3.0+."""
    from database.requests import get_all_servers

    return all(
        panel_version_at_least(
            server.get("panel_version"),
            MINIMUM_SUPPORTED_3X_UI_VERSION,
        )
        for server in get_all_servers()
    )
