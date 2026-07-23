"""Telegram Payments and YooKassa QR user flows."""
from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.user.payments.base import (
    check_qr_payment_flow,
    complete_promo_free_payment,
    create_qr_payment_flow,
    send_telegram_invoice_or_status,
)
from bot.handlers.user.payments.tariff_select_page import show_provider_tariff_select_page
from bot.services.money import format_money_minor
from bot.utils.page_renderer import render_page
from bot.utils.payment_invoice import (
    clamp_invoice_text,
    invoice_change_method_button,
    invoice_pay_button,
    purchase_invoice_description,
    renewal_invoice_description,
)

logger = logging.getLogger(__name__)

router = Router()

_CARDS_TYPE = 'cards'
_CARDS_PROVIDER = 'TG payments'
_YK_TYPE = 'yookassa_qr'
_YK_PROVIDER = 'YooKassa'
_YK_QR_FILE = 'qr.png'
_YK_CHECK_PREFIX = 'check_yookassa_qr'
_YK_RESULT_KEY = 'yookassa_payment_id'


def _callback_tail(data: str | None) -> str | None:
    _, separator, tail = str(data or '').partition(':')
    return tail if separator and tail else None


async def _show_order_unavailable(callback: CallbackQuery) -> None:
    await render_page(callback, 'payment_order_unavailable')
    await callback.answer()


def _cards_invoice_markup(amount_minor: int, cancel_callback: str):
    amount = format_money_minor(amount_minor, 'RUB')
    return (
        InlineKeyboardBuilder()
        .row(InlineKeyboardButton(text=invoice_pay_button(amount), pay=True))
        .row(
            InlineKeyboardButton(
                text=invoice_change_method_button(),
                callback_data=cancel_callback,
            )
        )
        .as_markup()
    )


async def _send_cards_invoice(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    tariff: dict,
    key: dict | None,
    order_id: str | None,
) -> None:
    from bot.services.promotions import prepare_order_pricing
    from database.requests import (
        create_pending_order,
        get_or_create_user,
        get_setting,
        update_order_tariff,
    )

    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return

    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    user_id = int(user['id'])
    tariff_id = int(tariff['id'])
    key_id = int(key['id']) if key else None
    action = 'renewal' if key else 'new_key'

    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type=_CARDS_TYPE)
    else:
        _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type=_CARDS_TYPE,
            vpn_key_id=key_id,
        )

    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type=_CARDS_TYPE,
        action=action,
    )
    if not quote['ok']:
        logger.warning(
            'Telegram invoice quote is unavailable order=%s reason=%s',
            order_id,
            quote.get('unavailable_reason'),
        )
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return

    amount_minor = int(quote['final_amount'])
    if amount_minor <= 0:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return

    if key:
        description = renewal_invoice_description(key['display_name'], tariff['name'])
        payload = f'renew:{order_id}'
        cancel_callback = f'renew_invoice_cancel:{key_id}:{tariff_id}'
        log_context = f'cards:renew order={order_id} tariff={tariff_id} key={key_id}'
    else:
        description = purchase_invoice_description(tariff['name'], tariff['duration_days'])
        payload = f'vpn_key:{order_id}'
        cancel_callback = 'buy_key'
        log_context = f'cards:new_key order={order_id} tariff={tariff_id}'

    amount_rub = amount_minor / 100
    provider_data = {
        'receipt': {
            'customer': {'email': f'user_{order_id}@t.me'},
            'items': [
                {
                    'description': clamp_invoice_text(description, 128),
                    'quantity': '1.00',
                    'amount': {'value': f'{amount_rub:.2f}', 'currency': 'RUB'},
                    'vat_code': 1,
                    'payment_mode': 'full_prepayment',
                    'payment_subject': 'service',
                }
            ],
        }
    }
    bot_info = await callback.bot.get_me()
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title=_CARDS_PROVIDER,
        log_context=log_context,
        title=clamp_invoice_text(bot_info.first_name, 32),
        description=clamp_invoice_text(description, 255),
        payload=payload,
        provider_token=provider_token,
        currency='RUB',
        prices=[
            LabeledPrice(
                label=clamp_invoice_text(description, 80),
                amount=amount_minor,
            )
        ],
        provider_data=json.dumps(provider_data),
        reply_markup=_cards_invoice_markup(amount_minor, cancel_callback),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data.startswith('pay_cards'))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Select a tariff for a new-key Telegram invoice."""
    from database.requests import get_all_tariffs

    order_id = _callback_tail(callback.data)
    await show_provider_tariff_select_page(
        callback,
        tariffs=get_all_tariffs(include_hidden=False),
        payment_type=_CARDS_TYPE,
        callback_factory=lambda tariff_id: (
            f'cards_pay:{tariff_id}:{order_id}' if order_id else f'cards_pay:{tariff_id}'
        ),
        back_callback='buy_key',
    )
    await callback.answer()


@router.callback_query(F.data.startswith('cards_pay:'))
async def pay_cards_invoice(callback: CallbackQuery, state: FSMContext):
    """Create a Telegram invoice for a new key."""
    from database.requests import get_tariff_by_id

    parts = str(callback.data or '').split(':')
    try:
        tariff_id = int(parts[1])
    except (IndexError, ValueError):
        await _show_order_unavailable(callback)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await _show_order_unavailable(callback)
        return
    await _send_cards_invoice(
        callback,
        state,
        tariff=tariff,
        key=None,
        order_id=parts[2] if len(parts) > 2 and parts[2] else None,
    )


@router.callback_query(F.data.startswith('renew_cards_tariff:'))
async def renew_cards_select_tariff(callback: CallbackQuery):
    """Select a tariff for a Telegram invoice renewal."""
    from bot.utils.groups import get_tariffs_for_renewal
    from database.requests import get_key_details_for_user

    parts = str(callback.data or '').split(':')
    try:
        key_id = int(parts[1])
    except (IndexError, ValueError):
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return
    order_id = parts[2] if len(parts) > 2 and parts[2] else None
    await show_provider_tariff_select_page(
        callback,
        tariffs=get_tariffs_for_renewal(key.get('tariff_id', 0)),
        payment_type=_CARDS_TYPE,
        callback_factory=lambda tariff_id: (
            f'renew_pay_cards:{key_id}:{tariff_id}:{order_id}'
            if order_id
            else f'renew_pay_cards:{key_id}:{tariff_id}'
        ),
        back_callback=f'key_renew:{key_id}',
        key=key,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_cards:'))
async def renew_cards_invoice(callback: CallbackQuery, state: FSMContext):
    """Create a Telegram invoice for a key renewal."""
    from database.requests import get_key_details_for_user, get_tariff_by_id

    parts = str(callback.data or '').split(':')
    try:
        key_id = int(parts[1])
        tariff_id = int(parts[2])
    except (IndexError, ValueError):
        await _show_order_unavailable(callback)
        return
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await _show_order_unavailable(callback)
        return
    await _send_cards_invoice(
        callback,
        state,
        tariff=tariff,
        key=key,
        order_id=parts[3] if len(parts) > 3 and parts[3] else None,
    )


@router.callback_query(F.data == 'pay_qr')
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Select a tariff for a new-key YooKassa QR payment."""
    from database.requests import get_all_tariffs

    tariffs = [
        tariff
        for tariff in get_all_tariffs(include_hidden=False)
        if float(tariff.get('price_rub') or 0) > 0
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_YK_TYPE,
        callback_factory=lambda tariff_id: f'qr_pay:{tariff_id}',
        back_callback='buy_key',
    )
    await callback.answer()


