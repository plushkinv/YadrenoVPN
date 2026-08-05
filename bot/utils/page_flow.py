"""Secure registry hooks/guards for page builder routes."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from aiogram import Bot
from aiogram.types import InlineKeyboardButton

logger = logging.getLogger(__name__)


@dataclass
class PageGuardResult:
    """The result of checking access to route/page."""

    allowed: bool
    message: str = ''
    show_alert: bool = True


@dataclass
class PageHookResult:
    """Data that the hook adds to render_page()."""

    context: dict[str, Any] = field(default_factory=dict)
    text_replacements: dict[str, Any] = field(default_factory=dict)
    visibility: dict[str, bool] = field(default_factory=dict)
    prepend_buttons: list[list[InlineKeyboardButton]] | None = None
    append_buttons: list[list[InlineKeyboardButton]] | None = None


PageGuard = Callable[
    [Any, Mapping[str, Any]],
    PageGuardResult | bool | Mapping[str, Any] | Awaitable[PageGuardResult | bool | Mapping[str, Any]],
]
PageHook = Callable[[Any, Mapping[str, Any]], PageHookResult | Mapping[str, Any] | Awaitable[PageHookResult | Mapping[str, Any]]]

PAGE_GUARDS: dict[str, PageGuard] = {}
PAGE_HOOKS: dict[str, PageHook] = {}

PAGE_FLOW_CALL_TIMEOUT_SECONDS = 5.0
PAGE_FLOW_TOTAL_EXTENSION_BUDGET_SECONDS = 10.0
PAGE_FLOW_CIRCUIT_FAILURE_THRESHOLD = 3
PAGE_FLOW_CIRCUIT_COOLDOWN_SECONDS = 300.0
PAGE_HOOK_MAX_MAPPING_ENTRIES = 128
PAGE_HOOK_MAX_PAYLOAD_BYTES = 64 * 1024
PAGE_HOOK_MAX_PAYLOAD_DEPTH = 8
PAGE_HOOK_MAX_PAYLOAD_NODES = 2048
PAGE_HOOK_MAX_BUTTON_ROWS = 25
PAGE_HOOK_MAX_BUTTONS = 50
PAGE_HOOK_MAX_BUTTONS_PER_ROW = 8
PAGE_GUARD_MAX_MESSAGE_LENGTH = 200
_PAGE_FLOW_KINDS = frozenset({'guard', 'hook'})


@dataclass
class PageFlowExecutionFrame:
    """Per-materialization state shared by page and route flow layers."""

    seen_hook_names: set[str] = field(default_factory=set)
    extension_elapsed_seconds: float = 0.0
    extension_hook_result: PageHookResult = field(default_factory=PageHookResult)


@dataclass(frozen=True)
class _ExtensionOwner:
    extension_id: str
    callable: Callable[..., Any]
    generation: int


@dataclass
class _CallableHealth:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    limit_rejections: int = 0
    open_skips: int = 0
    budget_skips: int = 0
    consecutive_failures: int = 0
    opened_until_monotonic: float = 0.0
    half_open_in_flight: bool = False
    last_duration_ms: float | None = None
    last_failure_kind: str = ''
    last_error: str = ''
    last_page: str = ''
    last_scope: str = ''
    last_event_at: str = ''


class _PageFlowLimitError(ValueError):
    """An extension-owned flow result exceeds the published runtime envelope."""


@dataclass(frozen=True)
class _CallableExecution:
    status: str
    value: Any = None


_PAGE_FLOW_EXTENSION_OWNERS: dict[tuple[str, str], _ExtensionOwner] = {}
_PAGE_FLOW_HEALTH: dict[tuple[str, str], _CallableHealth] = {}
_PAGE_FLOW_GENERATION = 0


def register_page_guard(name: str, func: PageGuard, *, replace: bool = False) -> None:
    """Registers an enabled guard by name."""
    normalized = _normalize_registered_name(name)
    _require_bool(replace, 'replace')
    if not callable(func):
        raise ValueError('page guard должен быть callable')
    if normalized in PAGE_GUARDS and not replace:
        raise ValueError(f"page guard '{normalized}' уже зарегистрирован")
    _clear_page_flow_registration_state('guard', normalized)
    PAGE_GUARDS[normalized] = func


def register_page_hook(name: str, func: PageHook, *, replace: bool = False) -> None:
    """Registers an allowed before-render hook by name."""
    normalized = _normalize_registered_name(name)
    _require_bool(replace, 'replace')
    if not callable(func):
        raise ValueError('page hook должен быть callable')
    if normalized in PAGE_HOOKS and not replace:
        raise ValueError(f"page hook '{normalized}' уже зарегистрирован")
    _clear_page_flow_registration_state('hook', normalized)
    PAGE_HOOKS[normalized] = func


def create_page_flow_execution_frame() -> PageFlowExecutionFrame:
    """Creates exactly-once and extension-budget state for one page materialization."""
    return PageFlowExecutionFrame()


def mark_page_flow_extension_owner(kind: str, name: str, extension_id: str) -> None:
    """Marks an already registered page-flow callable as extension-owned."""
    global _PAGE_FLOW_GENERATION

    normalized_kind = _normalize_page_flow_kind(kind)
    normalized_name = _normalize_registered_name(name)
    registry = _page_flow_registry(normalized_kind)
    registered = registry.get(normalized_name)
    if registered is None:
        raise ValueError(f"page flow {normalized_kind} '{normalized_name}' is not registered")
    if not isinstance(extension_id, str) or not extension_id.strip():
        raise ValueError('extension_id must be a non-empty string')

    _PAGE_FLOW_GENERATION += 1
    key = (normalized_kind, normalized_name)
    _PAGE_FLOW_EXTENSION_OWNERS[key] = _ExtensionOwner(
        extension_id=extension_id.strip(),
        callable=registered,
        generation=_PAGE_FLOW_GENERATION,
    )
    _PAGE_FLOW_HEALTH.pop(key, None)


def remove_page_flow_registration(kind: str, name: str) -> None:
    """Removes a page-flow callable together with its process-local runtime state."""
    normalized_kind = _normalize_page_flow_kind(kind)
    normalized_name = _normalize_registered_name(name)
    _page_flow_registry(normalized_kind).pop(normalized_name, None)
    _clear_page_flow_registration_state(normalized_kind, normalized_name)


def snapshot_page_flow_runtime_state() -> dict[str, Any]:
    """Returns registry-adjacent state for atomic custom-extension reload rollback."""
    return {
        'owners': dict(_PAGE_FLOW_EXTENSION_OWNERS),
        'health': copy.deepcopy(_PAGE_FLOW_HEALTH),
        'generation': _PAGE_FLOW_GENERATION,
    }


def restore_page_flow_runtime_state(snapshot: Mapping[str, Any]) -> None:
    """Restores page-flow owner and health state after a failed extension reload."""
    global _PAGE_FLOW_GENERATION

    owners = snapshot.get('owners', {}) if isinstance(snapshot, Mapping) else {}
    health = snapshot.get('health', {}) if isinstance(snapshot, Mapping) else {}
    generation = snapshot.get('generation', 0) if isinstance(snapshot, Mapping) else 0
    _PAGE_FLOW_EXTENSION_OWNERS.clear()
    _PAGE_FLOW_EXTENSION_OWNERS.update(dict(owners))
    _PAGE_FLOW_HEALTH.clear()
    _PAGE_FLOW_HEALTH.update(copy.deepcopy(dict(health)))
    _PAGE_FLOW_GENERATION = int(generation or 0)


def reset_page_flow_runtime_health() -> None:
    """Clears process-local health while preserving current extension ownership."""
    _PAGE_FLOW_HEALTH.clear()


def parse_registry_names(raw: Any) -> list[str]:
    """Parses a JSON array of hooks/guards names from the database."""
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple)):
        values = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = parsed
            elif isinstance(parsed, str):
                values = [parsed]
            else:
                values = [raw]
        except json.JSONDecodeError:
            values = [raw]
    else:
        return []
    return [_normalize_registry_name(value) for value in values if isinstance(value, str) and value.strip()]


def build_page_flow_context(target: Any, **values: Any) -> dict[str, Any]:
    """
    Collects the base context for guards/hooks route/page transitions.

    Hooks are executed before render_page(), so they need the same minimum
    common values, which the renderer will later add for placeholders.
    """
    context = dict(values)
    if 'telegram_id' not in context:
        user = getattr(target, 'from_user', None)
        if user and not getattr(user, 'is_bot', False):
            context['telegram_id'] = user.id

    bot = target if isinstance(target, Bot) else getattr(target, 'bot', None)
    if bot is None:
        message = getattr(target, 'message', None)
        bot = getattr(message, 'bot', None)
    bot_username = (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )
    if bot_username:
        context.setdefault('bot_username', bot_username)
    return context


async def run_page_guards(
    guard_names: list[str],
    target: Any,
    context: Mapping[str, Any],
    *,
    frame: PageFlowExecutionFrame | None = None,
    page_key: str = '',
    scope: str = 'page',
) -> PageGuardResult:
    """Performs guards. An unknown guard is blocking the route."""
    base_context = _require_context_mapping(context)
    execution_frame = frame or create_page_flow_execution_frame()
    for name in guard_names:
        normalized_name = _normalize_registry_name(name)
        guard = PAGE_GUARDS.get(normalized_name)
        if guard is None:
            logger.warning("Неизвестный page guard '%s' — переход заблокирован", name)
            return PageGuardResult(False, "⚠️ Страница временно недоступна")
        try:
            execution = await _execute_page_flow_callable(
                kind='guard',
                name=normalized_name,
                func=guard,
                target=target,
                context=base_context,
                frame=execution_frame,
                page_key=page_key,
                scope=scope,
                normalize=_normalize_and_validate_guard_result,
            )
            if execution.status != 'ok':
                return PageGuardResult(False, "⚠️ Страница временно недоступна")
            normalized = execution.value
        except Exception as e:
            logger.exception("Ошибка page guard '%s': %s", name, e)
            return PageGuardResult(False, "⚠️ Страница временно недоступна")
        if not normalized.allowed:
            return normalized
    return PageGuardResult(True)


async def run_page_hooks(
    hook_names: list[str],
    target: Any,
    context: Mapping[str, Any],
    *,
    frame: PageFlowExecutionFrame | None = None,
    page_key: str = '',
    scope: str = 'page',
) -> PageHookResult:
    """Executes hooks. Each next hook sees the context of previous hooks."""
    merged = PageHookResult()
    current_context = _require_context_mapping(context)
    execution_frame = frame or create_page_flow_execution_frame()
    for name in hook_names:
        normalized_name = _normalize_registry_name(name)
        if normalized_name in execution_frame.seen_hook_names:
            continue
        execution_frame.seen_hook_names.add(normalized_name)
        hook = PAGE_HOOKS.get(normalized_name)
        if hook is None:
            logger.warning("Неизвестный page hook '%s' — пропускаем", name)
            continue
        try:
            def normalize(result: Any) -> PageHookResult:
                normalized_result = _normalize_hook_result(result)
                if _get_extension_owner('hook', normalized_name, hook) is not None:
                    trial = _copy_hook_result(execution_frame.extension_hook_result)
                    _merge_hook_result(trial, normalized_result)
                    _validate_hook_result_limits(normalized_result)
                    _validate_hook_result_limits(trial)
                return normalized_result

            execution = await _execute_page_flow_callable(
                kind='hook',
                name=normalized_name,
                func=hook,
                target=target,
                context=current_context,
                frame=execution_frame,
                page_key=page_key,
                scope=scope,
                normalize=normalize,
            )
            if execution.status != 'ok':
                continue
            normalized = execution.value
            if _get_extension_owner('hook', normalized_name, hook) is not None:
                _merge_hook_result(execution_frame.extension_hook_result, normalized)
            _merge_hook_result(merged, normalized)
        except Exception as e:
            logger.exception("Ошибка page hook '%s': %s", name, e)
            continue
        current_context.update(normalized.context)
    return merged


def _normalize_page_flow_kind(kind: Any) -> str:
    normalized = str(kind or '').strip().casefold()
    if normalized not in _PAGE_FLOW_KINDS:
        raise ValueError("page flow kind must be 'guard' or 'hook'")
    return normalized


def _page_flow_registry(kind: str) -> dict[str, Callable[..., Any]]:
    return PAGE_GUARDS if kind == 'guard' else PAGE_HOOKS


def _clear_page_flow_registration_state(kind: str, name: str) -> None:
    key = (_normalize_page_flow_kind(kind), _normalize_registry_name(name))
    _PAGE_FLOW_EXTENSION_OWNERS.pop(key, None)
    _PAGE_FLOW_HEALTH.pop(key, None)


def _get_extension_owner(
    kind: str,
    name: str,
    func: Callable[..., Any],
) -> _ExtensionOwner | None:
    key = (kind, name)
    owner = _PAGE_FLOW_EXTENSION_OWNERS.get(key)
    if owner is None:
        return None
    if owner.callable is func and _page_flow_registry(kind).get(name) is func:
        return owner
    _PAGE_FLOW_EXTENSION_OWNERS.pop(key, None)
    _PAGE_FLOW_HEALTH.pop(key, None)
    return None


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _health_status(health: _CallableHealth, now: float | None = None) -> str:
    current = time.monotonic() if now is None else now
    if health.half_open_in_flight:
        return 'half_open'
    if health.opened_until_monotonic > current:
        return 'open'
    if health.consecutive_failures >= PAGE_FLOW_CIRCUIT_FAILURE_THRESHOLD:
        return 'half_open'
    if health.consecutive_failures:
        return 'degraded'
    if health.calls:
        return 'healthy'
    return 'idle'


def _begin_extension_call(
    key: tuple[str, str],
    owner: _ExtensionOwner,
    *,
    page_key: str,
    scope: str,
) -> tuple[_CallableHealth, bool]:
    health = _PAGE_FLOW_HEALTH.setdefault(key, _CallableHealth())
    now = time.monotonic()
    if health.opened_until_monotonic > now:
        health.open_skips += 1
        health.last_page = page_key
        health.last_scope = scope
        health.last_event_at = _utc_now_text()
        return health, False
    if health.consecutive_failures >= PAGE_FLOW_CIRCUIT_FAILURE_THRESHOLD:
        if health.half_open_in_flight:
            health.open_skips += 1
            health.last_page = page_key
            health.last_scope = scope
            health.last_event_at = _utc_now_text()
            return health, False
        health.half_open_in_flight = True
        logger.info(
            "Page flow circuit half-open probe: extension=%s kind=%s name=%s",
            owner.extension_id,
            key[0],
            key[1],
        )
    health.calls += 1
    health.last_page = page_key
    health.last_scope = scope
    health.last_event_at = _utc_now_text()
    return health, True


def _record_extension_success(
    key: tuple[str, str],
    owner: _ExtensionOwner,
    health: _CallableHealth,
    duration: float,
) -> None:
    previous_status = _health_status(health)
    health.successes += 1
    health.consecutive_failures = 0
    health.opened_until_monotonic = 0.0
    health.half_open_in_flight = False
    health.last_duration_ms = round(duration * 1000, 3)
    health.last_failure_kind = ''
    health.last_error = ''
    health.last_event_at = _utc_now_text()
    if previous_status in {'open', 'half_open', 'degraded'}:
        logger.info(
            "Page flow circuit recovered: extension=%s kind=%s name=%s",
            owner.extension_id,
            key[0],
            key[1],
        )


def _record_extension_failure(
    key: tuple[str, str],
    owner: _ExtensionOwner,
    health: _CallableHealth,
    *,
    kind: str,
    error: BaseException | str,
    duration: float,
) -> None:
    health.failures += 1
    health.consecutive_failures += 1
    health.half_open_in_flight = False
    health.last_duration_ms = round(duration * 1000, 3)
    health.last_failure_kind = kind
    health.last_error = _bounded_error_text(error)
    health.last_event_at = _utc_now_text()
    if kind == 'timeout':
        health.timeouts += 1
    elif kind == 'limit_rejection':
        health.limit_rejections += 1

    logger.warning(
        "Extension page flow failure: extension=%s kind=%s name=%s failure=%s error=%s",
        owner.extension_id,
        key[0],
        key[1],
        kind,
        health.last_error,
    )
    if health.consecutive_failures >= PAGE_FLOW_CIRCUIT_FAILURE_THRESHOLD:
        health.opened_until_monotonic = (
            time.monotonic() + PAGE_FLOW_CIRCUIT_COOLDOWN_SECONDS
        )
        logger.error(
            "Page flow circuit opened: extension=%s kind=%s name=%s cooldown_seconds=%s",
            owner.extension_id,
            key[0],
            key[1],
            int(PAGE_FLOW_CIRCUIT_COOLDOWN_SECONDS),
        )


def _bounded_error_text(error: BaseException | str) -> str:
    """Keeps diagnostics useful without retaining Telegram-sized identifiers."""
    if isinstance(error, BaseException):
        raw = f'{type(error).__name__}: {error}'
    else:
        raw = str(error)
    redacted = re.sub(r'(?<![A-Za-z0-9_])-?\d{5,}(?![A-Za-z0-9_])', '[redacted-id]', raw)
    return redacted.replace('\r', ' ').replace('\n', ' ')[:240]


def _discard_awaitable(value: Any) -> None:
    if isinstance(value, asyncio.Future):
        value.cancel()
    elif inspect.iscoroutine(value):
        value.close()


async def _execute_page_flow_callable(
    *,
    kind: str,
    name: str,
    func: Callable[..., Any],
    target: Any,
    context: Mapping[str, Any],
    frame: PageFlowExecutionFrame,
    page_key: str,
    scope: str,
    normalize: Callable[[Any], Any],
) -> _CallableExecution:
    key = (kind, name)
    owner = _get_extension_owner(kind, name, func)
    health: _CallableHealth | None = None
    if owner is not None:
        health, allowed = _begin_extension_call(
            key,
            owner,
            page_key=page_key,
            scope=scope,
        )
        if not allowed:
            return _CallableExecution('circuit_open')
        remaining_budget = (
            PAGE_FLOW_TOTAL_EXTENSION_BUDGET_SECONDS
            - frame.extension_elapsed_seconds
        )
        if remaining_budget <= 0:
            health.calls = max(health.calls - 1, 0)
            health.budget_skips += 1
            health.half_open_in_flight = False
            return _CallableExecution('budget_exhausted')
        call_timeout = min(PAGE_FLOW_CALL_TIMEOUT_SECONDS, remaining_budget)
    else:
        call_timeout = 0.0

    started = time.perf_counter()
    raw_result: Any = None
    try:
        raw_result = func(target, dict(context))
        synchronous_elapsed = time.perf_counter() - started
        if owner is not None and synchronous_elapsed > call_timeout:
            _discard_awaitable(raw_result)
            raise TimeoutError('synchronous page flow callable exceeded its watchdog')
        if inspect.isawaitable(raw_result):
            if owner is None:
                raw_result = await raw_result
            else:
                remaining_call_time = max(call_timeout - synchronous_elapsed, 0.0)
                if remaining_call_time <= 0:
                    _discard_awaitable(raw_result)
                    raise TimeoutError('page flow callable exhausted its watchdog')
                raw_result = await asyncio.wait_for(
                    raw_result,
                    timeout=remaining_call_time,
                )
        normalized = normalize(raw_result)
        if owner is not None and kind == 'guard':
            _validate_guard_result_limits(normalized)
        duration = time.perf_counter() - started
        if owner is not None and duration > call_timeout:
            raise TimeoutError('page flow callable exceeded its watchdog')
    except asyncio.CancelledError:
        if health is not None:
            health.half_open_in_flight = False
        raise
    except (asyncio.TimeoutError, TimeoutError) as exc:
        duration = time.perf_counter() - started
        if owner is not None and health is not None:
            frame.extension_elapsed_seconds += duration
            _record_extension_failure(
                key,
                owner,
                health,
                kind='timeout',
                error=exc,
                duration=duration,
            )
        else:
            logger.exception("Page flow %s '%s' timed out", kind, name)
        return _CallableExecution('failed')
    except _PageFlowLimitError as exc:
        duration = time.perf_counter() - started
        if owner is not None and health is not None:
            frame.extension_elapsed_seconds += duration
            _record_extension_failure(
                key,
                owner,
                health,
                kind='limit_rejection',
                error=exc,
                duration=duration,
            )
        else:
            logger.warning("Page flow %s '%s' result rejected: %s", kind, name, exc)
        return _CallableExecution('failed')
    except Exception as exc:
        duration = time.perf_counter() - started
        if owner is not None and health is not None:
            frame.extension_elapsed_seconds += duration
            failure_kind = 'malformed_result' if isinstance(exc, ValueError) else 'exception'
            _record_extension_failure(
                key,
                owner,
                health,
                kind=failure_kind,
                error=exc,
                duration=duration,
            )
        else:
            logger.exception("Page flow %s '%s' failed: %s", kind, name, exc)
        return _CallableExecution('failed')

    if owner is not None and health is not None:
        frame.extension_elapsed_seconds += duration
        _record_extension_success(key, owner, health, duration)
    return _CallableExecution('ok', normalized)


def _normalize_and_validate_guard_result(result: Any) -> PageGuardResult:
    return _normalize_guard_result(result)


def _validate_guard_result_limits(result: PageGuardResult) -> None:
    if len(result.message) > PAGE_GUARD_MAX_MESSAGE_LENGTH:
        raise _PageFlowLimitError(
            f'guard message exceeds {PAGE_GUARD_MAX_MESSAGE_LENGTH} characters'
        )


def _copy_hook_result(result: PageHookResult) -> PageHookResult:
    return PageHookResult(
        context=dict(result.context),
        text_replacements=dict(result.text_replacements),
        visibility=dict(result.visibility),
        prepend_buttons=[list(row) for row in result.prepend_buttons or []] or None,
        append_buttons=[list(row) for row in result.append_buttons or []] or None,
    )


def _estimate_hook_payload(value: Any) -> tuple[int, int, int]:
    size = 0
    nodes = 0
    maximum_depth = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited: set[int] = set()
    while stack:
        item, depth = stack.pop()
        nodes += 1
        maximum_depth = max(maximum_depth, depth)
        if nodes > PAGE_HOOK_MAX_PAYLOAD_NODES:
            break
        if isinstance(item, str):
            size += len(item.encode('utf-8'))
        elif isinstance(item, bytes):
            size += len(item)
        elif item is None or isinstance(item, (bool, int, float)):
            size += len(str(item).encode('utf-8'))
        elif isinstance(item, Mapping):
            marker = id(item)
            if marker in visited:
                continue
            visited.add(marker)
            stack.extend((child, depth + 1) for pair in item.items() for child in pair)
        elif isinstance(item, (list, tuple, set, frozenset)):
            marker = id(item)
            if marker in visited:
                continue
            visited.add(marker)
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, InlineKeyboardButton):
            marker = id(item)
            if marker in visited:
                continue
            visited.add(marker)
            stack.append((item.model_dump(exclude_none=True), depth + 1))
        else:
            size += 64
    return size, nodes, maximum_depth


def _validate_hook_result_limits(result: PageHookResult) -> None:
    mapping_entries = (
        len(result.context)
        + len(result.text_replacements)
        + len(result.visibility)
    )
    if mapping_entries > PAGE_HOOK_MAX_MAPPING_ENTRIES:
        raise _PageFlowLimitError(
            f'hook mappings exceed {PAGE_HOOK_MAX_MAPPING_ENTRIES} entries'
        )

    rows = list(result.prepend_buttons or []) + list(result.append_buttons or [])
    if len(rows) > PAGE_HOOK_MAX_BUTTON_ROWS:
        raise _PageFlowLimitError(
            f'hook buttons exceed {PAGE_HOOK_MAX_BUTTON_ROWS} rows'
        )
    button_count = 0
    for row in rows:
        if len(row) > PAGE_HOOK_MAX_BUTTONS_PER_ROW:
            raise _PageFlowLimitError(
                f'hook button row exceeds {PAGE_HOOK_MAX_BUTTONS_PER_ROW} buttons'
            )
        button_count += len(row)
    if button_count > PAGE_HOOK_MAX_BUTTONS:
        raise _PageFlowLimitError(
            f'hook buttons exceed {PAGE_HOOK_MAX_BUTTONS} total buttons'
        )

    payload = {
        'context': result.context,
        'text_replacements': result.text_replacements,
        'visibility': result.visibility,
        'prepend_buttons': rows[:len(result.prepend_buttons or [])],
        'append_buttons': rows[len(result.prepend_buttons or []):],
    }
    size, nodes, depth = _estimate_hook_payload(payload)
    if size > PAGE_HOOK_MAX_PAYLOAD_BYTES:
        raise _PageFlowLimitError(
            f'hook payload exceeds {PAGE_HOOK_MAX_PAYLOAD_BYTES} bytes'
        )
    if nodes > PAGE_HOOK_MAX_PAYLOAD_NODES:
        raise _PageFlowLimitError(
            f'hook payload exceeds {PAGE_HOOK_MAX_PAYLOAD_NODES} nodes'
        )
    if depth > PAGE_HOOK_MAX_PAYLOAD_DEPTH:
        raise _PageFlowLimitError(
            f'hook payload exceeds depth {PAGE_HOOK_MAX_PAYLOAD_DEPTH}'
        )


def get_page_flow_runtime_diagnostics() -> dict[str, Any]:
    """Returns bounded extension-owned page-flow health diagnostics."""
    now = time.monotonic()
    items: list[dict[str, Any]] = []
    for key, owner in sorted(list(_PAGE_FLOW_EXTENSION_OWNERS.items())):
        registered = _page_flow_registry(key[0]).get(key[1])
        if registered is not owner.callable:
            _PAGE_FLOW_EXTENSION_OWNERS.pop(key, None)
            _PAGE_FLOW_HEALTH.pop(key, None)
            continue
        health = _PAGE_FLOW_HEALTH.get(key, _CallableHealth())
        remaining = max(health.opened_until_monotonic - now, 0.0)
        items.append({
            'kind': key[0],
            'name': key[1],
            'extension': owner.extension_id,
            'status': _health_status(health, now),
            'calls': health.calls,
            'successes': health.successes,
            'failures': health.failures,
            'timeouts': health.timeouts,
            'limit_rejections': health.limit_rejections,
            'open_skips': health.open_skips,
            'budget_skips': health.budget_skips,
            'consecutive_failures': health.consecutive_failures,
            'last_duration_ms': health.last_duration_ms,
            'last_failure_kind': health.last_failure_kind,
            'last_error': health.last_error[:240],
            'last_page': health.last_page,
            'last_scope': health.last_scope,
            'last_event_at': health.last_event_at,
            'disabled_for_seconds': int(remaining) if remaining else 0,
        })
    totals = {
        'registered': len(items),
        'calls': sum(item['calls'] for item in items),
        'failures': sum(item['failures'] for item in items),
        'timeouts': sum(item['timeouts'] for item in items),
        'limit_rejections': sum(item['limit_rejections'] for item in items),
        'skips': sum(item['open_skips'] + item['budget_skips'] for item in items),
        'degraded': sum(item['status'] == 'degraded' for item in items),
        'open': sum(item['status'] in {'open', 'half_open'} for item in items),
    }
    return {'totals': totals, 'items': items}


def _normalize_registry_name(name: Any) -> str:
    return str(name).strip().casefold()


def _normalize_registered_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError("registry name должен быть строкой")
    return _normalize_registry_name(name)


def _require_context_mapping(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError("context должен быть mapping")
    return dict(context)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_guard_result(result: PageGuardResult | bool | Mapping[str, Any] | None) -> PageGuardResult:
    if isinstance(result, PageGuardResult):
        return PageGuardResult(
            _require_bool(result.allowed, 'allowed'),
            _optional_text(result.message, 'message') or '',
            _require_bool(result.show_alert, 'show_alert'),
        )
    if isinstance(result, bool):
        return PageGuardResult(result)
    if isinstance(result, Mapping):
        if 'allowed' not in result:
            raise ValueError("page guard mapping должен содержать поле allowed")
        message = _optional_text(result.get('message'), 'message') or ''
        return PageGuardResult(
            _require_bool(result.get('allowed'), 'allowed'),
            message,
            _require_bool(result.get('show_alert', True), 'show_alert'),
        )
    if result is None:
        return PageGuardResult(False)
    raise ValueError("page guard должен вернуть PageGuardResult, bool или mapping с allowed")


def _require_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"page guard field {field_name} должен быть bool")


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"page guard field {field_name} должен быть строкой")
    return value


def _normalize_hook_result(result: PageHookResult | Mapping[str, Any] | None) -> PageHookResult:
    if isinstance(result, PageHookResult):
        return PageHookResult(
            context=_require_mapping_dict(result.context, 'context'),
            text_replacements=_require_mapping_dict(result.text_replacements, 'text_replacements'),
            visibility=_require_visibility_dict(result.visibility),
            prepend_buttons=_normalize_button_rows(result.prepend_buttons, 'prepend_buttons'),
            append_buttons=_normalize_button_rows(result.append_buttons, 'append_buttons'),
        )
    if result is None:
        return PageHookResult()
    if not isinstance(result, Mapping):
        raise ValueError("page hook должен вернуть PageHookResult, mapping или None")
    return PageHookResult(
        context=_require_mapping_dict(result.get('context'), 'context'),
        text_replacements=_require_mapping_dict(result.get('text_replacements'), 'text_replacements'),
        visibility=_require_visibility_dict(result.get('visibility')),
        prepend_buttons=_normalize_button_rows(result.get('prepend_buttons'), 'prepend_buttons'),
        append_buttons=_normalize_button_rows(result.get('append_buttons'), 'append_buttons'),
    )


def _require_mapping_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"page hook field {field_name} должен быть mapping")
    return dict(value)


def _require_visibility_dict(value: Any) -> dict[str, bool]:
    raw = _require_mapping_dict(value, 'visibility')
    result: dict[str, bool] = {}
    for button_id, visible in raw.items():
        if not isinstance(button_id, str):
            raise ValueError("page hook visibility keys должны быть строками")
        if not isinstance(visible, bool):
            raise ValueError("page hook visibility values должны быть bool")
        result[button_id] = visible
    return result


def _normalize_button_rows(value: Any, field_name: str) -> list[list[InlineKeyboardButton]] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"page hook field {field_name} должен быть списком рядов кнопок")
    rows: list[list[InlineKeyboardButton]] = []
    for row in value:
        if not isinstance(row, (list, tuple)):
            raise ValueError(f"page hook field {field_name} должен содержать только ряды кнопок")
        buttons: list[InlineKeyboardButton] = []
        for button in row:
            if not isinstance(button, InlineKeyboardButton):
                raise ValueError(f"page hook field {field_name} должен содержать только InlineKeyboardButton")
            buttons.append(button)
        rows.append(buttons)
    return rows


def _merge_hook_result(target: PageHookResult, source: PageHookResult) -> None:
    target.context.update(source.context)
    target.text_replacements.update(source.text_replacements)
    target.visibility.update(source.visibility)
    if source.prepend_buttons:
        if target.prepend_buttons is None:
            target.prepend_buttons = []
        target.prepend_buttons.extend(source.prepend_buttons)
    if source.append_buttons:
        if target.append_buttons is None:
            target.append_buttons = []
        target.append_buttons.extend(source.append_buttons)


def _context_int(context: Mapping[str, Any], key: str) -> int | None:
    value = context.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _context_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    return value if isinstance(value, str) else ''


def _missing_context_values(
    context: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key not in context}


def _not_banned_guard(target: Any, context: Mapping[str, Any]) -> PageGuardResult:
    telegram_id = _context_int(context, 'telegram_id')
    if telegram_id is None:
        return PageGuardResult(False, "⚠️ Страница недоступна")

    from database.requests import is_user_banned

    if is_user_banned(telegram_id):
        return PageGuardResult(False, "⛔ Доступ заблокирован")
    return PageGuardResult(True)


def _referral_enabled_guard(target: Any, context: Mapping[str, Any]) -> PageGuardResult:
    from database.requests import is_referral_enabled

    if not is_referral_enabled():
        return PageGuardResult(False, "❌ Реферальная система недоступна")
    return PageGuardResult(True)


def _widget_tariffs_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds tariff-list context if it is not already passed."""
    if 'tariffs_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_tariff_text

    return PageHookResult(context={'tariffs_html': build_tariff_text()})


