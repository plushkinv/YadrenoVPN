"""Minute-worker execution of durable one-off extension tasks."""
from __future__ import annotations

import json

from bot.utils.action_origin_context import normalize_origin_payload
from bot.utils.extension_background import (
    HANDLER_TIMEOUT_SECONDS, run_background_attempt, run_background_batch,
)
from database.db_extension_payments import iso_utc
from database.requests import claim_extension_task, finish_extension_task, get_due_extension_task_ids

TASK_HANDLER_TIMEOUT_SECONDS = HANDLER_TIMEOUT_SECONDS


async def _deliver(task_id: int, *, bot=None) -> str:
    job = claim_extension_task(task_id)
    if job is None:
        return 'skipped'
    try:
        if job['contract_version'] != 1 or not isinstance(job['task_id'], str):
            raise ValueError('unsupported task snapshot')
        job['payload'] = normalize_origin_payload(json.loads(job['payload']))
        for field in ('created_at', 'run_at'):
            job[field] = iso_utc(job[field])
            if job[field] is None:
                raise ValueError('invalid task timestamp')
    except (ValueError, TypeError):
        state, error, retry_seconds = 'degraded', 'invalid_snapshot', 0
    else:
        from bot.utils.extension_task_registry import dispatch_extension_task

        state, error, retry_seconds = await run_background_attempt(
            dispatch_extension_task(job, bot=bot), attempts=int(job['attempts']),
            timeout=TASK_HANDLER_TIMEOUT_SECONDS, identity=job['task_id'],
        )
    if not finish_extension_task(
        task_id, job['claim_token'], state=state,
        error_code=error, retry_seconds=retry_seconds,
    ):
        return 'skipped'
    return state


async def process_due_extension_tasks(*, bot=None, limit: int = 100) -> dict[str, int]:
    ids = get_due_extension_task_ids(limit)
    return await run_background_batch(ids, lambda value: _deliver(value, bot=bot))
