"""Runtime dispatcher for semantic user actions."""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram.types import CallbackQuery, Message

from bot.utils.action_policy import (
    ACTION_POLICIES,
    normalize_core_action,
    normalize_core_action_params,
    run_action_policies,
)

logger = logging.getLogger(__name__)

MAX_ACTION_REDIRECT_DEPTH = 8

_INTERNAL_BUTTON_ACTIONS = {
    'cmd_buy': 'key.purchase.start',
    'cmd_balance_topup': 'balance.topup.start',
    'cmd_activate_trial': 'trial.activate',
}

_SYSTEM_BUTTON_ACTIONS = {
    'btn_key_renew': 'key.renew.start',
    'btn_key_configure': 'key.configure.start',
    'btn_key_replace': 'key.replace.start',
    'btn_key_rename': 'key.rename.start',
    'btn_key_delete': 'key.delete',
}


@dataclass(frozen=True)
class CoreActionRequest:
    """Validated internal request passed only to core action executors."""

    target: CallbackQuery | Message
    action: str
    params: dict[str, Any]
    telegram_id: int
    source: str
    state: Any = None


CoreActionExecutor = Callable[[CoreActionRequest], Any | Awaitable[Any]]
CORE_ACTION_EXECUTORS: dict[str, CoreActionExecutor] = {}


def register_core_action_executor(
    action: str,
    executor: CoreActionExecutor,
    *,
    replace: bool = False,
) -> None:
    """Register one trusted core executor for a supported semantic action."""
    action_name = normalize_core_action(action)
    if not callable(executor):
        raise ValueError('core action executor must be callable')
    if not isinstance(replace, bool):
        raise ValueError('replace must be bool')
    if action_name in CORE_ACTION_EXECUTORS and not replace:
        raise ValueError(f"core action executor '{action_name}' is already registered")
    CORE_ACTION_EXECUTORS[action_name] = executor


async def dispatch_core_action(
    target: CallbackQuery | Message,
    action: str,
    params: Mapping[str, Any] | None = None,
    *,
    source: str,
    state: Any = None,
    _visited: tuple[str, ...] = (),
) -> bool:
    """Resolve policies and execute or redirect one semantic core action."""
    try:
        action_name = normalize_core_action(action)
        normalized_params = normalize_core_action_params(action_name, params)
        telegram_id = _target_telegram_id(target)
        if telegram_id is None:
            raise ValueError('semantic action target has no Telegram user')
    except Exception as exc:
        logger.warning('Rejected semantic action %r: %s', action, exc)
        await _deny_action(target, '⚠️ Действие недоступно', show_alert=True)
        return False

    if action_name in _visited or len(_visited) >= MAX_ACTION_REDIRECT_DEPTH:
        logger.warning('Semantic action redirect loop: %s -> %s', _visited, action_name)
        await _deny_action(target, '⚠️ Не удалось выполнить действие', show_alert=True)
        return False

    decision = await run_action_policies(
        action_name,
        normalized_params,
        telegram_id=telegram_id,
        source=source,
        phase='execute',
        bot=getattr(target, 'bot', None),
    )
    decision_name = decision['decision']

    if decision_name == 'continue':
        executor = CORE_ACTION_EXECUTORS.get(action_name)
        if executor is None:
            logger.error("No executor registered for semantic action '%s'", action_name)
            await _deny_action(target, '⚠️ Действие временно недоступно', show_alert=True)
            return False
        request = CoreActionRequest(
            target=target,
            action=action_name,
            params=normalized_params,
            telegram_id=telegram_id,
            source=source,
            state=state,
        )
        try:
            result = executor(request)
            if inspect.isawaitable(result):
                await result
            return True
        except Exception as exc:
            logger.exception("Core semantic action '%s' failed: %s", action_name, exc)
            await _deny_action(target, '⚠️ Не удалось выполнить действие', show_alert=True)
            return False

    if decision_name == 'deny':
        await _deny_action(
            target,
            decision.get('message') or '⚠️ Действие недоступно',
            show_alert=bool(decision.get('show_alert', True)),
        )
        return False

    target_kind = decision['target']
    extension_id = str(decision.get('_extension_id') or '')
    if target_kind == 'core_action':
        return await dispatch_core_action(
            target,
            decision['action'],
            decision.get('params'),
            source=source,
            state=state,
            _visited=(*_visited, action_name),
        )
    if target_kind == 'extension_action':
        return await _redirect_to_extension_action(target, decision, extension_id)
    if target_kind == 'page':
        return await _redirect_to_page(target, decision, extension_id, action_name)
    if target_kind == 'route':
        return await _redirect_to_route(target, decision, extension_id, action_name)

    await _deny_action(target, '⚠️ Действие временно недоступно', show_alert=True)
    return False


