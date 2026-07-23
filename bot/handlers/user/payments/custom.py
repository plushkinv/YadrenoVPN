"""Generic handlers for extension-owned payment providers."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.user.payments.tariff_select_page import show_provider_tariff_select_page
from bot.utils.callbacks import safe_answer_callback
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page

logger = logging.getLogger(__name__)
router = Router()


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


@router.callback_query(F.data.startswith('pe:'))
async def custom_payment_select_tariff(callback: CallbackQuery):
    """Show page-owned tariff rows for an enabled extension provider."""
    provider_id = callback.data.split(':', 1)[1]
    provider = _get_available_provider(provider_id, {'telegram_id': callback.from_user.id})
    if provider is None:
        await _render_callback_page(callback, 'payment_unavailable')
        return

    from database.requests import get_all_tariffs

    tariffs = _eligible_provider_tariffs(
        get_all_tariffs(include_hidden=False),
        getattr(provider, 'payment_type', f'ext_{provider.provider_id}'),
        getattr(provider, 'minimum_amount_minor', provider.minimum_amount_cents),
    )
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=provider.payment_type,
        callback_factory=lambda tariff_id: f'pet:{provider.provider_id}:{tariff_id}',
        back_callback='buy_key',
        minimum_amount=provider.minimum_amount_minor,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('re:'))
async def custom_payment_select_renew_tariff(callback: CallbackQuery):
    """Show page-owned renewal tariff rows for an extension provider."""
    parts = callback.data.split(':')
    try:
        provider_id = parts[1]
        key_id = int(parts[2])
    except (IndexError, ValueError):
        await _render_callback_page(callback, 'action_unavailable')
        return
    provider = _get_available_provider(
        provider_id,
        {'telegram_id': callback.from_user.id, 'key_id': key_id},
    )
    if provider is None:
        await _render_callback_page(callback, 'payment_unavailable')
        return

    from bot.utils.groups import get_tariffs_for_renewal
    from database.requests import get_key_details_for_user

    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await _render_callback_page(callback, 'key_not_found')
        return
    tariffs = _eligible_provider_tariffs(
        get_tariffs_for_renewal(key.get('tariff_id', 0)),
        getattr(provider, 'payment_type', f'ext_{provider.provider_id}'),
        getattr(provider, 'minimum_amount_minor', provider.minimum_amount_cents),
    )
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=provider.payment_type,
        callback_factory=lambda tariff_id: f'ret:{provider.provider_id}:{key_id}:{tariff_id}',
        back_callback=f'key_renew:{key_id}',
        key=key,
        minimum_amount=provider.minimum_amount_minor,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('pet:'))
async def custom_payment_create(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    try:
        provider_id = parts[1]
        tariff_id = int(parts[2])
    except (IndexError, ValueError):
        await _render_callback_page(callback, 'action_unavailable')
        return
    await _create_custom_payment(
        callback,
        state,
        provider_id=provider_id,
        tariff_id=tariff_id,
    )


@router.callback_query(F.data.startswith('ret:'))
async def custom_payment_create_renewal(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    try:
        provider_id = parts[1]
        key_id = int(parts[2])
        tariff_id = int(parts[3])
    except (IndexError, ValueError):
        await _render_callback_page(callback, 'action_unavailable')
        return
    await _create_custom_payment(
        callback,
        state,
        provider_id=provider_id,
        tariff_id=tariff_id,
        key_id=key_id,
    )


@router.callback_query(F.data.startswith('check_ext:'))
async def custom_payment_check(callback: CallbackQuery, state: FSMContext):
    """Check an extension provider order without exposing provider exceptions."""
    order_id = callback.data.split(':', 1)[1]
    from bot.handlers.user.payments.base import finalize_payment_ui
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
        await finalize_payment_ui(
            callback.message,
            state,
            '',
            order,
            user_id=callback.from_user.id,
        )
        await safe_answer_callback(callback)
        return

    provider_order = get_payment_provider_order(order_id)
    if not provider_order:
        await _render_callback_page(callback, 'payment_order_unavailable')
        return
    if provider_order.get('status') == 'succeeded':
        update_payment_auto_check(order_id, state='provider_succeeded', next_delay_seconds=0)
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
        logger.warning('Custom payment check failed order=%s: %s', order_id, error)
        await _render_callback_page(callback, 'payment_failed', order_id=order_id)
        return
    if result['status'] == 'succeeded':
        update_payment_auto_check(order_id, state='provider_succeeded', next_delay_seconds=0)
        await _complete_custom_payment_flow(callback, state, order, provider_order)
        return
    if result['status'] == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        await _render_callback_page(callback, 'payment_canceled', order_id=order_id)
        return
    await _render_callback_page(
        callback,
        'payment_pending',
        order_id=order_id,
        payment_check_callback=f'check_ext:{order_id}',
        payment_can_check=True,
    )


async def _create_custom_payment(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    provider_id: str,
    tariff_id: int,
    key_id: int | None = None,
) -> None:
    provider = _get_available_provider(
        provider_id,
        {'telegram_id': callback.from_user.id, 'key_id': key_id},
    )
    if provider is None:
        await _render_callback_page(callback, 'payment_unavailable')
        return

    from bot.handlers.user.payments.base import (
        QR_PAYMENT_PAGE_KEY,
        build_qr_payment_page_context,
        complete_promo_free_payment,
    )
    from bot.services.custom_payments import create_custom_payment_order
    from bot.services.promotions import describe_quote_lines, format_amount
    from database.requests import get_key_details_for_user, get_or_create_user, get_tariff_by_id

    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id) if key_id else None
    if not tariff or (key_id and not key):
        await _render_callback_page(callback, 'payment_order_unavailable')
        return

    await safe_answer_callback(callback)
    await render_page(callback, 'payment_creating')
    bot_info = await callback.bot.get_me()
    try:
        result = await create_custom_payment_order(
            provider.provider_id,
            user_id=int(user['id']),
            telegram_id=callback.from_user.id,
            tariff=tariff,
            action='renewal' if key_id else 'new_key',
            vpn_key_id=key_id,
            key=key,
            bot_username=bot_info.username,
        )
    except Exception as error:
        logger.warning(
            'Custom payment creation failed provider=%s tariff=%s: %s',
            provider.provider_id,
            tariff_id,
            error,
        )
        await render_page(callback, 'payment_failed')
        return
    if not result.get('ok'):
        logger.warning(
            'Custom payment unavailable provider=%s reason=%s',
            provider.provider_id,
            result.get('reason'),
        )
        await render_page(callback, 'payment_unavailable')
        return

    order_id = str(result['order_id'])
    quote = result['quote']
    if result.get('is_free'):
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        return
    payment_url = str(result['payment_url'])
    payment_context = build_qr_payment_page_context(
        title=provider.title,
        tariff_name=tariff['name'],
        price_str=format_amount(quote['final_amount'], provider.payment_type),
        days=int(tariff.get('duration_days') or 0),
        qr_url=payment_url,
        key_name=key['display_name'] if key else None,
        hint_text=None,
        instruction_text=None,
        promo_lines=describe_quote_lines(quote),
    )
    back_callback = f're:{provider.provider_id}:{key_id}' if key_id else f'pe:{provider.provider_id}'
    payment_context.update({
        'bot_username': bot_info.username,
        'order_id': order_id,
        'payment_check_callback': f'check_ext:{order_id}',
        'payment_methods_callback': back_callback,
        'payment_cancel_callback': back_callback,
        'payment_can_check': True,
    })
    await render_page(
        callback,
        page_key='payment_link_renewal' if key else QR_PAYMENT_PAGE_KEY,
        context=build_page_flow_context(callback, **payment_context),
        force_new=True,
        media_policy='runtime',
    )


def _get_available_provider(provider_id: str, context: dict | None = None):
    from bot.utils.payment_provider_registry import get_payment_provider, is_payment_provider_enabled

    try:
        provider = get_payment_provider(provider_id)
    except ValueError:
        return None
    if provider is None or not is_payment_provider_enabled(provider.provider_id, context or {}):
        return None
    return provider


async def _complete_custom_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
    order: dict,
    provider_order: dict,
) -> None:
    from bot.services.billing import complete_payment_flow
    from database.requests import find_order_by_order_id, update_payment_auto_check

    order_id = str(order.get('order_id') or '')
    await complete_payment_flow(
        order_id=order_id,
        message=callback.message,
        state=state,
        telegram_id=callback.from_user.id,
        payment_type=str(order.get('payment_type') or provider_order.get('payment_type')),
        referral_amount=_custom_payment_referral_amount(order),
    )
    completed_order = find_order_by_order_id(order_id)
    if completed_order and completed_order.get('status') == 'paid':
        update_payment_auto_check(order_id, state='completed')


def _custom_payment_referral_amount(order: dict) -> int:
    try:
        if order.get('final_amount_cents') is not None:
            return int(order.get('final_amount_cents') or 0)
        return int(order.get('amount_cents') or 0)
    except (TypeError, ValueError):
        return 0


def _eligible_provider_tariffs(
    tariffs: list[dict],
    payment_type: str,
    minimum_amount_minor: int,
) -> list[dict]:
    from bot.services.exchange_rate import get_payment_rate_snapshot, provider_amount_from_base_minor

    snapshot = get_payment_rate_snapshot()
    result = []
    for tariff in tariffs:
        base_amount = int(
            tariff.get('price_minor')
            or int(float(tariff.get('price_rub') or 0) * 100)
        )
        if base_amount <= 0:
            continue
        charge_amount, _ = provider_amount_from_base_minor(base_amount, payment_type, snapshot)
        if charge_amount >= int(minimum_amount_minor or 0):
            result.append(tariff)
    return result


def _eligible_rub_tariffs(tariffs: list[dict], minimum_amount_cents: int) -> list[dict]:
    """Deprecated compatibility wrapper for old extensions and tests."""
    return _eligible_provider_tariffs(tariffs, 'cards', minimum_amount_cents)
