"""Registry of declarative bot commands for custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Awaitable

from database.db_extensions import normalize_extension_id

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
_RESERVED_CORE_COMMANDS = {
    'start',
    'id',
    'help',
    'support',
    'buy',
    'mykeys',
    'my_keys',
}

ExtensionCommandHandler = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]


@dataclass(frozen=True)
class ExtensionCommandDefinition:
    """A public command registered by one extension."""

    extension_id: str
    command: str
    description: str
    action_key: str


EXTENSION_COMMAND_HANDLERS: dict[str, ExtensionCommandHandler] = {}
EXTENSION_COMMAND_DEFINITIONS: dict[str, ExtensionCommandDefinition] = {}


def register_extension_command_handler(
    extension_id: str,
    command: str,
    description: str,
    handler: ExtensionCommandHandler,
    *,
    replace: bool = False,
) -> str:
    """Registers an extension-owned Telegram command handler."""
    ext_id = normalize_extension_id(extension_id)
    command_key = normalize_extension_command(command)
    if command_key in _RESERVED_CORE_COMMANDS:
        raise ValueError(f"extension command '/{command_key}' is reserved by the core")
    command_description = normalize_extension_command_description(description)
    if not isinstance(replace, bool):
        raise ValueError('replace must be bool')
    if not callable(handler):
        raise ValueError('command handler must be callable')

    existing = EXTENSION_COMMAND_DEFINITIONS.get(command_key)
    if existing is not None:
        if existing.extension_id != ext_id:
            raise ValueError(f"extension command '/{command_key}' is already registered by another extension")
        if not replace:
            raise ValueError(f"extension command '/{command_key}' is already registered")

    action_key = f'{ext_id}.{command_key}'
    EXTENSION_COMMAND_DEFINITIONS[command_key] = ExtensionCommandDefinition(
        extension_id=ext_id,
        command=command_key,
        description=command_description,
        action_key=action_key,
    )
    EXTENSION_COMMAND_HANDLERS[action_key] = handler
    return action_key


def remove_extension_command_handlers(extension_id: str, action_keys: set[str]) -> None:
    """Removes runtime command handlers for a specific extension."""
    ext_id = normalize_extension_id(extension_id)
    for action_key in set(action_keys):
        normalized_key = str(action_key or '').strip().casefold()
        if not normalized_key.startswith(f'{ext_id}.'):
            continue
        command = normalized_key.split('.', 1)[1]
        definition = EXTENSION_COMMAND_DEFINITIONS.get(command)
        if definition and definition.action_key == normalized_key:
            EXTENSION_COMMAND_DEFINITIONS.pop(command, None)
        EXTENSION_COMMAND_HANDLERS.pop(normalized_key, None)


def parse_extension_command(message_text: Any, *, bot_username: str | None = None) -> dict[str, str] | None:
    """Parses a Telegram command message if it can belong to an extension."""
    if not isinstance(message_text, str):
        return None
    text = message_text.strip()
    if not text.startswith('/'):
        return None

    token, _, argument = text.partition(' ')
    command_part = token[1:]
    if not command_part:
        return None
    command_name, separator, mentioned_bot = command_part.partition('@')
    if separator and bot_username:
        if mentioned_bot.casefold() != str(bot_username).strip().casefold():
            return None
    try:
        command = normalize_extension_command(command_name)
    except ValueError:
        return None
    return {
        'command': command,
        'argument': argument.strip(),
        'message_text': text,
    }


def get_extension_command_definition(command: str) -> ExtensionCommandDefinition | None:
    """Returns one registered command definition, if present."""
    try:
        command_key = normalize_extension_command(command)
    except ValueError:
        return None
    return EXTENSION_COMMAND_DEFINITIONS.get(command_key)


def get_extension_command_definitions() -> list[ExtensionCommandDefinition]:
    """Returns registered extension commands sorted for a stable Bot API menu."""
    return [
        EXTENSION_COMMAND_DEFINITIONS[key]
        for key in sorted(EXTENSION_COMMAND_DEFINITIONS)
    ]


def is_registered_extension_command(command: str) -> bool:
    """Returns whether a command is currently handled by an extension."""
    return get_extension_command_definition(command) is not None


async def dispatch_extension_command(context: Mapping[str, Any], *, bot: Any = None) -> dict[str, Any]:
    """Calls a registered extension command and normalizes the declarative result."""
    if not isinstance(context, Mapping):
        raise ValueError('context must be a mapping')
    try:
        command = normalize_extension_command(context.get('command'))
    except ValueError:
        return {
            'answer_text': '⚠️ Команда расширения недоступна',
        }
    definition = EXTENSION_COMMAND_DEFINITIONS.get(command)
    if definition is None:
        return {
            'answer_text': '⚠️ Команда расширения недоступна',
        }
    handler = EXTENSION_COMMAND_HANDLERS.get(definition.action_key)
    if handler is None:
        return {
            'answer_text': '⚠️ Команда расширения недоступна',
        }

    try:
        handler_context = {
            'extension_id': definition.extension_id,
            'telegram_id': context.get('telegram_id'),
            'command': definition.command,
            'argument': context.get('argument', ''),
            'message_text': context.get('message_text', ''),
        }
        if 'bot_username' in context:
            handler_context['bot_username'] = context['bot_username']

        from bot.utils.custom_extensions import _extension_bot_context

        with _extension_bot_context(bot):
            raw_result = handler(handler_context)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
        return normalize_extension_command_result(raw_result)
    except Exception as exc:
        logger.exception("Extension command '/%s' failed: %s", command, exc)
        return {
            'answer_text': '⚠️ Команда расширения временно недоступна',
        }


def normalize_extension_command_result(raw_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validates an extension command result."""
    if raw_result is None:
        return {}
    if not isinstance(raw_result, Mapping):
        raise ValueError('extension command must return dict or None')
    allowed = {'answer_text', 'page_key', 'route_key', 'context'}
    result = dict(raw_result)
    unknown = set(result.keys()) - allowed
    if unknown:
        raise ValueError(f"unsupported command result fields: {', '.join(sorted(unknown))}")
    if 'answer_text' in result and result['answer_text'] is not None and not isinstance(result['answer_text'], str):
        raise ValueError('answer_text must be a string')
    for field in ('page_key', 'route_key'):
        if field in result and result[field] is not None and not isinstance(result[field], str):
            raise ValueError(f'{field} must be a string')
    if result.get('page_key') and result.get('route_key'):
        raise ValueError('command result cannot include both page_key and route_key')
    if 'context' in result:
        if result['context'] is None:
            result['context'] = {}
        if not isinstance(result['context'], Mapping):
            raise ValueError('context must be a mapping')
        result['context'] = dict(result['context'])
    return result


def normalize_extension_command(command: Any) -> str:
    """Normalizes a Telegram command name without the leading slash."""
    if not isinstance(command, str):
        raise ValueError('command must be a string')
    value = command.strip().removeprefix('/').casefold()
    if not _COMMAND_RE.fullmatch(value):
        raise ValueError("command must match ^[a-z][a-z0-9_]{0,31}$")
    return value


def normalize_extension_command_description(description: Any) -> str:
    """Normalizes a BotCommand description."""
    if not isinstance(description, str):
        raise ValueError('command description must be a string')
    value = description.strip()
    if not value:
        raise ValueError('command description must not be empty')
    if len(value) > 256:
        raise ValueError('command description must be <= 256 characters')
    return value


__all__ = [
    'EXTENSION_COMMAND_DEFINITIONS',
    'EXTENSION_COMMAND_HANDLERS',
    'ExtensionCommandDefinition',
    'dispatch_extension_command',
    'get_extension_command_definition',
    'get_extension_command_definitions',
    'is_registered_extension_command',
    'normalize_extension_command',
    'normalize_extension_command_description',
    'normalize_extension_command_result',
    'parse_extension_command',
    'register_extension_command_handler',
    'remove_extension_command_handlers',
]
