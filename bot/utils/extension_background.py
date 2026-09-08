"""Shared invocation, acknowledgement and retry rules for durable extensions."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bot.utils.extension_completion_registry import normalize_extension_completion_result

logger = logging.getLogger(__name__)
HANDLER_TIMEOUT_SECONDS = 12
_RETRY_SECONDS = (60, 300, 900, 3600, 21600, 86400)


async def invoke_background_handler(handler, context: dict[str, Any], *, bot=None):
    """Detach input and bind the application bot for sync and async handlers."""
    from bot.utils.custom_extensions import _extension_bot_context

    context = deepcopy(context)
    with _extension_bot_context(bot):
        if inspect.iscoroutinefunction(handler):
            result = handler(context)
        else:
            result = await asyncio.to_thread(handler, context)
        if inspect.isawaitable(result):
            result = await result
    if isinstance(result, Mapping):
        result = {'ok': False, **result}
    return normalize_extension_completion_result(result)


async def run_background_attempt(awaitable, *, attempts: int, timeout: float, identity: str):
    """Return a persisted outcome; cancellation leaves the lease reclaimable."""
    state, error = 'pending', None
    try:
        result = await asyncio.wait_for(awaitable, timeout)
        if result['ok']:
            state = 'completed'
        else:
            state = 'pending' if result['retry'] else 'degraded'
            error = 'handler_retry_requested' if result['retry'] else 'handler_rejected'
    except asyncio.TimeoutError:
        error = 'handler_timeout'
    except LookupError:
        error = 'handler_unavailable'
    except Exception as exc:
        error = 'handler_error'
        logger.warning('Extension delivery %s failed type=%s', identity, type(exc).__name__)
    delay = _RETRY_SECONDS[min(max(0, attempts - 1), len(_RETRY_SECONDS) - 1)]
    return state, error, delay if state == 'pending' else 0


async def run_background_batch(ids, deliver):
    """Isolate deliveries and bound concurrent work in one scheduler pass."""
    semaphore = asyncio.Semaphore(8)

    async def guarded(value):
        async with semaphore:
            return await deliver(value)

    outcomes = await asyncio.gather(*(guarded(value) for value in ids), return_exceptions=True)
    summary = {'queued': len(ids), 'completed': 0, 'pending': 0, 'degraded': 0, 'skipped': 0, 'errors': 0}
    for outcome in outcomes:
        summary['errors' if isinstance(outcome, BaseException) else outcome] += 1
    return summary
