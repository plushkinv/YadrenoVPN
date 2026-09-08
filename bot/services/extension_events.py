"""Independent retry delivery of committed core events to local extensions."""
from __future__ import annotations

import json
import logging
from typing import Any

from bot.utils.extension_background import (
    HANDLER_TIMEOUT_SECONDS, run_background_attempt, run_background_batch,
)

from database.requests import (
    claim_core_event_delivery, finish_core_event_delivery, get_due_core_event_delivery_ids,
    record_key_delivery_event,
)

logger = logging.getLogger(__name__)
EVENT_HANDLER_TIMEOUT_SECONDS = HANDLER_TIMEOUT_SECONDS


async def _deliver(delivery_id: int, *, bot: Any) -> str:
    job = claim_core_event_delivery(delivery_id)
    if job is None:
        return 'skipped'
    retry_seconds = 0
    try:
        payload = json.loads(job['payload'])
        if not isinstance(payload, dict) or payload.get('contract_version') != 1:
            raise ValueError('unsupported event snapshot')
        if payload.get('event_id') != job['event_id'] or payload.get('event') != job['event_name']:
            raise ValueError('event identity mismatch')
        job['payload'] = payload
    except (ValueError, TypeError):
        state, error = 'degraded', 'invalid_snapshot'
    else:
        from bot.utils.extension_event_registry import dispatch_extension_event

        state, error, retry_seconds = await run_background_attempt(
            dispatch_extension_event(job, bot=bot), attempts=int(job['attempts']),
            timeout=EVENT_HANDLER_TIMEOUT_SECONDS, identity=f'event:{delivery_id}',
        )
    if not finish_core_event_delivery(
        delivery_id, job['claim_token'], state=state,
        error_code=error, retry_seconds=retry_seconds,
    ):
        return 'skipped'
    return state


async def notify_key_delivered(
    order_id: str, *, key_id: int, telegram_id: int, bot: Any,
) -> None:
    """Publish an already sent key and immediately attempt its subscribers."""
    try:
        from bot.utils.extension_event_registry import event_subscribers

        event_id = record_key_delivery_event(
            order_id, key_id=key_id, telegram_id=telegram_id,
            subscribers=event_subscribers('key.delivered'),
        )
        if event_id is not None:
            await process_due_extension_events(bot=bot, event_id=event_id)
    except Exception as exc:
        logger.warning('Key delivery event failed type=%s', type(exc).__name__)


async def process_due_extension_events(
    *, bot: Any = None, limit: int = 100, event_id: str | None = None,
) -> dict[str, int]:
    """Process a bounded batch; one broken handler cannot hold another's result."""
    ids = (
        get_due_core_event_delivery_ids(limit, event_id=event_id)
        if event_id is not None else get_due_core_event_delivery_ids(limit)
    )
    return await run_background_batch(ids, lambda value: _deliver(value, bot=bot))
