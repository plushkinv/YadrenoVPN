"""Namespaced handlers for persisted one-off extension tasks."""
from __future__ import annotations

from typing import Callable

from bot.utils.action_origin_context import normalize_completion_handler_name
from bot.utils.extension_background import invoke_background_handler
from database.db_extensions import normalize_extension_id

EXTENSION_TASK_HANDLERS: dict[str, dict] = {}


def register_extension_task_handler(
    extension_id: str, name: str, handler: Callable, *, replace: bool = False,
) -> str:
    extension_id = normalize_extension_id(extension_id)
    name = normalize_completion_handler_name(name)
    if not callable(handler) or not isinstance(replace, bool):
        raise ValueError('handler must be callable and replace must be bool')
    key = f'{extension_id}.{name}'
    if key in EXTENSION_TASK_HANDLERS and not replace:
        raise ValueError('task handler is already registered')
    EXTENSION_TASK_HANDLERS[key] = {
        'extension_id': extension_id, 'handler_name': name, 'handler': handler,
    }
    return key


async def dispatch_extension_task(job: dict, *, bot=None) -> dict:
    registration = EXTENSION_TASK_HANDLERS.get(f"{job['extension_id']}.{job['handler_name']}")
    if registration is None:
        raise LookupError('task handler is unavailable')
    context = {key: job[key] for key in (
        'task_id', 'created_at', 'run_at', 'user_id', 'telegram_id', 'payload',
    )}
    context.update(contract_version=1, delivery_attempt=int(job['attempts']))
    return await invoke_background_handler(registration['handler'], context, bot=bot)
