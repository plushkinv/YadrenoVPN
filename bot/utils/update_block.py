"""
Update blocking management.

When a blocking update is installed - regular updates
and auto-check are disabled until the unlock conditions are met.

Settings in settings:
- update_blocked: '1' or '0' - blocking flag

The message text is taken from bot.blocking_update.BLOCKING_MESSAGE,
if not specified, DEFAULT_BLOCKED_MESSAGE is used.
"""
import logging
import importlib

from database.requests import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_BLOCKED_MESSAGE = (
    "🔒 <b>Обновления приостановлены</b>\n\n"
    "Для продолжения автоматических обновлений "
    "необходимо выполнить определённые действия в боте.\n\n"
    "Доступные режимы обновления:\n"
    "• Команда /update — экстренное обновление\n"
    "• Принудительная перезапись в настройках\n\n"
    "После выполнения требуемых действий блокировка снимется автоматически."
)


def is_update_blocked() -> bool:
    return get_setting('update_blocked', '0') == '1'


def get_blocked_message() -> str:
    try:
        mod = importlib.import_module('bot.blocking_update')
        custom = getattr(mod, 'BLOCKING_MESSAGE', '')
        if custom:
            return custom
    except Exception:
        pass
    return DEFAULT_BLOCKED_MESSAGE


def set_update_blocked() -> None:
    set_setting('update_blocked', '1')
    logger.info("Блокировка обновлений установлена")


def clear_update_blocked() -> None:
    set_setting('update_blocked', '0')
    logger.info("Блокировка обновлений снята")


def try_unblock() -> bool:
    """
    Checks unblocking conditions via bot.blocking_update.

    Imports a module, checks for the presence of check_unblock_conditions().
    If the function exists and returns True, it releases the lock.

    Returns:
        True if the lock has been removed
    """
    if not is_update_blocked():
        return False

    try:
        mod = importlib.import_module('bot.blocking_update')
    except Exception as e:
        logger.debug(f"Модуль blocking_update не найден: {e}")
        return False

    check_fn = getattr(mod, 'check_unblock_conditions', None)
    if check_fn is None:
        logger.debug("Функция check_unblock_conditions не определена")
        return False

    try:
        result = check_fn()
        if result:
            logger.info("Условия разблокировки выполнены, снимаем блокировку")
            clear_update_blocked()
            return True
        else:
            logger.debug("Условия разблокировки НЕ выполнены")
            return False
    except Exception as e:
        logger.error(f"Ошибка в check_unblock_conditions: {e}")
        return False
