"""Shared timeout, retry and timing policy for payment provider API calls."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp

logger = logging.getLogger(__name__)

PAYMENT_API_CONNECT_TIMEOUT_SECONDS = 5
PAYMENT_API_TOTAL_TIMEOUT_SECONDS = 15
PAYMENT_API_MAX_ATTEMPTS = 3
PAYMENT_API_RETRY_DELAYS_SECONDS = (1, 2)

T = TypeVar('T')


class PaymentApiError(RuntimeError):
    """Base error for classified payment provider failures."""


class PaymentApiTransientError(PaymentApiError):
    """Retryable provider failure, normally an HTTP 5xx response."""


class PaymentApiResponseError(PaymentApiError):
    """Non-retryable provider response or contract failure."""


class PaymentApiRateLimitError(PaymentApiResponseError):
    """Non-retryable immediate rate-limit response."""


class PaymentApiRetryExhausted(PaymentApiTransientError):
    """All allowed attempts ended in retryable failures."""


def payment_client_timeout() -> aiohttp.ClientTimeout:
    """Returns the explicit aiohttp timeout shared by built-in providers."""
    return aiohttp.ClientTimeout(
        total=PAYMENT_API_TOTAL_TIMEOUT_SECONDS,
        connect=PAYMENT_API_CONNECT_TIMEOUT_SECONDS,
        sock_connect=PAYMENT_API_CONNECT_TIMEOUT_SECONDS,
    )


def is_retryable_payment_error(error: BaseException) -> bool:
    """Returns whether a failed payment API attempt can be repeated safely."""
    if isinstance(error, PaymentApiResponseError):
        return False
    if isinstance(error, aiohttp.ClientResponseError):
        return int(error.status or 0) >= 500
    if isinstance(error, (aiohttp.InvalidURL, ValueError)):
        return False
    return isinstance(
        error,
        (
            PaymentApiTransientError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            TimeoutError,
        ),
    )


async def run_payment_api_operation(
    *,
    provider: str,
    operation: str,
    order_id: str | None,
    call: Callable[[], Awaitable[T]],
    retry: bool,
    max_attempts: int = PAYMENT_API_MAX_ATTEMPTS,
) -> T:
    """Runs one provider operation with bounded timing and optional retries."""
    attempts_allowed = max(1, int(max_attempts)) if retry else 1
    started = time.monotonic()
    last_error: BaseException | None = None

    for attempt in range(1, attempts_allowed + 1):
        attempt_started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                call(),
                timeout=PAYMENT_API_TOTAL_TIMEOUT_SECONDS,
            )
            total_duration = time.monotonic() - started
            logger.info(
                "%s: provider=%s operation=%s order=%s "
                "attempt=%s/%s duration=%.3fs total=%.3fs",
                (
                    'Payment API recovered after retry'
                    if attempt > 1
                    else 'Payment API success'
                ),
                provider,
                operation,
                order_id or '-',
                attempt,
                attempts_allowed,
                time.monotonic() - attempt_started,
                total_duration,
            )
            return result
        except Exception as error:
            last_error = error
            retryable = retry and is_retryable_payment_error(error)
            duration = time.monotonic() - attempt_started
            if isinstance(error, PaymentApiRateLimitError) or (
                isinstance(error, aiohttp.ClientResponseError)
                and int(error.status or 0) == 429
            ):
                logger.warning(
                    "Payment API rate-limited: provider=%s operation=%s order=%s "
                    "attempt=%s duration=%.3fs error=%s",
                    provider,
                    operation,
                    order_id or '-',
                    attempt,
                    duration,
                    error,
                )
                raise
            if not retryable or attempt >= attempts_allowed:
                if retryable:
                    logger.warning(
                        "Payment API attempts exhausted: provider=%s operation=%s "
                        "order=%s attempts=%s duration=%.3fs error=%s",
                        provider,
                        operation,
                        order_id or '-',
                        attempt,
                        time.monotonic() - started,
                        error,
                    )
                    raise PaymentApiRetryExhausted(str(error)) from error
                logger.error(
                    "Payment API failure: provider=%s operation=%s order=%s "
                    "attempt=%s duration=%.3fs error=%s",
                    provider,
                    operation,
                    order_id or '-',
                    attempt,
                    duration,
                    error,
                )
                raise

            delay = PAYMENT_API_RETRY_DELAYS_SECONDS[
                min(attempt - 1, len(PAYMENT_API_RETRY_DELAYS_SECONDS) - 1)
            ]
            logger.warning(
                "Payment API retry: provider=%s operation=%s order=%s "
                "attempt=%s/%s duration=%.3fs retry_in=%ss error=%s",
                provider,
                operation,
                order_id or '-',
                attempt,
                attempts_allowed,
                duration,
                delay,
                error,
            )
            await asyncio.sleep(delay)

    raise PaymentApiRetryExhausted(str(last_error or 'Unknown payment API error'))


__all__ = [
    'PAYMENT_API_CONNECT_TIMEOUT_SECONDS',
    'PAYMENT_API_MAX_ATTEMPTS',
    'PAYMENT_API_RETRY_DELAYS_SECONDS',
    'PAYMENT_API_TOTAL_TIMEOUT_SECONDS',
    'PaymentApiError',
    'PaymentApiRateLimitError',
    'PaymentApiResponseError',
    'PaymentApiRetryExhausted',
    'PaymentApiTransientError',
    'is_retryable_payment_error',
    'payment_client_timeout',
    'run_payment_api_operation',
]
