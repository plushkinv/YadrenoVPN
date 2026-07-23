"""Cardlink compatibility payment callbacks backed by configurable UI pages."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.user.payments.base import check_qr_payment_flow, create_qr_payment_flow
from bot.handlers.user.payments.tariff_select_page import show_provider_tariff_select_page
from bot.services.money import format_money_minor
from bot.utils.page_renderer import render_page

router = Router()

_CL_TYPE = 'cardlink'
_CL_ERROR = 'Cardlink'
_CL_QR_FILE = 'cardlink.png'
_CL_CHECK_PREFIX = 'check_cardlink'
_CL_RESULT_KEY = 'cardlink_bill_id'
_CL_MIN_PRICE = 10


async def _show_minimum_page(callback: CallbackQuery) -> None:
    await render_page(
        callback,
        'payment_minimum_unavailable',
        context={'payment_minimum_text': format_money_minor(_CL_MIN_PRICE * 100, 'RUB')},
    )
    await callback.answer()


@router.callback_query(F.data == 'pay_cardlink')
async def pay_cardlink_select_tariff(callback: CallbackQuery):
    """Select a tariff for a new-key Cardlink payment."""
    from database.requests import get_all_tariffs

    tariffs = [
        tariff
        for tariff in get_all_tariffs(include_hidden=False)
        if float(tariff.get('price_rub') or 0) >= _CL_MIN_PRICE
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_CL_TYPE,
        callback_factory=lambda tariff_id: f'cardlink_pay:{tariff_id}',
        back_callback='buy_key',
        minimum_amount=_CL_MIN_PRICE * 100,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('cardlink_pay:'))
async def cardlink_pay_create(callback: CallbackQuery, state: FSMContext):
    """Create a Cardlink payment link for a new key."""
    from bot.services.billing import create_cardlink_payment
    from database.requests import get_tariff_by_id, save_cardlink_bill_id

    try:
        tariff_id = int(callback.data.split(':', 1)[1])
    except (IndexError, TypeError, ValueError):
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _CL_MIN_PRICE:
        await _show_minimum_page(callback)
        return

    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_CL_TYPE,
        create_func=create_cardlink_payment,
        save_func=save_cardlink_bill_id,
        result_key=_CL_RESULT_KEY,
        title=_CL_ERROR,
        check_prefix=_CL_CHECK_PREFIX,
        error_name=_CL_ERROR,
        qr_filename=_CL_QR_FILE,
        back_callback='pay_cardlink',
    )


@router.callback_query(F.data.startswith('renew_cardlink_tariff:'))
async def renew_cardlink_select_tariff(callback: CallbackQuery):
    """Select a tariff for a Cardlink key renewal."""
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
        if float(tariff.get('price_rub') or 0) >= _CL_MIN_PRICE
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_CL_TYPE,
        callback_factory=lambda tariff_id: f'renew_pay_cardlink:{key_id}:{tariff_id}',
        back_callback=f'key_renew:{key_id}',
        key=key,
        minimum_amount=_CL_MIN_PRICE * 100,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_cardlink:'))
async def renew_cardlink_create(callback: CallbackQuery, state: FSMContext):
    """Create a Cardlink payment link for a key renewal."""
    from bot.services.billing import create_cardlink_payment
    from database.requests import get_key_details_for_user, get_tariff_by_id, save_cardlink_bill_id
    try:
        _, key_id_raw, tariff_id_raw = callback.data.split(':', 2)
        key_id = int(key_id_raw)
        tariff_id = int(tariff_id_raw)
    except (TypeError, ValueError):
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _CL_MIN_PRICE:
        await _show_minimum_page(callback)
        return

    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_CL_TYPE,
        create_func=create_cardlink_payment,
        save_func=save_cardlink_bill_id,
        result_key=_CL_RESULT_KEY,
        title=_CL_ERROR,
        check_prefix=_CL_CHECK_PREFIX,
        error_name=_CL_ERROR,
        qr_filename=_CL_QR_FILE,
        back_callback=f'renew_cardlink_tariff:{key_id}',
        key=key,
        vpn_key_id=key_id,
    )


@router.callback_query(F.data.startswith('check_cardlink:'))
async def check_cardlink_payment(callback: CallbackQuery, state: FSMContext):
    """Check a Cardlink payment from its page action."""
    await _run_cardlink_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_cardlink_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    """Check a Cardlink payment for both page actions and deep-link returns."""
    from bot.services.billing import check_cardlink_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_CL_TYPE,
        payment_id_field=_CL_RESULT_KEY,
        check_func=check_cardlink_payment_status,
        rate_limit_seconds=10,
        rate_limit_prefix='cardlink',
        callback=callback,
    )
