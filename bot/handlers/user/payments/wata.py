import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.utils.text import escape_html, safe_edit_or_send
from bot.handlers.user.payments.base import create_qr_payment_flow, check_qr_payment_flow
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()

# WATA provider configuration (common parameters for create and check)
_WATA_TITLE = '🌊 <b>WATA</b>'
_WATA_TYPE = 'wata'
_WATA_ERROR = 'WATA'
_WATA_QR_FILE = 'wata.png'
_WATA_CHECK_PREFIX = 'check_wata'
_WATA_RESULT_KEY = 'wata_link_id'
_WATA_MIN_PRICE = 10


@router.callback_query(F.data == 'pay_wata')
async def pay_wata_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for payment via WATA (new key)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _WATA_MIN_PRICE]
    if not rub_tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html=_WATA_TITLE,
                instruction_html=f'😔 Нет тарифов с ценой в рублях (от {_WATA_MIN_PRICE} ₽).\nОбратитесь к администратору.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_WATA_TITLE,
            instruction_html='Выберите тариф:\n\n<i>Оплата банковской картой или СБП через сервис WATA.</i>',
        ),
        runtime_markup=tariff_select_kb(rub_tariffs, is_wata=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('wata_pay:'))
async def wata_pay_create(callback: CallbackQuery, state: FSMContext):
    """Creates a WATA payment link for the new key and sends a QR photo."""
    from database.requests import get_tariff_by_id, save_wata_link_id
    from bot.services.billing import create_wata_payment

    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _WATA_MIN_PRICE:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            f'Минимальная сумма для WATA — {_WATA_MIN_PRICE} ₽.',
            payment_provider_title='WATA',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
        payment_type=_WATA_TYPE,
        create_func=create_wata_payment,
        save_func=save_wata_link_id,
        result_key=_WATA_RESULT_KEY,
        title=_WATA_TITLE,
        check_prefix=_WATA_CHECK_PREFIX,
        error_name=_WATA_ERROR,
        qr_filename=_WATA_QR_FILE,
        back_callback='pay_wata',
    )


@router.callback_query(F.data.startswith('renew_wata_tariff:'))
async def renew_wata_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for paying WATA when renewing a key."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.utils.groups import get_tariffs_for_renewal

    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _WATA_MIN_PRICE]
    if not rub_tariffs:
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html=_WATA_TITLE,
            instruction_html=f'😔 Нет тарифов с ценой в рублях (от {_WATA_MIN_PRICE} ₽) для продления.\nОбратитесь к администратору.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_WATA_TITLE,
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_wata=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_wata:'))
async def renew_wata_create(callback: CallbackQuery, state: FSMContext):
    """Creates a WATA payment link for key renewal."""
    from database.requests import get_tariff_by_id, get_key_details_for_user, save_wata_link_id
    from bot.services.billing import create_wata_payment

    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _WATA_MIN_PRICE:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            f'Минимальная сумма для WATA — {_WATA_MIN_PRICE} ₽.',
            payment_provider_title='WATA',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
        payment_type=_WATA_TYPE,
        create_func=create_wata_payment,
        save_func=save_wata_link_id,
        result_key=_WATA_RESULT_KEY,
        title=_WATA_TITLE,
        check_prefix=_WATA_CHECK_PREFIX,
        error_name=_WATA_ERROR,
        qr_filename=_WATA_QR_FILE,
        back_callback=f'renew_wata_tariff:{key_id}',
        key=key, vpn_key_id=key_id,
    )


@router.callback_query(F.data.startswith('check_wata:'))
async def check_wata_payment(callback: CallbackQuery, state: FSMContext):
    """
    Checks the status of the WATA payment by clicking “✅ I paid.”

    WATA has a limit - no more than one request per 30 seconds.
    """
    await _run_wata_check(
        callback.message, state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_wata_check(message, state, order_id: str,
                          telegram_id: int, callback=None) -> None:
    """
    General verification of WATA payment for the “I paid” button and deep-link return.
    """
    from bot.services.billing import check_wata_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_WATA_TYPE,
        payment_id_field=_WATA_RESULT_KEY,
        check_func=check_wata_payment_status,
        check_arg_is_order_id=False,  # WATA: check by wata_link_id via /links/{id}
        rate_limit_seconds=30,
        rate_limit_prefix='wata',
        pending_hint='Если только что оплатили — подождите 30 секунд (ограничение WATA API).',
        callback=callback,
    )
