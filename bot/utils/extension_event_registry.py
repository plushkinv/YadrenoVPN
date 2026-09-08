"""Runtime subscriptions to the closed catalog of committed core events."""
from __future__ import annotations

from typing import Any, Callable

from bot.utils.action_origin_context import normalize_completion_handler_name
from bot.utils.extension_background import invoke_background_handler
from database.db_extensions import normalize_extension_id

CORE_EVENT_NAMES = ('payment.completed', 'trial.activated', 'key.delivered', 'key.expired', 'user.registered')
EXTENSION_EVENT_HANDLERS: dict[str, dict[str, Any]] = {}


def register_extension_event_handler(
    extension_id: str, name: str, *, events, handler: Callable, replace: bool = False,
) -> str:
    """Register an owner-local name and its explicitly selected event names."""
    extension_id = normalize_extension_id(extension_id)
    name = normalize_completion_handler_name(name)
    if not isinstance(events, (list, tuple, set, frozenset)) or not events:
        raise ValueError('events must be a non-empty collection')
    if any(not isinstance(event, str) or event not in CORE_EVENT_NAMES for event in events):
        raise ValueError('unsupported core event')
    if len(events) != len(set(events)):
        raise ValueError('duplicate event names')
    if not callable(handler) or not isinstance(replace, bool):
        raise ValueError('handler must be callable and replace must be bool')
    key = f'{extension_id}.{name}'
    if key in EXTENSION_EVENT_HANDLERS and not replace:
        raise ValueError('event handler is already registered')
    EXTENSION_EVENT_HANDLERS[key] = {
        'extension_id': extension_id, 'handler_name': name,
        'events': tuple(sorted(events)), 'handler': handler,
    }
    return key


def event_subscribers(event_name: str) -> list[dict[str, str]]:
    """Capture recipients without invoking extension code."""
    return [
        {'extension_id': item['extension_id'], 'handler_name': item['handler_name']}
        for item in list(EXTENSION_EVENT_HANDLERS.values()) if event_name in item['events']
    ]


async def dispatch_extension_event(job: dict[str, Any], *, bot: Any = None) -> dict[str, Any]:
    key = f"{job['extension_id']}.{job['handler_name']}"
    registration = EXTENSION_EVENT_HANDLERS.get(key)
    if registration is None or job['event_name'] not in registration['events']:
        raise LookupError('event handler is unavailable')
    context = dict(job['payload'])
    context['delivery_attempt'] = int(job['attempts'])
    return await invoke_background_handler(registration['handler'], context, bot=bot)
