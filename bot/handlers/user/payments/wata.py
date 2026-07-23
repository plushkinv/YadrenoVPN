"""WATA compatibility payment callbacks backed by configurable UI pages."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.user.payments.base import check_qr_payment_flow, create_qr_payment_flow
from bot.handlers.user.payments.tariff_select_page import show_provider_tariff_select_page
from bot.services.money import format_money_minor
from bot.utils.page_renderer import render_page

router = Router()

_WATA_TYPE = 'wata'
_WATA_ERROR = 'WATA'
_WATA_QR_FILE = 'wata.png'
_WATA_CHECK_PREFIX = 'check_wata'
_WATA_RESULT_KEY = 'wata_link_id'
_WATA_MIN_PRICE = 10


async def _show_minimum_page(callback: CallbackQuery) -> None:
    await render_page(
        callback,
        'payment_minimum_unavailable',
        context={'payment_minimum_text': format_money_minor(_WATA_MIN_PRICE * 100, 'RUB')},
    )
    await callback.answer()


@router.callback_query(F.data == 'pay_wata')
async def pay_wata_select_tariff(callback: CallbackQuery):
    """Select a tariff for a new-key WATA payment."""
    from database.requests import get_all_tariffs

    tariffs = [
        tariff
        for tariff in get_all_tariffs(include_hidden=False)
        if float(tariff.get('price_rub') or 0) >= _WATA_MIN_PRICE
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_WATA_TYPE,
        callback_factory=lambda tariff_id: f'wata_pay:{tariff_id}',
        back_callback='buy_key',
        minimum_amount=_WATA_MIN_PRICE * 100,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('wata_pay:'))
async def wata_pay_create(callback: CallbackQuery, state: FSMContext):
    """Create a WATA payment link for a new key."""
    from bot.services.billing import create_wata_payment
    from database.requests import get_tariff_by_id, save_wata_link_id

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
    if price_rub < _WATA_MIN_PRICE:
        await _show_minimum_page(callback)
        return

    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_WATA_TYPE,
        create_func=create_wata_payment,
        save_func=save_wata_link_id,
        result_key=_WATA_RESULT_KEY,
        title=_WATA_ERROR,
        check_prefix=_WATA_CHECK_PREFIX,
        error_name=_WATA_ERROR,
        qr_filename=_WATA_QR_FILE,
        back_callback='pay_wata',
    )


@router.callback_query(F.data.startswith('renew_wata_tariff:'))
async def renew_wata_select_tariff(callback: CallbackQuery):
    """Select a tariff for a WATA key renewal."""
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
        if float(tariff.get('price_rub') or 0) >= _WATA_MIN_PRICE
    ]
    await show_provider_tariff_select_page(
        callback,
        tariffs=tariffs,
        payment_type=_WATA_TYPE,
        callback_factory=lambda tariff_id: f'renew_pay_wata:{key_id}:{tariff_id}',
        back_callback=f'key_renew:{key_id}',
        key=key,
        minimum_amount=_WATA_MIN_PRICE * 100,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_wata:'))
async def renew_wata_create(callback: CallbackQuery, state: FSMContext):
    """Create a WATA payment link for a key renewal."""
    from bot.services.billing import create_wata_payment
    from database.requests import get_key_details_for_user, get_tariff_by_id, save_wata_link_id

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
    if price_rub < _WATA_MIN_PRICE:
        await _show_minimum_page(callback)
        return

    await create_qr_payment_flow(
        callback=callback,
        state=state,
        tariff=tariff,
        price_rub=price_rub,
        payment_type=_WATA_TYPE,
        create_func=create_wata_payment,
        save_func=save_wata_link_id,
        result_key=_WATA_RESULT_KEY,
        title=_WATA_ERROR,
        check_prefix=_WATA_CHECK_PREFIX,
        error_name=_WATA_ERROR,
        qr_filename=_WATA_QR_FILE,
        back_callback=f'renew_wata_tariff:{key_id}',
        key=key,
        vpn_key_id=key_id,
    )


@router.callback_query(F.data.startswith('check_wata:'))
async def check_wata_payment(callback: CallbackQuery, state: FSMContext):
    """Check a WATA payment from its page action."""
    await _run_wata_check(
        callback.message,
        state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_wata_check(message, state, order_id: str, telegram_id: int, callback=None) -> None:
    """Check a WATA payment for both page actions and deep-link returns."""
    from bot.services.billing import check_wata_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_WATA_TYPE,
        payment_id_field=_WATA_RESULT_KEY,
        check_func=check_wata_payment_status,
        check_arg_is_order_id=False,
        rate_limit_seconds=30,
        rate_limit_prefix='wata',
        callback=callback,
    )
