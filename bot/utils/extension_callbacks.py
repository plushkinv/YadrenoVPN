"""Registry декларативных callback-actions для custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any, Awaitable

from bot.utils.action_registry import normalize_callback_data
from database.db_extensions import normalize_extension_id

logger = logging.getLogger(__name__)

EXT_CALLBACK_PREFIX = 'ext:'
_ACTION_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
_PAYLOAD_RE = re.compile(r'^[a-zA-Z0-9_.:-]{0,32}$')

ExtensionCallbackHandler = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]

EXTENSION_CALLBACK_HANDLERS: dict[str, ExtensionCallbackHandler] = {}


def register_extension_callback_handler(
    extension_id: str,
    action_name: str,
    handler: ExtensionCallbackHandler,
    *,
    replace: bool = False,
) -> str:
    """Регистрирует callback handler расширения и возвращает action key."""
    ext_id = normalize_extension_id(extension_id)
    action = normalize_extension_action_name(action_name)
    if not isinstance(replace, bool):
        raise ValueError('replace должен быть bool')
    if not callable(handler):
        raise ValueError('callback handler должен быть callable')
    key = f'{ext_id}.{action}'
    if key in EXTENSION_CALLBACK_HANDLERS and not replace:
        raise ValueError(f"extension callback '{key}' уже зарегистрирован")
    EXTENSION_CALLBACK_HANDLERS[key] = handler
    return key


def remove_extension_callback_handlers(extension_id: str, action_keys: set[str]) -> None:
    """Удаляет runtime callback handlers конкретного расширения."""
    ext_id = normalize_extension_id(extension_id)
    for key in set(action_keys):
        if key.startswith(f'{ext_id}.'):
            EXTENSION_CALLBACK_HANDLERS.pop(key, None)


def build_extension_callback_data(
    extension_id: str,
    action_name: str,
    payload: str | None = None,
) -> str:
    """Собирает callback_data вида `ext:<extension>.<action>[:payload]`."""
    ext_id = normalize_extension_id(extension_id)
    action = normalize_extension_action_name(action_name)
    if payload is None:
        callback_data = f'{EXT_CALLBACK_PREFIX}{ext_id}.{action}'
    else:
        payload_value = normalize_extension_callback_payload(payload)
        callback_data = f'{EXT_CALLBACK_PREFIX}{ext_id}.{action}:{payload_value}'
    return normalize_callback_data(callback_data, 'extension callback_data')


def parse_extension_callback_data(callback_data: Any) -> dict[str, str] | None:
    """Разбирает callback_data расширения."""
    if not isinstance(callback_data, str) or not callback_data.startswith(EXT_CALLBACK_PREFIX):
        return None
    body = callback_data[len(EXT_CALLBACK_PREFIX):]
    if ':' in body:
        action_part, payload = body.split(':', 1)
    else:
        action_part, payload = body, ''
    if '.' not in action_part:
        return None
    extension_id, action_name = action_part.split('.', 1)
    try:
        ext_id = normalize_extension_id(extension_id)
        action = normalize_extension_action_name(action_name)
        payload = normalize_extension_callback_payload(payload)
    except ValueError:
        return None
    expected = build_extension_callback_data(ext_id, action, payload if payload else None)
    if expected != callback_data:
        return None
    return {
        'extension_id': ext_id,
        'action_name': action,
        'action_key': f'{ext_id}.{action}',
        'payload': payload,
        'callback_data': callback_data,
    }


async def dispatch_extension_callback(context: Mapping[str, Any]) -> dict[str, Any]:
    """Вызывает зарегистрированный extension callback и нормализует результат."""
    if not isinstance(context, Mapping):
        raise ValueError('context должен быть mapping')
    action_key = str(context.get('action_key') or '')
    handler = EXTENSION_CALLBACK_HANDLERS.get(action_key)
    if handler is None:
        return {
            'answer_text': '⚠️ Действие расширения недоступно',
            'show_alert': True,
        }
    try:
        handler_context = {
            key: context[key]
            for key in ('extension_id', 'telegram_id', 'action_name', 'payload', 'callback_data')
            if key in context
        }
        raw_result = handler(handler_context)
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        return normalize_extension_callback_result(raw_result)
    except Exception as exc:
        logger.exception("Ошибка extension callback '%s': %s", action_key, exc)
        return {
            'answer_text': '⚠️ Действие расширения временно недоступно',
            'show_alert': True,
        }


def normalize_extension_callback_result(raw_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Проверяет декларативный результат callback handler-а."""
    if raw_result is None:
        return {}
    if not isinstance(raw_result, Mapping):
        raise ValueError('extension callback должен вернуть dict или None')
    allowed = {'answer_text', 'show_alert', 'page_key', 'route_key', 'context'}
    result = dict(raw_result)
    unknown = set(result.keys()) - allowed
    if unknown:
        raise ValueError(f"неподдерживаемые поля callback result: {', '.join(sorted(unknown))}")
    if 'answer_text' in result and result['answer_text'] is not None and not isinstance(result['answer_text'], str):
        raise ValueError('answer_text должен быть строкой')
    if 'show_alert' in result and not isinstance(result['show_alert'], bool):
        raise ValueError('show_alert должен быть bool')
    for field in ('page_key', 'route_key'):
        if field in result and result[field] is not None and not isinstance(result[field], str):
            raise ValueError(f'{field} должен быть строкой')
    if result.get('page_key') and result.get('route_key'):
        raise ValueError('нельзя одновременно вернуть page_key и route_key')
    if 'context' in result:
        if result['context'] is None:
            result['context'] = {}
        if not isinstance(result['context'], Mapping):
            raise ValueError('context должен быть mapping')
        result['context'] = dict(result['context'])
    return result


def normalize_extension_action_name(action_name: Any) -> str:
    if not isinstance(action_name, str):
        raise ValueError('action_name должен быть строкой')
    value = action_name.strip().casefold()
    if not _ACTION_NAME_RE.fullmatch(value):
        raise ValueError("action_name должен соответствовать ^[a-z][a-z0-9_]{0,31}$")
    return value


def normalize_extension_callback_payload(payload: Any) -> str:
    if not isinstance(payload, str):
        raise ValueError('payload должен быть строкой')
    value = payload.strip()
    if not _PAYLOAD_RE.fullmatch(value):
        raise ValueError('payload содержит недопустимые символы')
    return value


__all__ = [
    'EXT_CALLBACK_PREFIX',
    'EXTENSION_CALLBACK_HANDLERS',
    'build_extension_callback_data',
    'dispatch_extension_callback',
    'normalize_extension_action_name',
    'normalize_extension_callback_payload',
    'normalize_extension_callback_result',
    'parse_extension_callback_data',
    'register_extension_callback_handler',
    'remove_extension_callback_handlers',
]
