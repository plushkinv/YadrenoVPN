"""Check-only Telegram compatibility for already-created legacy payments."""
from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.user.payments.status_page import answer_payment_status_notification
from bot.utils.callbacks import safe_answer_callback
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page

logger = logging.getLogger(__name__)
router = Router()


async def check_legacy_link_payment(
    message,
    state: FSMContext,
    *,
    order_id: str,
    telegram_id: int,
    payment_type: str,
    payment_id_field: str,
    check_func: Callable[..., Awaitable[str]],
    check_arg_is_order_id: bool = False,
    rate_limit_seconds: int = 0,
    rate_limit_prefix: str = '',
    callback: CallbackQuery | None = None,
    referral_override_func: Callable[[dict, FSMContext], Awaitable[int]] | None = None,
) -> None:
    """Check and settle one existing v0 link-based payment."""
    from bot.services.payment_completion import complete_confirmed_payment
    from database.requests import (
        cancel_pending_order,
        find_order_by_order_id,
        get_or_create_user,
        is_order_already_paid,
        update_payment_auto_check,
        update_payment_type,
    )

    async def show_order_unavailable() -> None:
        await render_page(
            callback or message,
            'payment_order_unavailable',
            force_new=callback is None,
        )
        if callback:
            await safe_answer_callback(callback)

    order = find_order_by_order_id(order_id)
    if not order:
        await show_order_unavailable()
        return

    current_user, _ = get_or_create_user(telegram_id)
    owner_user_id = int(current_user['id'])
    if int(order.get('user_id') or 0) != owner_user_id:
        logger.warning(
            'Rejected foreign legacy payment check order=%s telegram_id=%s owner=%s',
            order_id,
            telegram_id,
            order.get('user_id'),
        )
        await show_order_unavailable()
        return

    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await complete_confirmed_payment(
            order_id,
            bot=message.bot,
            target=message,
            state=state,
            telegram_id=telegram_id,
            payment_type=payment_type,
        )
        if callback:
            await safe_answer_callback(callback)
        return

    payment_id = order.get(payment_id_field)
    if not payment_id:
        await show_order_unavailable()
        return

    if rate_limit_seconds > 0:
        state_data = await state.get_data()
        last_check_key = f'{rate_limit_prefix}_last_check_{order_id}'
        last_check = state_data.get(last_check_key, 0)
        now = time.time()
        elapsed = now - last_check
        if last_check and elapsed < rate_limit_seconds:
            wait = int(rate_limit_seconds - elapsed)
            if callback:
                await answer_payment_status_notification(
                    callback,
                    'payment_check_wait',
                    payment_wait_seconds=wait,
                )
            else:
                await render_page(
                    message,
                    'payment_check_wait',
                    context={'payment_wait_seconds': wait},
                    force_new=True,
                )
            return
        await state.update_data({last_check_key: now})

    try:
        check_arg = order_id if check_arg_is_order_id else payment_id
        check_kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(check_func)
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_kwargs or 'order_id' in signature.parameters:
                check_kwargs['order_id'] = order_id
        except (TypeError, ValueError):
            pass
        status = await check_func(check_arg, **check_kwargs)
    except Exception as error:
        logger.error(
            'Legacy payment check failed type=%s order=%s: %s',
            payment_type,
            order_id,
            error,
        )
        if callback:
            await safe_answer_callback(callback)
        await render_page(callback or message, 'payment_failed', force_new=True)
        return

    if status == 'succeeded':
        if callback:
            await safe_answer_callback(callback)
        update_payment_type(order_id, payment_type)
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )
        if referral_override_func:
            referral_amount = await referral_override_func(order, state)
        elif order.get('final_amount_cents') is not None:
            referral_amount = int(order.get('final_amount_cents') or 0)
        else:
            from database.requests import get_tariff_by_id

            tariff = get_tariff_by_id(order.get('tariff_id'))
            referral_amount = (
                int(float(tariff.get('price_rub') or 0) * 100)
                if tariff
                else 0
            )
        logger.info(
            'Legacy payment referral type=%s order=%s amount=%s',
            payment_type,
            order_id,
            referral_amount,
        )
        try:
            await message.delete()
        except Exception:
            pass
        await complete_confirmed_payment(
            order_id,
            bot=message.bot,
            target=message,
            state=state,
            telegram_id=telegram_id,
            payment_type=payment_type,
            referral_amount=referral_amount,
        )
        return

    if status == 'canceled':
        if callback:
            await safe_answer_callback(callback)
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        await render_page(callback or message, 'payment_canceled', force_new=True)
        return

    if callback:
        await answer_payment_status_notification(
            callback,
            'payment_pending',
            order_id=order_id,
        )
    else:
        await render_page(message, 'payment_pending', force_new=True)


async def _yookassa_referral_amount(order: dict, state: FSMContext) -> int:
    """Return the paid QR portion used by historical referral accounting."""
    state_data = await state.get_data()
    remaining_cents = int(state_data.get('remaining_cents') or 0)
    if remaining_cents > 0:
        return remaining_cents
    if order.get('final_amount_cents') is not None:
        return int(order.get('final_amount_cents') or 0)
    from database.requests import get_tariff_by_id

    tariff = get_tariff_by_id(order.get('tariff_id'))
    return int(float(tariff.get('price_rub') or 0) * 100) if tariff else 0


async def _run_yookassa_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    from bot.services.billing import check_yookassa_payment_status

    await check_legacy_link_payment(
        message,
        state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type='yookassa_qr',
        payment_id_field='yookassa_payment_id',
        check_func=check_yookassa_payment_status,
        callback=callback,
        referral_override_func=_yookassa_referral_amount,
    )


