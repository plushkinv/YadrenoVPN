"""Bounded background polling and completion for API payment providers."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from database.requests import (
    cancel_pending_order,
    find_order_by_order_id,
    get_due_payment_auto_checks,
    get_payment_auto_check,
    record_payment_auto_check_attempt,
    record_payment_completion_attempt,
    update_payment_auto_check,
)

logger = logging.getLogger(__name__)

AUTO_CHECK_OFFSETS_SECONDS = (120, 300, 600, 1200, 1800)
AUTO_CHECK_MAX_ATTEMPTS = len(AUTO_CHECK_OFFSETS_SECONDS)
AUTO_CHECK_MAX_AGE_SECONDS = AUTO_CHECK_OFFSETS_SECONDS[-1]
AUTO_CHECK_BATCH_LIMIT = 10
AUTO_CHECK_CONCURRENCY = 3
COMPLETION_RETRY_DELAYS_SECONDS = (60, 120)
COMPLETION_MAX_ATTEMPTS = 3

_BUILTIN_CHECKS = {
    'yookassa_qr': ('yookassa_payment_id', 'check_yookassa_payment_status'),
    'wata': ('wata_link_id', 'check_wata_payment_status'),
    'platega': ('platega_transaction_id', 'check_platega_payment_status'),
    'cardlink': ('cardlink_bill_id', 'check_cardlink_payment_status'),
}


async def auto_check_payment_orders(
    *,
    bot: Any,
    limit: int = AUTO_CHECK_BATCH_LIMIT,
    concurrency: int = AUTO_CHECK_CONCURRENCY,
) -> dict[str, int]:
    """Checks one bounded batch with a hard concurrency cap."""
    rows = get_due_payment_auto_checks(limit=min(max(1, int(limit)), AUTO_CHECK_BATCH_LIMIT))
    summary = {
        'queued': len(rows),
        'checked': 0,
        'pending': 0,
        'completed': 0,
        'canceled': 0,
        'exhausted': 0,
        'errors': 0,
    }
    semaphore = asyncio.Semaphore(min(max(1, int(concurrency)), AUTO_CHECK_CONCURRENCY))

    async def _run(row: Mapping[str, Any]) -> None:
        async with semaphore:
            if row.get('state') == 'active':
                summary['checked'] += 1
            outcome = await _process_due_row(bot, row)
            summary[outcome] = summary.get(outcome, 0) + 1

    await asyncio.gather(*(_run(row) for row in rows))
    return summary


async def _process_due_row(bot: Any, row: Mapping[str, Any]) -> str:
    order_id = str(row.get('order_id') or '')
    if not order_id:
        return 'errors'
    if row.get('state') == 'provider_succeeded':
        return await _complete_confirmed_payment(bot, row)

    minimum_interval = _custom_minimum_interval(row)
    if (
        int(row.get('check_attempts') or 0) == 0
        and minimum_interval is not None
        and minimum_interval > AUTO_CHECK_MAX_AGE_SECONDS
    ):
        logger.warning(
            "Автопроверка custom payment отключена общим лимитом: "
            "provider=%s order=%s interval=%ss max_age=%ss",
            row.get('provider_id'),
            order_id,
            minimum_interval,
            AUTO_CHECK_MAX_AGE_SECONDS,
        )
        update_payment_auto_check(order_id, state='exhausted')
        return 'exhausted'

    attempt_no = int(row.get('check_attempts') or 0) + 1
    record_payment_auto_check_attempt(order_id)
    try:
        status = await _check_provider_status(row)
    except Exception as error:
        logger.warning(
            "Автопроверка платежа не выполнена: provider=%s order=%s check=%s/%s error=%s",
            row.get('provider_id'),
            order_id,
            attempt_no,
            AUTO_CHECK_MAX_ATTEMPTS,
            error,
        )
        if attempt_no >= AUTO_CHECK_MAX_ATTEMPTS:
            update_payment_auto_check(
                order_id,
                state='exhausted',
                last_error=str(error)[:500],
            )
            return 'exhausted'
        delay = _next_check_delay(row, attempt_no)
        if delay is None:
            update_payment_auto_check(
                order_id,
                state='exhausted',
                last_error=str(error)[:500],
            )
            return 'exhausted'
        update_payment_auto_check(
            order_id,
            state='active',
            next_delay_seconds=delay,
            last_error=str(error)[:500],
        )
        return 'errors'

    if status == 'succeeded':
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )
        current = get_payment_auto_check(order_id) or dict(row)
        return await _complete_confirmed_payment(bot, current)

    if status == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        return 'canceled'

    if attempt_no >= AUTO_CHECK_MAX_ATTEMPTS:
        logger.warning(
            "Автопроверки платежа исчерпаны: provider=%s order=%s checks=%s",
            row.get('provider_id'),
            order_id,
            attempt_no,
        )
        update_payment_auto_check(order_id, state='exhausted')
        return 'exhausted'

    delay = _next_check_delay(row, attempt_no)
    if delay is None:
        update_payment_auto_check(order_id, state='exhausted')
        return 'exhausted'
    update_payment_auto_check(
        order_id,
        state='active',
        next_delay_seconds=delay,
    )
    return 'pending'


async def _check_provider_status(row: Mapping[str, Any]) -> str:
    payment_type = str(row.get('payment_type') or '')
    order_id = str(row.get('order_id') or '')
    builtin = _BUILTIN_CHECKS.get(payment_type)
    if builtin:
        from bot.services import billing

        field_name, function_name = builtin
        external_id = row.get(field_name)
        if not external_id:
            raise ValueError(f'В ордере отсутствует {field_name}')
        check_function = getattr(billing, function_name)
        return str(await check_function(str(external_id), order_id=order_id))

    from bot.services.custom_payments import check_custom_payment_order

    order = find_order_by_order_id(order_id)
    if not order:
        raise ValueError('Ордер не найден')
    result = await check_custom_payment_order(str(row.get('provider_id') or ''), order)
    return str(result.get('status') or 'pending')


def _next_check_delay(row: Mapping[str, Any], completed_attempts: int) -> int | None:
    if completed_attempts >= AUTO_CHECK_MAX_ATTEMPTS:
        return None
    started_at = _parse_timestamp(row.get('started_at'))
    now = datetime.now(timezone.utc)
    if started_at is None:
        base_delay = (
            AUTO_CHECK_OFFSETS_SECONDS[completed_attempts]
            - AUTO_CHECK_OFFSETS_SECONDS[completed_attempts - 1]
        )
    else:
        target = started_at + timedelta(
            seconds=AUTO_CHECK_OFFSETS_SECONDS[completed_attempts]
        )
        base_delay = max(0, math.ceil((target - now).total_seconds()))
    interval = _custom_minimum_interval(row)
    delay = max(base_delay, interval or 0)
    if started_at is not None:
        deadline = started_at + timedelta(seconds=AUTO_CHECK_MAX_AGE_SECONDS)
        if now + timedelta(seconds=delay) > deadline:
            return None
    return delay


def _custom_minimum_interval(row: Mapping[str, Any]) -> int | None:
    payment_type = str(row.get('payment_type') or '')
    if not payment_type.startswith('ext_'):
        return None
    from bot.utils.payment_provider_registry import get_payment_provider

    try:
        provider = get_payment_provider(str(row.get('provider_id') or ''))
    except ValueError:
        return None
    if provider is None or not provider.auto_check_interval_seconds:
        return None
    return int(provider.auto_check_interval_seconds)


async def _complete_confirmed_payment(bot: Any, row: Mapping[str, Any]) -> str:
    order_id = str(row.get('order_id') or '')
    completed_before = int(row.get('completion_attempts') or 0)
    if completed_before >= COMPLETION_MAX_ATTEMPTS:
        update_payment_auto_check(order_id, state='completion_failed')
        return 'errors'

    attempt_no = record_payment_completion_attempt(order_id)
    try:
        from bot.services.billing import complete_payment_order_background

        result = await complete_payment_order_background(
            order_id,
            bot=bot,
            notify_user=True,
            retry_post_actions=(
                attempt_no > 1 or str(row.get('order_status') or '') == 'paid'
            ),
        )
        if not result.get('ok'):
            raise RuntimeError(str(result.get('text') or 'Платёж не завершён'))
    except Exception as error:
        if attempt_no >= COMPLETION_MAX_ATTEMPTS:
            logger.error(
                "Не удалось завершить подтверждённый платёж: provider=%s order=%s attempts=%s error=%s",
                row.get('provider_id'),
                order_id,
                attempt_no,
                error,
                exc_info=True,
            )
            update_payment_auto_check(
                order_id,
                state='completion_failed',
                last_error=str(error)[:500],
            )
            await _notify_admins_completion_failure(bot, row, error)
        else:
            delay = COMPLETION_RETRY_DELAYS_SECONDS[attempt_no - 1]
            logger.warning(
                "Повтор завершения подтверждённого платежа: provider=%s order=%s "
                "attempt=%s/%s retry_in=%ss error=%s",
                row.get('provider_id'),
                order_id,
                attempt_no,
                COMPLETION_MAX_ATTEMPTS,
                delay,
                error,
            )
            update_payment_auto_check(
                order_id,
                state='provider_succeeded',
                next_delay_seconds=delay,
                last_error=str(error)[:500],
            )
        return 'errors'

    update_payment_auto_check(order_id, state='completed')
    return 'completed'


async def _notify_admins_completion_failure(
    bot: Any,
    row: Mapping[str, Any],
    error: Exception,
) -> None:
    from config import ADMIN_IDS
    from bot.utils.text import escape_html

    text = (
        '❌ <b>Не завершён подтверждённый платёж</b>\n\n'
        f"Провайдер: <code>{escape_html(str(row.get('provider_id') or '-'))}</code>\n"
        f"Ордер: <code>{escape_html(str(row.get('order_id') or '-'))}</code>\n"
        f"Ошибка: {escape_html(str(error))}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode='HTML')
        except Exception as notify_error:
            logger.warning(
                "Не удалось уведомить администратора %s о сбое order=%s: %s",
                admin_id,
                row.get('order_id'),
                notify_error,
            )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace(' ', 'T'))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def run_payment_auto_check_scheduler(bot: Any) -> None:
    """Runs the bounded payment polling queue once per minute."""
    logger.info("Scheduler автопроверки API-платежей запущен")
    while True:
        try:
            summary = await auto_check_payment_orders(bot=bot)
            if summary['queued']:
                logger.info("Автопроверка API-платежей: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Ошибка scheduler автопроверки API-платежей: %s", error, exc_info=True)
        await asyncio.sleep(60)


__all__ = [
    'AUTO_CHECK_BATCH_LIMIT',
    'AUTO_CHECK_CONCURRENCY',
    'AUTO_CHECK_MAX_ATTEMPTS',
    'AUTO_CHECK_OFFSETS_SECONDS',
    'auto_check_payment_orders',
    'run_payment_auto_check_scheduler',
]
