"""Compatibility entry points for spending the internal referral balance."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.user.payments.tariff_select_page import show_provider_tariff_select_page
from bot.services.payment_intents import (
    PURPOSE_KEY_PURCHASE,
    PURPOSE_KEY_RENEWAL,
    create_payment_intent,
    format_base_minor,
    quote_payment_intent,
)
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page

logger = logging.getLogger(__name__)

router = Router()
BALANCE_PAYMENT_PAGE_KEY = 'balance_payment'


def _payment_discount_line(promo_lines: str | None) -> str:
    discount = (promo_lines or '').strip('\n')
    return f'{discount}\n' if discount else ''


def build_balance_payment_page_context(
    *,
    tariff_name: str,
    price_str: str,
    balance_str: str,
    deduct_str: str,
    remaining_str: str,
    promo_lines: str | None = None,
    no_topup_methods: bool = False,
) -> dict:
    """Return data-only values consumed by the editable balance page."""
    del no_topup_methods
    return {
        'payment_tariff_name': tariff_name,
        'payment_amount_text': price_str,
        'payment_balance_text': balance_str,
        'payment_balance_deduct_text': deduct_str,
        'payment_remaining_text': remaining_str,
        'payment_discount_line_html': _payment_discount_line(promo_lines),
    }


def _balance_spending_enabled() -> bool:
    from database.requests import get_referral_reward_type, is_referral_enabled

    return is_referral_enabled() and get_referral_reward_type() == 'balance'


def _get_or_create_user_id(callback: CallbackQuery) -> int:
    from database.requests import get_or_create_user

    user, _ = get_or_create_user(
        callback.from_user.id,
        getattr(callback.from_user, 'username', None),
        getattr(callback.from_user, 'first_name', None),
        getattr(callback.from_user, 'last_name', None),
    )
    return int(user['id'])


def _create_owned_intent(
    callback: CallbackQuery,
    *,
    tariff_id: int,
    key_id: int | None,
):
    from database.requests import get_key_details_for_user, get_tariff_by_id

    tariff = get_tariff_by_id(tariff_id)
    if not tariff or not tariff.get('is_active', True):
        return None
    purpose_data = {'tariff_id': int(tariff_id)}
    purpose = PURPOSE_KEY_PURCHASE
    if key_id:
        if not get_key_details_for_user(key_id, callback.from_user.id):
            return None
        purpose = PURPOSE_KEY_RENEWAL
        purpose_data['key_id'] = int(key_id)
    try:
        return create_payment_intent(
            user_id=_get_or_create_user_id(callback),
            purpose=purpose,
            purpose_data=purpose_data,
        )
    except (TypeError, ValueError) as error:
        logger.warning(
            'Legacy balance callback could not create an intent tariff=%s key=%s: %s',
            tariff_id,
            key_id,
            error,
        )
        return None


async def _show_balance_payment_screen(
    callback: CallbackQuery,
    state: FSMContext,
    tariff_id: int,
    user_internal_id: int,
    key_id: int | None = None,
) -> None:
    """Show a confirmation backed entirely by the ``balance_payment`` page."""
    del user_internal_id
    from bot.services.promotions import describe_quote_lines
    from database.requests import get_tariff_by_id, get_user_balance

    if not _balance_spending_enabled():
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    intent = _create_owned_intent(callback, tariff_id=tariff_id, key_id=key_id)
    if not intent:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    try:
        quote = quote_payment_intent(intent.order_id, 'balance')
    except ValueError as error:
        logger.warning('Balance preview is unavailable order=%s: %s', intent.order_id, error)
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    if quote.unavailable_reason or not quote.raw.get('ok', True):
        logger.warning(
            'Balance preview is unavailable order=%s reason=%s',
            intent.order_id,
            quote.unavailable_reason,
        )
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    if quote.is_free:
        from bot.services.billing import complete_payment_flow
        from database.requests import update_payment_type

        update_payment_type(intent.order_id, 'promo_free')
        await complete_payment_flow(
            order_id=intent.order_id,
            message=callback.message,
            state=state,
            telegram_id=callback.from_user.id,
            payment_type='promo_free',
            referral_amount=0,
        )
        await callback.answer()
        return

    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    balance = get_user_balance(intent.user_id)
    deduction = min(balance, quote.payable_amount_minor)
    if deduction <= 0:
        await render_page(
            callback,
            'balance_insufficient',
            context={
                'payment_balance_text': format_base_minor(balance, intent.base_currency),
                'payment_amount_text': format_base_minor(
                    quote.payable_amount_minor,
                    intent.base_currency,
                ),
            },
        )
        await callback.answer()
        return

    remaining = max(0, quote.payable_amount_minor - deduction)
    context = build_balance_payment_page_context(
        tariff_name=str(tariff.get('name') or f'#{tariff_id}'),
        price_str=format_base_minor(quote.payable_amount_minor, intent.base_currency),
        balance_str=format_base_minor(balance, intent.base_currency),
        deduct_str=format_base_minor(deduction, intent.base_currency),
        remaining_str=format_base_minor(remaining, intent.base_currency),
        promo_lines=describe_quote_lines(dict(quote.raw)),
    )
    context.update({
        'order_id': intent.order_id,
        'telegram_id': callback.from_user.id,
        'payment_allow_balance': True,
    })
    await state.update_data(
        payment_intent_order_id=intent.order_id,
        tariff_id=tariff_id,
        key_id=key_id,
    )
    await render_page(
        callback,
        page_key=BALANCE_PAYMENT_PAGE_KEY,
        context=build_page_flow_context(callback, **context),
    )
    await callback.answer()


async def _show_balance_tariffs(callback: CallbackQuery, key: dict | None = None) -> None:
    from database.requests import get_all_tariffs, get_user_balance

    if not _balance_spending_enabled():
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    user_id = _get_or_create_user_id(callback)
    balance = get_user_balance(user_id)
    if balance <= 0:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    if key:
        from bot.utils.groups import get_tariffs_for_renewal

        tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
        key_id = int(key['id'])
        callback_factory = lambda tariff_id: f'balance_pay:{tariff_id}:{key_id}'
        back_callback = f'key_renew:{key_id}'
    else:
        tariffs = get_all_tariffs(include_hidden=False)
        callback_factory = lambda tariff_id: f'balance_pay:{tariff_id}'
        back_callback = 'buy_key'
    tariffs = [tariff for tariff in tariffs if int(tariff.get('price_minor') or 0) > 0]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type='balance',
        callback_factory=callback_factory,
        back_callback=back_callback,
        key=key,
    )
    await callback.answer()


@router.callback_query(F.data == 'pay_use_balance')
async def pay_use_balance_buy_handler(callback: CallbackQuery, state: FSMContext):
    """Compatibility entry for selecting a purchase tariff paid from balance."""
    del state
    await _show_balance_tariffs(callback)


@router.callback_query(F.data.startswith('pay_use_balance:'))
async def pay_use_balance_renew_handler(callback: CallbackQuery, state: FSMContext):
    """Compatibility entry for selecting a renewal tariff paid from balance."""
    del state
    from database.requests import get_key_details_for_user

    try:
        key_id = int(callback.data.split(':', 1)[1])
    except (IndexError, TypeError, ValueError):
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return
    await _show_balance_tariffs(callback, key)


@router.callback_query(F.data.startswith('balance_pay:'))
async def balance_pay_handler(callback: CallbackQuery, state: FSMContext):
    """Create an intent and show the balance confirmation page."""
    parts = str(callback.data or '').split(':')
    try:
        tariff_id = int(parts[1])
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] not in {'', '0'} else None
    except (IndexError, ValueError):
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    await _show_balance_payment_screen(
        callback,
        state,
        tariff_id,
        _get_or_create_user_id(callback),
        key_id=key_id,
    )


async def _apply_legacy_balance_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Map an old balance action to the current intent-based balance operation."""
    parts = str(callback.data or '').split(':')
    state_data = await state.get_data()
    try:
        tariff_id = int(state_data.get('tariff_id') or (parts[1] if len(parts) > 1 else 0))
        raw_key_id = state_data.get('key_id')
        if not raw_key_id and len(parts) > 2 and parts[2] not in {'', '0'}:
            raw_key_id = parts[2]
        key_id = int(raw_key_id) if raw_key_id else None
    except (TypeError, ValueError):
        tariff_id = 0
        key_id = None
    if tariff_id <= 0:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    intent = _create_owned_intent(callback, tariff_id=tariff_id, key_id=key_id)
    if not intent:
        await render_page(callback, 'action_unavailable')
        await callback.answer()
        return
    from bot.handlers.user.payments.intent import apply_payment_intent_balance

    await apply_payment_intent_balance(callback, state, intent)


@router.callback_query(F.data.startswith('pay_with_balance:'))
async def pay_with_balance_handler(callback: CallbackQuery, state: FSMContext):
    """Handle the legacy full-balance callback through a current intent."""
    await _apply_legacy_balance_callback(callback, state)


@router.callback_query(F.data.startswith('pay_card_balance:'))
async def pay_card_balance_handler(callback: CallbackQuery, state: FSMContext):
    """Handle a legacy card-surcharge callback through provider selection."""
    await _apply_legacy_balance_callback(callback, state)


@router.callback_query(F.data.startswith('pay_qr_balance:'))
async def pay_qr_balance_handler(callback: CallbackQuery, state: FSMContext):
    """Handle a legacy QR-surcharge callback through provider selection."""
    await _apply_legacy_balance_callback(callback, state)


__all__ = [
    'BALANCE_PAYMENT_PAGE_KEY',
    'build_balance_payment_page_context',
    'router',
]
