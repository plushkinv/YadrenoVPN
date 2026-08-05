"""Daily delivery of win-back coupons after all user keys have expired."""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from aiogram import Bot

from bot.utils.delivery import is_bot_blocked_error
from bot.utils.page_renderer import PreparedPageRender, prepare_page_render
from bot.utils.text import send_media_or_text
from database.requests import (
    cancel_ineligible_lapsed_coupon_deliveries,
    discover_lapsed_coupon_episodes,
    ensure_lapsed_coupon_for_delivery,
    get_lapsed_coupon_enabled,
    list_due_lapsed_coupon_deliveries,
    mark_lapsed_coupon_delivery_failed,
    mark_lapsed_coupon_delivery_retry,
    mark_lapsed_coupon_delivery_sent,
    mark_user_bot_blocked,
)

logger = logging.getLogger(__name__)

LAPSED_KEY_COUPON_PAGE_KEY = "lapsed_key_coupon"
DELIVERY_ATTEMPTS_PER_CYCLE = 3
DELIVERY_RETRY_BASE_SECONDS = 1.0


@dataclass
class LapsedCouponDeliveryReport:
    """Summary of one daily win-back coupon pass."""

    enabled: bool = False
    discovered: int = 0
    canceled: int = 0
    due: int = 0
    sent: int = 0
    deferred: int = 0
    failed: int = 0
    blocked: int = 0


def _format_coupon_expiry(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid lapsed coupon expiry value: %r", raw)
        return ""
    return parsed.strftime("%d.%m.%Y")


def _coupon_page_context(coupon: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "promo_code": str(coupon.get("code") or ""),
        "promo_discount": int(coupon.get("discount_percent") or 0),
        "promo_expires_at": _format_coupon_expiry(coupon.get("expires_at")),
    }


async def _deliver_coupon(
    bot: Bot,
    coupon: Mapping[str, Any],
) -> tuple[str, int, Optional[Exception]]:
    """Try one Telegram delivery cycle and return its stable result code."""
    delivery_id = int(coupon["delivery_id"])
    telegram_id = int(coupon["telegram_id"])
    context = {
        'telegram_id': telegram_id,
        **_coupon_page_context(coupon),
    }
    last_error: Optional[Exception] = None

    try:
        prepared = await prepare_page_render(
            bot,
            LAPSED_KEY_COUPON_PAGE_KEY,
            context=context,
        )
    except Exception as exc:
        prepared = None
        last_error = exc
    if not isinstance(prepared, PreparedPageRender):
        last_error = last_error or RuntimeError('page_flow_denied')
        mark_lapsed_coupon_delivery_retry(
            delivery_id,
            str(last_error),
            attempts=0,
        )
        return 'deferred', 0, last_error

    for attempt_index in range(DELIVERY_ATTEMPTS_PER_CYCLE):
        try:
            await send_media_or_text(
                bot,
                chat_id=telegram_id,
                text=prepared.text,
                media=prepared.media,
                media_type=prepared.media_type,
                reply_markup=prepared.reply_markup,
            )
            attempts = attempt_index + 1
            mark_lapsed_coupon_delivery_sent(
                delivery_id,
                attempts=attempts,
            )
            return "sent", attempts, None
        except Exception as exc:
            last_error = exc
            attempts = attempt_index + 1
            if is_bot_blocked_error(exc):
                mark_user_bot_blocked(telegram_id)
                mark_lapsed_coupon_delivery_failed(
                    delivery_id,
                    "bot_blocked",
                    attempts=attempts,
                )
                return "blocked", attempts, exc
            if attempt_index + 1 < DELIVERY_ATTEMPTS_PER_CYCLE:
                await asyncio.sleep(
                    DELIVERY_RETRY_BASE_SECONDS * (2**attempt_index)
                )

    mark_lapsed_coupon_delivery_retry(
        delivery_id,
        str(last_error or "telegram_delivery_failed"),
        attempts=DELIVERY_ATTEMPTS_PER_CYCLE,
    )
    return "deferred", DELIVERY_ATTEMPTS_PER_CYCLE, last_error


async def process_lapsed_coupon_deliveries(
    bot: Bot,
) -> LapsedCouponDeliveryReport:
    """Discover, validate and deliver all due lapsed-user coupons."""
    report = LapsedCouponDeliveryReport(
        enabled=get_lapsed_coupon_enabled(),
    )
    if not report.enabled:
        logger.info("Lapsed-user automatic coupons are disabled")
        return report

    report.discovered = discover_lapsed_coupon_episodes()
    report.canceled = cancel_ineligible_lapsed_coupon_deliveries()
    due = list_due_lapsed_coupon_deliveries()
    report.due = len(due)
    if not due:
        logger.info(
            "Lapsed-user coupon pass completed: discovered=%s canceled=%s due=0",
            report.discovered,
            report.canceled,
        )
        return report

    for delivery in due:
        delivery_id = int(delivery["id"])
        try:
            coupon = ensure_lapsed_coupon_for_delivery(delivery_id)
            if coupon is None:
                current = delivery.get("coupon_id")
                if current:
                    report.failed += 1
                continue
            result, _attempts, error = await _deliver_coupon(
                bot,
                coupon,
            )
            if result == "sent":
                report.sent += 1
            elif result == "blocked":
                report.failed += 1
                report.blocked += 1
            else:
                report.deferred += 1
            if error is not None:
                logger.warning(
                    "Lapsed-user coupon delivery %s for telegram_id=%s: %s",
                    result,
                    coupon.get("telegram_id"),
                    error,
                )
        except Exception as exc:
            report.deferred += 1
            logger.exception(
                "Lapsed-user coupon delivery failed for delivery_id=%s: %s",
                delivery_id,
                exc,
            )

    logger.info(
        "Lapsed-user coupon pass completed: discovered=%s canceled=%s "
        "due=%s sent=%s deferred=%s failed=%s blocked=%s",
        report.discovered,
        report.canceled,
        report.due,
        report.sent,
        report.deferred,
        report.failed,
        report.blocked,
    )
    return report


__all__ = [
    "DELIVERY_ATTEMPTS_PER_CYCLE",
    "DELIVERY_RETRY_BASE_SECONDS",
    "LAPSED_KEY_COUPON_PAGE_KEY",
    "LapsedCouponDeliveryReport",
    "process_lapsed_coupon_deliveries",
]