async def apply_action_policy_previews(
    buttons: list[dict[str, Any]],
    target: CallbackQuery | Message,
    *,
    page_key: str,
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Apply optional policy labels without changing stored callback contracts."""
    if not ACTION_POLICIES:
        return buttons
    telegram_id = _target_telegram_id(target)
    if telegram_id is None:
        return buttons

    result: list[dict[str, Any]] = []
    for raw_button in buttons:
        button = dict(raw_button)
        resolved = _semantic_action_for_button(button, context)
        if resolved is None:
            result.append(button)
            continue
        action_name, params = resolved
        try:
            decision = await run_action_policies(
                action_name,
                params,
                telegram_id=telegram_id,
                source='button',
                phase='preview',
                bot=getattr(target, 'bot', None),
            )
        except Exception as exc:
            logger.warning(
                "Action policy preview skipped for page '%s', button '%s': %s",
                page_key,
                button.get('id'),
                exc,
            )
            result.append(button)
            continue
        if decision.get('label'):
            button['label'] = decision['label']
        result.append(button)
    return result


def _semantic_action_for_button(
    button: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    action_type = button.get('action_type', 'internal')
    action_name: str | None = None
    if action_type == 'internal':
        action_name = _INTERNAL_BUTTON_ACTIONS.get(button.get('action_value'))
    elif action_type == 'system':
        action_name = _SYSTEM_BUTTON_ACTIONS.get(button.get('id'))
    if action_name is None:
        return None
    params: dict[str, Any] = {}
    if action_name.startswith('key.') and action_name != 'key.purchase.start':
        key_id = context.get('key_id')
        if isinstance(key_id, bool) or not isinstance(key_id, int) or key_id <= 0:
            return None
        params['key_id'] = key_id
    return action_name, params


async def _redirect_to_extension_action(
    target: CallbackQuery | Message,
    decision: Mapping[str, Any],
    extension_id: str,
) -> bool:
    from bot.utils.extension_callbacks import (
        EXTENSION_CALLBACK_HANDLERS,
        build_extension_callback_data,
        dispatch_extension_callback,
    )

    action_name = str(decision['action'])
    action_key = f'{extension_id}.{action_name}'
    if not extension_id or action_key not in EXTENSION_CALLBACK_HANDLERS:
        await _deny_action(target, '⚠️ Действие расширения недоступно', show_alert=True)
        return False
    payload = decision.get('payload')
    callback_data = build_extension_callback_data(extension_id, action_name, payload)
    result = await dispatch_extension_callback(
        {
            'extension_id': extension_id,
            'telegram_id': _target_telegram_id(target),
            'action_name': action_name,
            'action_key': action_key,
            'payload': payload or '',
            'callback_data': callback_data,
        },
        bot=getattr(target, 'bot', None),
    )
    return await _apply_extension_result(target, result, extension_id, action_name, payload or '')


async def _apply_extension_result(
    target: CallbackQuery | Message,
    result: Mapping[str, Any],
    extension_id: str,
    action_name: str,
    payload: str,
) -> bool:
    from bot.utils.extension_rendering import render_extension_page, render_extension_route

    render_context = {
        'telegram_id': _target_telegram_id(target),
        'extension_id': extension_id,
        'extension_action': action_name,
        'extension_payload': payload,
    }
    if isinstance(result.get('context'), Mapping):
        custom_context = dict(result['context'])
        custom_context.update(render_context)
        render_context = custom_context

    if result.get('page_key'):
        rendered, answered = await render_extension_page(
            target,
            str(result['page_key']),
            render_context,
            force_new_for_message=True,
        )
        if not answered:
            await _answer_extension_result(
                target,
                result,
                default_text=None if rendered else '⚠️ Страница недоступна',
            )
        return rendered
    if result.get('route_key'):
        rendered, answered = await render_extension_route(
            target,
            str(result['route_key']),
            render_context,
            force_new_for_message=True,
        )
        if not answered:
            await _answer_extension_result(
                target,
                result,
                default_text=None if rendered else '⚠️ Маршрут недоступен',
            )
        return rendered
    await _answer_extension_result(target, result, default_text=None)
    return True


async def _redirect_to_page(
    target: CallbackQuery | Message,
    decision: Mapping[str, Any],
    extension_id: str,
    source_action: str,
) -> bool:
    from bot.utils.extension_rendering import render_extension_page

    context = dict(decision.get('context') or {})
    context.update({
        'telegram_id': _target_telegram_id(target),
        'extension_id': extension_id,
        'semantic_action': source_action,
    })
    rendered, answered = await render_extension_page(
        target,
        str(decision['page_key']),
        context,
        force_new_for_message=True,
    )
    if not answered:
        await _answer_callback_if_needed(target, None if rendered else '⚠️ Страница недоступна')
    return rendered


async def _redirect_to_route(
    target: CallbackQuery | Message,
    decision: Mapping[str, Any],
    extension_id: str,
    source_action: str,
) -> bool:
    from bot.utils.extension_rendering import render_extension_route

    context = dict(decision.get('context') or {})
    context.update({
        'telegram_id': _target_telegram_id(target),
        'extension_id': extension_id,
        'semantic_action': source_action,
    })
    rendered, answered = await render_extension_route(
        target,
        str(decision['route_key']),
        context,
        force_new_for_message=True,
    )
    if not answered:
        await _answer_callback_if_needed(target, None if rendered else '⚠️ Маршрут недоступен')
    return rendered


async def _answer_extension_result(
    target: CallbackQuery | Message,
    result: Mapping[str, Any],
    *,
    default_text: str | None,
) -> None:
    text = result.get('answer_text')
    if text is None:
        text = default_text
    if isinstance(target, CallbackQuery):
        await target.answer(str(text) if text else None, show_alert=bool(result.get('show_alert', False)))
        return
    if text:
        from bot.utils.text import safe_edit_or_send

        await safe_edit_or_send(target, str(text), force_new=True)


async def _answer_callback_if_needed(target: CallbackQuery | Message, text: str | None) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=bool(text))


async def _deny_action(
    target: CallbackQuery | Message,
    message: str,
    *,
    show_alert: bool,
) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(message, show_alert=show_alert)
        return
    from bot.utils.text import safe_edit_or_send

    await safe_edit_or_send(target, message, force_new=True)


def _target_telegram_id(target: CallbackQuery | Message) -> int | None:
    user = getattr(target, 'from_user', None)
    value = getattr(user, 'id', None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


__all__ = [
    'CORE_ACTION_EXECUTORS',
    'MAX_ACTION_REDIRECT_DEPTH',
    'CoreActionRequest',
    'apply_action_policy_previews',
    'dispatch_core_action',
    'register_core_action_executor',
]
