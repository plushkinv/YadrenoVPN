"""Namespaced handlers and declarative requests for ordinary Telegram input."""
from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from bot.utils.action_origin_context import normalize_completion_handler_name, normalize_origin_payload
from database.db_extensions import normalize_extension_id

EXTENSION_INPUT_HANDLERS: dict[str, Callable] = {}


def input_handler_key(extension_id: str, name: str) -> str:
    return f'{normalize_extension_id(extension_id)}.{normalize_completion_handler_name(name)}'


def register_extension_input_handler(
    extension_id: str, name: str, handler: Callable, *, replace: bool = False,
) -> str:
    key = input_handler_key(extension_id, name)
    if not callable(handler):
        raise ValueError('input handler must be callable')
    if not isinstance(replace, bool):
        raise ValueError('replace must be bool')
    if key in EXTENSION_INPUT_HANDLERS and not replace:
        raise ValueError(f"extension input handler '{key}' is already registered")
    EXTENSION_INPUT_HANDLERS[key] = handler
    return key


def remove_extension_input_handlers(extension_id: str, keys: set[str]) -> None:
    prefix = normalize_extension_id(extension_id) + '.'
    for key in keys:
        if key.startswith(prefix):
            EXTENSION_INPUT_HANDLERS.pop(key, None)


def normalize_input_request(result: dict[str, Any]) -> None:
    """Normalize the additive request in a command/callback/page result in place."""
    if 'input' not in result:
        return
    raw = result['input']
    if not isinstance(raw, Mapping) or set(raw) - {'handler', 'payload'}:
        raise ValueError('input must contain only handler and optional payload')
    if not (bool(result.get('page_key')) ^ bool(result.get('route_key'))):
        raise ValueError('input requires exactly one page_key or route_key')
    if set(result) & {'target', 'action', 'params', 'origin_context', 'answer_text', 'show_alert'}:
        raise ValueError('input must accompany a stored page/route result only')
    result['input'] = {
        'handler': normalize_completion_handler_name(raw.get('handler')),
        'payload': normalize_origin_payload(raw.get('payload', {})),
    }


def normalize_input_result(raw: Any) -> dict[str, Any]:
    """Input handlers return stored pages, optionally requesting the next step."""
    from bot.utils.extension_commands import normalize_extension_command_result

    if raw is not None and (not isinstance(raw, Mapping) or set(raw) - {
        'page_key', 'route_key', 'context', 'input',
    }):
        raise ValueError('input handler must return a stored page/route result or None')
    return normalize_extension_command_result(raw)


async def dispatch_extension_input(
    extension_id: str, name: str, context: Mapping[str, Any], *, bot: Any = None,
) -> dict[str, Any]:
    """Bind the owner and bot without exposing Telegram objects to the callable."""
    from bot.utils.custom_extensions import _extension_bot_context

    key = input_handler_key(extension_id, name)
    handler = EXTENSION_INPUT_HANDLERS.get(key)
    if handler is None:
        raise LookupError('input handler is unavailable')
    with _extension_bot_context(bot):
        raw = handler(deepcopy(dict(context)))
        if inspect.isawaitable(raw):
            raw = await raw
    if EXTENSION_INPUT_HANDLERS.get(key) is not handler:
        raise LookupError('input handler changed during invocation')
    return normalize_input_result(raw)


def input_capabilities() -> dict[str, Any]:
    """Describe installed support, never pending prompts or submitted values."""
    return {
        'contract_version': 1,
        'registration': 'register_input_handler',
        'result_field': 'input',
        'content_types': ['text'],
        'number_validation': 'handler',
        'storage': 'memory',
        'cancel_on': ['callback_query', 'command'],
        'external_buttons_observable': False,
    }
