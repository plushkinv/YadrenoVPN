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

REQUIRED_SCHEMA_VERSION = 84

BLOCKING_MESSAGE = (
    "🔒 <b>Обновление 10.1 проверяет обязательную миграцию</b>\n\n"
    "Специальных действий не требуется. При запуске бот автоматически "
    "применяет миграции и проверяет версию базы данных.\n\n"
    "Следующие обновления продолжатся автоматически после успешного "
    f"обновления базы данных до v{REQUIRED_SCHEMA_VERSION}.\n\n"
    "Если это сообщение появляется повторно, миграция не завершилась или "
    "версия базы данных не прошла проверку. Подробности доступны в журнале "
    "запуска бота."
)


def check_unblock_conditions() -> bool:
    """Unlock further updates only after the v84 migration fully completes."""
    from database.migrations import LATEST_VERSION, get_current_version

    return (
        LATEST_VERSION == REQUIRED_SCHEMA_VERSION
        and get_current_version() == REQUIRED_SCHEMA_VERSION
    )
