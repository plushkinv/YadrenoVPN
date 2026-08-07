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
    "🔒 <b>Перед следующим обновлением включите режим «Подписка»</b>\n\n"
    "Установленная версия — <b>последняя, которая поддерживает выдачу "
    "отдельных VPN-ключей</b>. Начиная со следующей версии бот работает "
    "только в режиме подписок.\n\n"
    "<b>Почему мы переходим на подписки:</b>\n"
    "• пользователь получает одну постоянную ссылку для подключения;\n"
    "• вы можете менять доступные протоколы и конфигурацию на сервере;\n"
    "• изменения попадут в VPN-клиент автоматически — пользователю не "
    "придётся получать и добавлять новые ключи.\n\n"
    "Если вам важно выдавать доступ к каждому серверу отдельно, создавайте "
    "для каждого сервера отдельную подписку. Пользователь по-прежнему сможет "
    "выбирать нужный вариант подключения, но его конфигурация останется "
    "управляемой через subscription-ссылку.\n\n"
    "<b>Что необходимо сделать:</b>\n"
    "Откройте <b>Настройки бота</b>, переключите <b>Режим работы</b> на "
    "<b>📡 Подписка</b>, затем снова откройте раздел <b>Обновления</b>. "
    "Бот проверит условие автоматически и разрешит установку следующих "
    "версий.\n\n"
    "Аварийные способы обновления обходят эту проверку и предназначены "
    "только для восстановления работоспособности бота."
)


def check_unblock_conditions() -> bool:
    """Unlock later releases only after Subscription mode is selected."""
    from database.requests import get_setting

    return get_setting("bot_mode", "subscription") == "subscription"