async def _run_wata_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    from bot.services.billing import check_wata_payment_status

    await check_legacy_link_payment(
        message,
        state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type='wata',
        payment_id_field='wata_link_id',
        check_func=check_wata_payment_status,
        rate_limit_seconds=30,
        rate_limit_prefix='wata',
        callback=callback,
    )


async def _run_platega_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    from bot.services.billing import check_platega_payment_status

    await check_legacy_link_payment(
        message,
        state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type='platega',
        payment_id_field='platega_transaction_id',
        check_func=check_platega_payment_status,
        rate_limit_seconds=10,
        rate_limit_prefix='platega',
        callback=callback,
    )


async def _run_cardlink_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    from bot.services.billing import check_cardlink_payment_status

    await check_legacy_link_payment(
        message,
        state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type='cardlink',
        payment_id_field='cardlink_bill_id',
        check_func=check_cardlink_payment_status,
        rate_limit_seconds=10,
        rate_limit_prefix='cardlink',
        callback=callback,
    )


_LEGACY_PROVIDER_CHECKS = {
    'yookassa': _run_yookassa_check,
    'wata': _run_wata_check,
    'platega': _run_platega_check,
    'cardlink': _run_cardlink_check,
}


async def run_legacy_provider_check(
    provider: str,
    message,
    state: FSMContext,
    *,
    order_id: str,
    telegram_id: int,
    callback: CallbackQuery | None,
) -> None:
    """Dispatch a known legacy link-provider check without creating orders."""
    check = _LEGACY_PROVIDER_CHECKS.get(str(provider))
    if check is None:
        await render_page(
            callback or message,
            'payment_order_unavailable',
            force_new=callback is None,
        )
        if callback:
            await safe_answer_callback(callback)
        return
    await check(
        message,
        state,
        order_id=order_id,
        telegram_id=telegram_id,
        callback=callback,
    )


@router.callback_query(F.data.startswith('check_yookassa_qr:'))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await _run_yookassa_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


@router.callback_query(F.data.startswith('check_wata:'))
async def check_wata_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await _run_wata_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


@router.callback_query(F.data.startswith('check_platega:'))
async def check_platega_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await _run_platega_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


@router.callback_query(F.data.startswith('check_cardlink:'))
async def check_cardlink_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await _run_cardlink_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _render_callback_page(callback: CallbackQuery, page_key: str, **context) -> None:
    await render_page(
        callback,
        page_key,
        context=build_page_flow_context(
            callback,
            telegram_id=callback.from_user.id,
            **context,
        ),
    )
    await safe_answer_callback(callback)


@router.callback_query(F.data.startswith('check_ext:'))
async def custom_payment_check(callback: CallbackQuery, state: FSMContext) -> None:
    """Check an already-created v0 extension-provider order."""
    order_id = callback.data.split(':', 1)[1]
    from bot.services.payment_completion import complete_confirmed_payment
    from bot.services.custom_payments import check_custom_payment_order
    from database.requests import (
        cancel_pending_order,
        find_order_by_order_id,
        get_or_create_user,
        get_payment_provider_order,
        is_order_already_paid,
        update_payment_auto_check,
    )

    order = find_order_by_order_id(order_id)
    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    if not order or int(order.get('user_id') or 0) != int(user['id']):
        await _render_callback_page(callback, 'payment_order_unavailable')
        return
    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await complete_confirmed_payment(
            order_id,
            bot=callback.bot,
            target=callback.message,
            state=state,
            telegram_id=callback.from_user.id,
            payment_type=str(order.get('payment_type') or ''),
        )
        await safe_answer_callback(callback)
        return

    provider_order = get_payment_provider_order(order_id)
    if not provider_order:
        await _render_callback_page(callback, 'payment_order_unavailable')
        return
    if provider_order.get('status') == 'succeeded':
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )
        await _complete_custom_payment_flow(callback, state, order, provider_order)
        return
    if provider_order.get('status') == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        await _render_callback_page(callback, 'payment_canceled', order_id=order_id)
        return

    try:
        result = await check_custom_payment_order(provider_order['provider_id'], order)
    except Exception as error:
        logger.warning('Custom legacy payment check failed order=%s: %s', order_id, error)
        await _render_callback_page(callback, 'payment_failed', order_id=order_id)
        return
    if result['status'] == 'succeeded':
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )
        await _complete_custom_payment_flow(callback, state, order, provider_order)
        return
    if result['status'] == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        await _render_callback_page(callback, 'payment_canceled', order_id=order_id)
        return
    await answer_payment_status_notification(
        callback,
        'payment_pending',
        order_id=order_id,
    )


async def _complete_custom_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
    order: dict,
    provider_order: dict,
) -> None:
    from bot.services.payment_completion import complete_confirmed_payment

    order_id = str(order.get('order_id') or '')
    await complete_confirmed_payment(
        order_id,
        bot=callback.bot,
        target=callback.message,
        state=state,
        telegram_id=callback.from_user.id,
        payment_type=str(order.get('payment_type') or provider_order.get('payment_type')),
        referral_amount=_custom_payment_referral_amount(order),
    )


def _custom_payment_referral_amount(order: dict) -> int:
    try:
        if order.get('final_amount_cents') is not None:
            return int(order.get('final_amount_cents') or 0)
        return int(order.get('amount_cents') or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    'check_legacy_link_payment',
    'custom_payment_check',
    'router',
    'run_legacy_provider_check',
]
