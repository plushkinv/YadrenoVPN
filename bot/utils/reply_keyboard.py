"""Optional command keyboard: live configuration and /start delivery."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from bot.utils.extension_commands import get_extension_command_definitions
from database.requests import get_setting

logger = logging.getLogger(__name__)
SETTING_KEY = 'telegram_reply_keyboard'
CORE_COMMANDS = ('/start', '/mykeys', '/help', '/id', '/buy', '/support', '/my_keys')
ADMIN_COMMANDS = frozenset({'/custom_reset', '/update', '/cancel', '/yaa', '/yaan'})
COMMAND_PATTERN = r'^/[a-z][a-z0-9_]{0,31}$'
MAX_LABEL_LENGTH = 64


def available_commands() -> list[str]:
    """Return the installed public command catalog, independent of BotFather menus."""
    extensions = (
        '/' + definition.command for definition in get_extension_command_definitions()
    )
    return [*CORE_COMMANDS, *(command for command in extensions if command not in ADMIN_COMMANDS)]


def parse_config(value: str, *, require_available: bool = False) -> dict[str, Any]:
    """Validate persisted data; runtime retains unavailable extension bindings."""
    try:
        config = json.loads(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('telegram_reply_keyboard must be a JSON object') from exc
    if not isinstance(config, dict) or set(config) - {'message_text', 'rows'}:
        raise ValueError('telegram_reply_keyboard accepts only message_text and rows')
    rows = config.get('rows')
    if not isinstance(rows, list):
        raise ValueError('rows must be an array of button rows')
    message_text = config.get('message_text')
    if rows or message_text is not None:
        if not isinstance(message_text, str) or not message_text.strip() or len(message_text) > 4096:
            raise ValueError('message_text must be a non-blank string of at most 4096 characters')
    allowed = set(available_commands()) if require_available else None
    labels: set[str] = set()
    normalized_rows = []
    for row in rows:
        if not isinstance(row, list) or not row:
            raise ValueError('each button row must be a non-empty array')
        normalized_row = []
        for button in row:
            if not isinstance(button, dict) or set(button) != {'label', 'command'}:
                raise ValueError('each button must contain only label and command')
            label, command = button['label'], button['command']
            if (not isinstance(label, str) or not label.strip()
                    or len(label.strip()) > MAX_LABEL_LENGTH or '\n' in label or '\r' in label):
                raise ValueError('label must be a non-blank single line of at most 64 characters')
            label = label.strip()
            if label in labels:
                raise ValueError('button labels must be unique after trimming')
            if not isinstance(command, str) or re.fullmatch(COMMAND_PATTERN, command) is None:
                raise ValueError('command must be a slash-prefixed command name without arguments')
            if command in ADMIN_COMMANDS:
                raise ValueError('administrative commands cannot be used in the reply keyboard')
            if label.startswith('/') and label != command:
                raise ValueError('a command label must match its bound command')
            if allowed is not None and command not in allowed:
                raise ValueError(f'command is not currently available: {command}')
            labels.add(label)
            normalized_row.append({'label': label, 'command': command})
        normalized_rows.append(normalized_row)
    return {'message_text': message_text, 'rows': normalized_rows}


def active_config() -> dict[str, Any] | None:
    """Read on every use and omit unloaded commands without rewriting storage."""
    value = get_setting(SETTING_KEY)
    if value is None:
        return None
    try:
        config = parse_config(value)
    except ValueError as exc:
        logger.warning('Ignoring invalid reply keyboard setting: %s', exc)
        return None
    allowed = set(available_commands())
    rows = [[button for button in row if button['command'] in allowed] for row in config['rows']]
    config['rows'] = [row for row in rows if row]
    return config if config['rows'] else None


async def send_reply_keyboard(message) -> None:
    """Send the configured keyboard once per /start and retain its message."""
    try:
        config = active_config()
        if config:
            from bot.keyboards.reply_keyboard import build_reply_keyboard
            from bot.utils.text import send_media_or_text

            await send_media_or_text(
                message.bot, chat_id=message.chat.id, text=config['message_text'],
                reply_markup=build_reply_keyboard(config['rows']),
            )
    except Exception:
        logger.warning('Failed to install reply keyboard on /start', exc_info=True)
