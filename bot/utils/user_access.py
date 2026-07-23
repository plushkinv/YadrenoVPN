"""Global user access guard registry for custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable

logger = logging.getLogger(__name__)

_GUARD_NAME_RE = re.compile(r'^[a-z][a-z0-9_.:-]{0,96}$')


@dataclass
class UserAccessGuardResult:
    """Declarative user access guard result."""

    allowed: bool
    message: str = ''
    show_alert: bool = True
    page_key: str = ''
    context: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[dict[str, str]]] = field(default_factory=list)


UserAccessGuard = Callable[
    [Any, Mapping[str, Any]],
    UserAccessGuardResult | bool | Mapping[str, Any] | Awaitable[UserAccessGuardResult | bool | Mapping[str, Any]],
]

USER_ACCESS_GUARDS: dict[str, UserAccessGuard] = {}


def register_user_access_guard(name: str, func: UserAccessGuard, *, replace: bool = False) -> None:
    """Registers a global user access guard."""
    normalized = normalize_user_access_guard_name(name)
    if not isinstance(replace, bool):
        raise ValueError('replace must be bool')
    if not callable(func):
        raise ValueError('user access guard must be callable')
    if normalized in USER_ACCESS_GUARDS and not replace:
        raise ValueError(f"user access guard '{normalized}' is already registered")
    USER_ACCESS_GUARDS[normalized] = func


def remove_user_access_guards(guard_names: set[str]) -> None:
    """Removes registered user access guards by normalized names."""
    for name in set(guard_names):
        USER_ACCESS_GUARDS.pop(normalize_user_access_guard_name(name), None)


async def run_user_access_guards(target: Any, context: Mapping[str, Any]) -> UserAccessGuardResult:
    """Runs all global user access guards in registration order."""
    base_context = _require_context_mapping(context)
    for name, guard in list(USER_ACCESS_GUARDS.items()):
        try:
            result = guard(target, dict(base_context))
            if inspect.isawaitable(result):
                result = await result
            normalized = normalize_user_access_guard_result(result)
        except Exception as exc:
            logger.exception("User access guard '%s' failed: %s", name, exc)
            return UserAccessGuardResult(
                allowed=False,
                message='',
                show_alert=True,
            )
        if not normalized.allowed:
            return normalized
    return UserAccessGuardResult(allowed=True)


def normalize_user_access_guard_name(name: Any) -> str:
    """Normalizes a user access guard registry name."""
    if not isinstance(name, str):
        raise ValueError('user access guard name must be a string')
    value = name.strip().casefold()
    if not _GUARD_NAME_RE.fullmatch(value):
        raise ValueError("user access guard name must match ^[a-z][a-z0-9_.:-]{0,96}$")
    return value


def normalize_user_access_guard_result(raw_result: UserAccessGuardResult | bool | Mapping[str, Any] | None) -> UserAccessGuardResult:
    """Validates a declarative guard result."""
    if raw_result is None:
        return UserAccessGuardResult(allowed=True)
    if isinstance(raw_result, UserAccessGuardResult):
        return UserAccessGuardResult(
            allowed=_require_bool(raw_result.allowed, 'allowed'),
            message=_optional_text(raw_result.message, 'message') or '',
            show_alert=_require_bool(raw_result.show_alert, 'show_alert'),
            page_key=_optional_text(raw_result.page_key, 'page_key') or '',
            context=_require_mapping(raw_result.context, 'context'),
            buttons=_normalize_buttons(raw_result.buttons, 'buttons'),
        )
    if isinstance(raw_result, bool):
        return UserAccessGuardResult(allowed=raw_result)
    if not isinstance(raw_result, Mapping):
        raise ValueError('user access guard must return dict, bool, UserAccessGuardResult or None')

    allowed_keys = {'allowed', 'message', 'show_alert', 'page_key', 'context', 'buttons', 'reply_markup'}
    unknown = set(raw_result.keys()) - allowed_keys
    if unknown:
        raise ValueError(f"unsupported user access guard result fields: {', '.join(sorted(unknown))}")
    if 'allowed' not in raw_result:
        raise ValueError('user access guard mapping must include allowed')
    buttons = raw_result.get('buttons')
    if buttons is None:
        buttons = raw_result.get('reply_markup')
    return UserAccessGuardResult(
        allowed=_require_bool(raw_result.get('allowed'), 'allowed'),
        message=_optional_text(raw_result.get('message'), 'message') or '',
        show_alert=_require_bool(raw_result.get('show_alert', True), 'show_alert'),
        page_key=_optional_text(raw_result.get('page_key'), 'page_key') or '',
        context=_require_mapping(raw_result.get('context'), 'context'),
        buttons=_normalize_buttons(buttons, 'buttons'),
    )


def has_user_access_guards() -> bool:
    """Returns whether global user access guards are registered."""
    return bool(USER_ACCESS_GUARDS)


def _require_context_mapping(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError('context must be a mapping')
    return dict(context)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'user access guard field {field_name} must be bool')
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'user access guard field {field_name} must be a string')
    return value


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f'user access guard field {field_name} must be a mapping')
    return dict(value)


def _normalize_buttons(value: Any, field_name: str) -> list[list[dict[str, str]]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f'user access guard field {field_name} must be button rows')
    rows: list[list[dict[str, str]]] = []
    for row_index, raw_row in enumerate(value):
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)):
            raise ValueError(f'user access guard {field_name} row #{row_index + 1} must be a list')
        row: list[dict[str, str]] = []
        for button_index, raw_button in enumerate(raw_row):
            if not isinstance(raw_button, Mapping):
                raise ValueError(
                    f'user access guard {field_name} row #{row_index + 1} button #{button_index + 1} must be a dict'
                )
            label = _non_empty_button_text(raw_button.get('label') or raw_button.get('text'), 'label')
            callback_data = raw_button.get('callback_data')
            url = raw_button.get('url')
            if bool(callback_data) == bool(url):
                raise ValueError('user access guard button must define exactly one of callback_data or url')
            button: dict[str, str] = {'label': label}
            if callback_data:
                button['callback_data'] = _non_empty_button_text(callback_data, 'callback_data')
            if url:
                button['url'] = _non_empty_button_text(url, 'url')
            row.append(button)
        if row:
            rows.append(row)
    return rows


def _non_empty_button_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'user access guard button {field_name} must be a string')
    text = value.strip()
    if not text:
        raise ValueError(f'user access guard button {field_name} must not be empty')
    return text


__all__ = [
    'USER_ACCESS_GUARDS',
    'UserAccessGuardResult',
    'has_user_access_guards',
    'normalize_user_access_guard_name',
    'normalize_user_access_guard_result',
    'register_user_access_guard',
    'remove_user_access_guards',
    'run_user_access_guards',
]
