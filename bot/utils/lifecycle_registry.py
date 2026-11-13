"""Lifecycle hooks for custom extensions."""
from __future__ import annotations

from localization import text as _tr

import inspect
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

_HOOK_NAME_RE = re.compile(r'^[a-z][a-z0-9_.:-]{0,127}$')
KEY_LIFECYCLE_EVENTS = {
    'key_created',
    'key_configured',
    'key_renewed',
    'key_replaced',
    'key_expired',
}
_ALLOWED_RESULT_KEYS = {'ok', 'label', 'reason', 'metadata'}

KeyLifecycleHook = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]

KEY_LIFECYCLE_HOOKS: dict[str, dict[str, Any]] = {}


def register_key_lifecycle_hook(
    name: str,
    func: KeyLifecycleHook,
    *,
    events: list[str] | tuple[str, ...] | set[str] | None = None,
    replace: bool = False,
) -> None:
    """Registers a key lifecycle hook."""
    hook_name = _normalize_hook_name(name)
    _require_bool_option(replace, 'replace')
    if not callable(func):
        raise ValueError(_tr('lifecycle_registry.register_key_lifecycle_hook.1'))
    if hook_name in KEY_LIFECYCLE_HOOKS and not replace:
        raise ValueError(_tr('lifecycle_registry.register_key_lifecycle_hook.2', p0=f'{hook_name}'))

    event_set = _normalize_events(events)
    KEY_LIFECYCLE_HOOKS[hook_name] = {
        'func': func,
        'events': event_set,
    }


async def emit_key_lifecycle_event(event: str, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Calls registered hooks on the key event without blocking core-flow with errors."""
    event_name = _normalize_event(event)
    base_context = _normalize_context(context)
    results: list[dict[str, Any]] = []

    for name, hook in list(KEY_LIFECYCLE_HOOKS.items()):
        if event_name not in hook['events']:
            continue

        hook_context = dict(base_context)
        hook_context['event'] = event_name
        try:
            raw_result = hook['func'](hook_context)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            result = _normalize_hook_result(raw_result)
        except Exception as e:
            logger.warning("Key lifecycle hook '%s' пропущен для %s: %s", name, event_name, e)
            results.append({'name': name, 'ok': False, 'reason': str(e)})
            continue

        if not result:
            continue

        entry = {'name': name, 'ok': result.get('ok', True)}
        for key in ('label', 'reason', 'metadata'):
            if key in result:
                entry[key] = result[key]
        results.append(entry)

    from core.extensions.events import emit_lifecycle
    results.extend(await emit_lifecycle(event_name, base_context))
    return results


def _normalize_hook_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError(_tr('lifecycle_registry.normalize_hook_name.1'))
    value = name.strip().casefold()
    if not _HOOK_NAME_RE.fullmatch(value):
        raise ValueError(_tr('lifecycle_registry.normalize_hook_name.2'))
    return value


def _normalize_event(event: str) -> str:
    if not isinstance(event, str):
        raise ValueError(_tr('lifecycle_registry.normalize_event.1'))
    value = event.strip().casefold()
    if value not in KEY_LIFECYCLE_EVENTS:
        raise ValueError(_tr('lifecycle_registry.normalize_event.2', p0=f'{event}'))
    return value


def _normalize_events(events: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    if events is None:
        return set(KEY_LIFECYCLE_EVENTS)
    if not isinstance(events, (list, tuple, set)):
        raise ValueError(_tr('lifecycle_registry.normalize_events.1'))
    normalized = {_normalize_event(event) for event in events}
    if not normalized:
        raise ValueError(_tr('lifecycle_registry.normalize_events.2'))
    return normalized


def _normalize_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError(_tr('action_registry.resolve_system_button.1'))
    return dict(context)


def _require_bool_option(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(_tr('action_registry.require_bool.1', p0=f'{field}'))
    return value


def _normalize_hook_result(raw_result: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw_result is None:
        return {}
    if not isinstance(raw_result, Mapping):
        raise ValueError(_tr('lifecycle_registry.normalize_hook_result.1'))
    result = dict(raw_result)
    unknown = set(result.keys()) - _ALLOWED_RESULT_KEYS
    if unknown:
        raise ValueError(_tr('lifecycle_registry.normalize_hook_result.2', p0=f"{', '.join(sorted(unknown))}"))
    if 'ok' in result and not isinstance(result['ok'], bool):
        raise ValueError(_tr('lifecycle_registry.normalize_hook_result.3'))
    for field in ('label', 'reason'):
        if field in result and result[field] is not None and not isinstance(result[field], str):
            raise ValueError(_tr('action_registry.require_text.1', p0=f'{field}'))
        if field in result and result[field] is None:
            result.pop(field)
    if 'metadata' in result and not isinstance(result['metadata'], Mapping):
        raise ValueError(_tr('lifecycle_registry.normalize_hook_result.4'))
    return result


__all__ = [
    'KEY_LIFECYCLE_EVENTS',
    'KEY_LIFECYCLE_HOOKS',
    'emit_key_lifecycle_event',
    'register_key_lifecycle_hook',
]