def _widget_referral_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds the context of referral placeholders if it has not already been sent."""
    if 'referral_link' in context and 'referral_stats_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_referral_context_values

    values = build_referral_context_values(
        _context_int(context, 'telegram_id'),
        _context_text(context, 'bot_username'),
    )
    return PageHookResult(context=_missing_context_values(context, values))


def _widget_support_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds login context to native support."""
    if 'support_title_html' in context and 'support_instruction_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_support_context_values

    thread_id = _context_int(context, 'support_thread_id') or _context_int(context, 'thread_id')
    values = build_support_context_values(thread_id=thread_id)
    return PageHookResult(context=_missing_context_values(context, values))


def _widget_profile_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds profile, balance and key summary context."""
    if 'user_profile_html' in context and 'keys_summary_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_user_profile_context_values

    values = build_user_profile_context_values(_context_int(context, 'telegram_id'))
    return PageHookResult(context=_missing_context_values(context, values))


async def _widget_my_keys_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds context to a text list of keys."""
    if 'keys_list_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_my_keys_context_values

    values = await build_my_keys_context_values(_context_int(context, 'telegram_id'))
    return PageHookResult(context=_missing_context_values(context, values))


register_page_guard('not_banned', _not_banned_guard)
register_page_guard('referral_enabled', _referral_enabled_guard)
register_page_hook('widget_tariffs', _widget_tariffs_hook)
register_page_hook('widget_referral', _widget_referral_hook)
register_page_hook('widget_support', _widget_support_hook)
register_page_hook('widget_profile', _widget_profile_hook)
register_page_hook('widget_my_keys', _widget_my_keys_hook)
