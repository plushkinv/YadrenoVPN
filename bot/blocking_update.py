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

BLOCKING_MESSAGE = (
    "🔒 <b>Обновление 9.7 применяет обязательную миграцию</b>\n\n"
    "Бот уже обновил код и при запуске применяет миграции базы данных. "
    "После успешного обновления БД до v73 блокировка следующих обновлений "
    "снимется автоматически.\n\n"
    "Если это сообщение повторяется после перезапуска, откройте настройки бота "
    "и проверьте обновления ещё раз."
)


def check_unblock_conditions():
    """Allow further updates after the blocking migration has been applied."""
    from database.migrations import get_current_version

    return get_current_version() >= 73
