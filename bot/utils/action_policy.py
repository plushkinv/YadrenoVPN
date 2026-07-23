"""Semantic action policies for controlled custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable

logger = logging.getLogger(__name__)

SUPPORTED_CORE_ACTIONS = frozenset({
    'key.purchase.start',
    'balance.topup.start',
    'key.renew.start',
    'trial.activate',
    'key.configure.start',
    'key.replace.start',
    'key.rename.start',
    'key.delete',
})

KEY_ID_ACTIONS = frozenset({
    'key.renew.start',
    'key.configure.start',
    'key.replace.start',
    'key.rename.start',
    'key.delete',
})

ACTION_POLICY_PHASES = frozenset({'preview', 'execute'})
ACTION_POLICY_SOURCES = frozenset({'command', 'callback', 'button'})

_POLICY_NAME_RE = re.compile(r'^[a-z][a-z0-9_.:-]{0,127}$')

ActionPolicyHandler = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]

# policy name -> registration data. Dict insertion order is execution order.
ACTION_POLICIES: dict[str, dict[str, Any]] = {}
_CURRENT_ACTION_POLICY_PHASE: ContextVar[str | None] = ContextVar(
    'current_action_policy_phase',
    default=None,
)


def register_action_policy(
    extension_id: str,
    name: str,
    *,
    actions: Sequence[str],
    handler: ActionPolicyHandler,
    replace: bool = False,
) -> None:
    """Register one extension-owned policy for a fixed set of core actions."""
    from database.requests import normalize_extension_id

    ext_id = normalize_extension_id(extension_id)
    policy_name = normalize_action_policy_name(name)
    normalized_actions = normalize_action_policy_actions(actions)
    if not callable(handler):
        raise ValueError('action policy handler must be callable')
    if not isinstance(replace, bool):
        raise ValueError('replace must be bool')
    if policy_name in ACTION_POLICIES and not replace:
        raise ValueError(f"action policy '{policy_name}' is already registered")
    ACTION_POLICIES[policy_name] = {
        'extension_id': ext_id,
        'actions': normalized_actions,
        'handler': handler,
    }


def remove_action_policies(policy_names: set[str]) -> None:
    """Remove policies recorded for one unloaded extension."""
    for name in set(policy_names):
        ACTION_POLICIES.pop(normalize_action_policy_name(name), None)


def normalize_action_policy_name(name: Any) -> str:
    """Normalize a public action policy registry name."""
    if not isinstance(name, str):
        raise ValueError('action policy name must be a string')
    value = name.strip().casefold()
    if not _POLICY_NAME_RE.fullmatch(value):
        raise ValueError("action policy name must match ^[a-z][a-z0-9_.:-]{0,127}$")
    return value


def normalize_action_policy_actions(actions: Any) -> tuple[str, ...]:
    """Validate and de-duplicate subscribed semantic actions."""
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        raise ValueError('actions must be a non-empty sequence')
    normalized: list[str] = []
    for raw_action in actions:
        action = normalize_core_action(raw_action)
        if action in normalized:
            raise ValueError(f'duplicate semantic action: {action}')
        normalized.append(action)
    if not normalized:
        raise ValueError('actions must not be empty')
    return tuple(normalized)


def normalize_core_action(action: Any) -> str:
    """Validate one semantic action against the fixed core catalog."""
    if not isinstance(action, str):
        raise ValueError('action must be a string')
    value = action.strip().casefold()
    if value not in SUPPORTED_CORE_ACTIONS:
        raise ValueError(f"unsupported semantic action: {action}")
    return value


def normalize_core_action_params(action: Any, params: Any = None) -> dict[str, Any]:
    """Validate the exact public parameter schema of one core action."""
    action_name = normalize_core_action(action)
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ValueError('action params must be a mapping')
    normalized = dict(params)
    allowed = {'key_id'} if action_name in KEY_ID_ACTIONS else set()
    unknown = set(normalized) - allowed
    if unknown:
        raise ValueError(f"unsupported params for {action_name}: {', '.join(sorted(unknown))}")
    if action_name in KEY_ID_ACTIONS:
        key_id = normalized.get('key_id')
        if isinstance(key_id, bool) or not isinstance(key_id, int) or key_id <= 0:
            raise ValueError(f'{action_name} requires a positive integer key_id')
        return {'key_id': key_id}
    return {}


async def run_action_policies(
    action: str,
    params: Mapping[str, Any] | None,
    *,
    telegram_id: int,
    source: str,
    phase: str,
    bot: Any = None,
) -> dict[str, Any]:
    """Run matching policies and return the first terminal decision."""
    action_name = normalize_core_action(action)
    normalized_params = normalize_core_action_params(action_name, params)
    if isinstance(telegram_id, bool) or not isinstance(telegram_id, int) or telegram_id <= 0:
        raise ValueError('telegram_id must be a positive integer')
    if not isinstance(source, str) or source.strip().casefold() not in ACTION_POLICY_SOURCES:
        raise ValueError(f'unsupported action policy source: {source}')
    source_name = source.strip().casefold()
    if phase not in ACTION_POLICY_PHASES:
        raise ValueError(f'unsupported action policy phase: {phase}')

    base_context = {
        'action': action_name,
        'params': dict(normalized_params),
        'telegram_id': telegram_id,
        'source': source_name,
        'phase': phase,
    }

    effective_label: str | None = None
    for policy_name, registration in list(ACTION_POLICIES.items()):
        if action_name not in registration['actions']:
            continue
        try:
            from bot.utils.custom_extensions import _extension_bot_context

            with _action_policy_context(phase), _extension_bot_context(bot):
                raw_decision = registration['handler'](dict(base_context))
                if inspect.isawaitable(raw_decision):
                    raw_decision = await raw_decision
            decision = normalize_action_policy_decision(raw_decision)
        except Exception as exc:
            logger.exception("Action policy '%s' failed for %s: %s", policy_name, action_name, exc)
            if phase == 'preview':
                return {'decision': 'continue'}
            return {
                'decision': 'deny',
                'message': '⚠️ Действие временно недоступно',
                'show_alert': True,
                '_policy': policy_name,
                '_extension_id': registration['extension_id'],
            }

        if decision.get('label'):
            effective_label = decision['label']
        if decision['decision'] == 'continue':
            continue
        result = dict(decision)
        if effective_label:
            result['label'] = effective_label
        result['_policy'] = policy_name
        result['_extension_id'] = registration['extension_id']
        return result

    result: dict[str, Any] = {'decision': 'continue'}
    if effective_label:
        result['label'] = effective_label
    return result


def ensure_action_policy_read_only(operation: str) -> None:
    """Reject extension mutations attempted while a policy is being evaluated."""
    phase = _CURRENT_ACTION_POLICY_PHASE.get()
    if phase is not None:
        raise RuntimeError(
            f"action policies are read-only; '{operation}' is unavailable during {phase}"
        )


@contextmanager
def _action_policy_context(phase: str) -> Iterator[None]:
    token = _CURRENT_ACTION_POLICY_PHASE.set(phase)
    try:
        yield
    finally:
        _CURRENT_ACTION_POLICY_PHASE.reset(token)


def normalize_action_policy_decision(raw_decision: Any) -> dict[str, Any]:
    """Validate one declarative action policy result."""
    if raw_decision is None:
        return {'decision': 'continue'}
    if not isinstance(raw_decision, Mapping):
        raise ValueError('action policy must return a mapping or None')
    decision = dict(raw_decision)
    decision_name = decision.get('decision')
    if not isinstance(decision_name, str):
        raise ValueError('action policy decision must be a string')
    decision_name = decision_name.strip().casefold()
    if decision_name not in {'continue', 'deny', 'redirect'}:
        raise ValueError(f'unsupported action policy decision: {decision_name}')
    decision['decision'] = decision_name

    allowed_by_decision = {
        'continue': {'decision', 'label'},
        'deny': {'decision', 'message', 'show_alert', 'label'},
        'redirect': {
            'decision', 'target', 'action', 'params', 'payload',
            'page_key', 'route_key', 'context', 'label',
        },
    }
    unknown = set(decision) - allowed_by_decision[decision_name]
    if unknown:
        raise ValueError(f"unsupported action policy fields: {', '.join(sorted(unknown))}")

    _normalize_optional_text(decision, 'label', allow_empty=False)
    if decision_name == 'continue':
        return decision
    if decision_name == 'deny':
        _normalize_optional_text(decision, 'message', allow_empty=False)
        if 'show_alert' in decision and not isinstance(decision['show_alert'], bool):
            raise ValueError('show_alert must be bool')
        decision.setdefault('show_alert', True)
        return decision

    target = decision.get('target')
    if not isinstance(target, str):
        raise ValueError('redirect target must be a string')
    target = target.strip().casefold()
    if target not in {'core_action', 'extension_action', 'page', 'route'}:
        raise ValueError(f'unsupported action policy redirect target: {target}')
    decision['target'] = target

    target_fields = {
        'core_action': {'decision', 'target', 'action', 'params', 'label'},
        'extension_action': {'decision', 'target', 'action', 'payload', 'label'},
        'page': {'decision', 'target', 'page_key', 'context', 'label'},
        'route': {'decision', 'target', 'route_key', 'context', 'label'},
    }
    irrelevant = set(decision) - target_fields[target]
    if irrelevant:
        raise ValueError(
            f"unsupported fields for {target} redirect: {', '.join(sorted(irrelevant))}"
        )

    if target == 'core_action':
        decision['action'] = normalize_core_action(decision.get('action'))
        decision['params'] = normalize_core_action_params(
            decision['action'],
            decision.get('params'),
        )
    elif target == 'extension_action':
        from bot.utils.extension_callbacks import (
            normalize_extension_action_name,
            normalize_extension_callback_payload,
        )

        decision['action'] = normalize_extension_action_name(decision.get('action'))
        if 'payload' in decision and decision['payload'] is not None:
            decision['payload'] = normalize_extension_callback_payload(decision['payload'])
    elif target == 'page':
        decision['page_key'] = _required_text(decision.get('page_key'), 'page_key')
        decision['context'] = _normalize_optional_context(decision.get('context'))
    else:
        decision['route_key'] = _required_text(decision.get('route_key'), 'route_key')
        decision['context'] = _normalize_optional_context(decision.get('context'))
    return decision


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


def _normalize_optional_text(decision: dict[str, Any], field: str, *, allow_empty: bool) -> None:
    if field not in decision or decision[field] is None:
        return
    if not isinstance(decision[field], str):
        raise ValueError(f'{field} must be a string')
    value = decision[field].strip()
    if not value and not allow_empty:
        raise ValueError(f'{field} must not be empty')
    decision[field] = value


def _normalize_optional_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError('context must be a mapping')
    return dict(value)


__all__ = [
    'ACTION_POLICIES',
    'KEY_ID_ACTIONS',
    'ACTION_POLICY_SOURCES',
    'SUPPORTED_CORE_ACTIONS',
    'ensure_action_policy_read_only',
    'normalize_action_policy_actions',
    'normalize_action_policy_decision',
    'normalize_action_policy_name',
    'normalize_core_action',
    'normalize_core_action_params',
    'register_action_policy',
    'remove_action_policies',
    'run_action_policies',
]