@router.callback_query(F.data.startswith('qr_pay:'))
async def qr_pay_create(callback: CallbackQuery, state: FSMContext):
    """Create a YooKassa QR payment for a new key."""
    from bot.services.billing import create_yookassa_qr_payment
    from database.requests import get_tariff_by_id, save_yookassa_payment_id

    try:
        tariff_id = int(callback.data.split(':', 1)[1])
    except (IndexError, TypeError, ValueError):
        await _show_order_unavailable(callback)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await _show_order_unavailable(callback)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_YK_TYPE,
        create_func=create_yookassa_qr_payment,
        save_func=save_yookassa_payment_id,
        result_key=_YK_RESULT_KEY,
        title=_YK_PROVIDER,
        check_prefix=_YK_CHECK_PREFIX,
        error_name=_YK_PROVIDER,
        qr_filename=_YK_QR_FILE,
        back_callback='pay_qr',
    )


async def _yookassa_referral_amount(order: dict, state: FSMContext) -> int:
    """Return the paid QR portion used for referral accounting."""
    state_data = await state.get_data()
    remaining_cents = int(state_data.get('remaining_cents') or 0)
    if remaining_cents > 0:
        return remaining_cents
    if order.get('final_amount_cents') is not None:
        return int(order.get('final_amount_cents') or 0)
    from database.requests import get_tariff_by_id

    tariff = get_tariff_by_id(order.get('tariff_id'))
    return int(float(tariff.get('price_rub') or 0) * 100) if tariff else 0


@router.callback_query(F.data.startswith('check_yookassa_qr:'))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    """Check a YooKassa QR payment from its page action."""
    await _run_yookassa_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_yookassa_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    """Check a YooKassa QR payment for page actions and deep-link returns."""
    from bot.services.billing import check_yookassa_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_YK_TYPE,
        payment_id_field=_YK_RESULT_KEY,
        check_func=check_yookassa_payment_status,
        callback=callback,
        referral_override_func=_yookassa_referral_amount,
    )


@router.callback_query(F.data.startswith('renew_qr_tariff:'))
async def renew_qr_select_tariff(callback: CallbackQuery):
    """Select a tariff for a YooKassa QR renewal."""
    from bot.utils.groups import get_tariffs_for_renewal
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
    tariffs = [
        tariff
        for tariff in get_tariffs_for_renewal(key.get('tariff_id', 0))
        if float(tariff.get('price_rub') or 0) > 0
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_YK_TYPE,
        callback_factory=lambda tariff_id: f'renew_pay_qr:{key_id}:{tariff_id}',
        back_callback=f'key_renew:{key_id}',
        key=key,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_qr:'))
async def renew_qr_create(callback: CallbackQuery, state: FSMContext):
    """Create a YooKassa QR payment for a key renewal."""
    from bot.services.billing import create_yookassa_qr_payment
    from database.requests import get_key_details_for_user, get_tariff_by_id, save_yookassa_payment_id

    try:
        _, key_id_raw, tariff_id_raw = callback.data.split(':', 2)
        key_id = int(key_id_raw)
        tariff_id = int(tariff_id_raw)
    except (TypeError, ValueError):
        await _show_order_unavailable(callback)
        return
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await _show_order_unavailable(callback)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_YK_TYPE,
        create_func=create_yookassa_qr_payment,
        save_func=save_yookassa_payment_id,
        result_key=_YK_RESULT_KEY,
        title=_YK_PROVIDER,
        check_prefix=_YK_CHECK_PREFIX,
        error_name=_YK_PROVIDER,
        qr_filename=_YK_QR_FILE,
        back_callback=f'renew_qr_tariff:{key_id}',
        key=key,
        vpn_key_id=key_id,
    )
