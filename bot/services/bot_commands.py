"""Telegram bot command menu synchronization."""
from __future__ import annotations

import logging
from typing import Any

from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

MAX_BOT_COMMANDS = 100

CORE_BOT_COMMANDS = (
    BotCommand(command='start', description='Главное меню'),
    BotCommand(command='id', description='Ваш Telegram ID'),
    BotCommand(command='help', description='Инструкция'),
    BotCommand(command='support', description='Поддержка'),
    BotCommand(command='buy', description='Купить ключ'),
    BotCommand(command='mykeys', description='Мои ключи'),
)


def build_bot_commands() -> list[BotCommand]:
    """Builds the Bot API command list from core and extension commands."""
    from bot.utils.extension_commands import get_extension_command_definitions

    commands = list(CORE_BOT_COMMANDS)
    used = {command.command for command in commands}
    for definition in get_extension_command_definitions():
        if definition.command in used:
            continue
        commands.append(BotCommand(command=definition.command, description=definition.description))
        used.add(definition.command)
        if len(commands) >= MAX_BOT_COMMANDS:
            logger.warning(
                "Telegram command menu reached %s commands; extra extension commands are skipped",
                MAX_BOT_COMMANDS,
            )
            break
    return commands


async def sync_bot_commands(bot: Any) -> bool:
    """Sends the current command menu to Telegram."""
    if bot is None or not callable(getattr(bot, 'set_my_commands', None)):
        raise ValueError('bot must support set_my_commands')
    commands = build_bot_commands()
    await bot.set_my_commands(commands)
    logger.info("Telegram command menu synchronized: %s commands", len(commands))
    return True


__all__ = [
    'CORE_BOT_COMMANDS',
    'MAX_BOT_COMMANDS',
    'build_bot_commands',
    'sync_bot_commands',
]
