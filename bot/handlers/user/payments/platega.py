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

# Platega provider configuration
_PLATEGA_TITLE = '💸 <b>Platega</b>'
_PLATEGA_TYPE = 'platega'
_PLATEGA_ERROR = 'Platega'
_PLATEGA_QR_FILE = 'platega.png'
_PLATEGA_CHECK_PREFIX = 'check_platega'
_PLATEGA_RESULT_KEY = 'platega_transaction_id'
_PLATEGA_MIN_PRICE = 10
_PLATEGA_INSTRUCTION = (
    'Откройте {payment_link} или отсканируйте QR-код и выберите способ оплаты '
    'на странице Platega.'
)


@router.callback_query(F.data == 'pay_platega')
async def pay_platega_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for payment via Platega (new key)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _PLATEGA_MIN_PRICE]
    if not rub_tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html=_PLATEGA_TITLE,
                instruction_html=f'😔 Нет тарифов с ценой в рублях (от {_PLATEGA_MIN_PRICE} ₽).\nОбратитесь к администратору.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_PLATEGA_TITLE,
            instruction_html='Выберите тариф:\n\n<i>Способ оплаты выбирается на странице Platega.</i>',
        ),
        runtime_markup=tariff_select_kb(rub_tariffs, is_platega=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('platega_pay:'))
async def platega_pay_create(callback: CallbackQuery, state: FSMContext):
    """Creates a Platega payment link for the new key and sends a QR photo."""
    from database.requests import get_tariff_by_id, save_platega_transaction_id
    from bot.services.billing import create_platega_payment

    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _PLATEGA_MIN_PRICE:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            f'Минимальная сумма для Platega — {_PLATEGA_MIN_PRICE} ₽.',
            payment_provider_title='Platega',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
        payment_type=_PLATEGA_TYPE,
        create_func=create_platega_payment,
        save_func=save_platega_transaction_id,
        result_key=_PLATEGA_RESULT_KEY,
        title=_PLATEGA_TITLE,
        check_prefix=_PLATEGA_CHECK_PREFIX,
        error_name=_PLATEGA_ERROR,
        qr_filename=_PLATEGA_QR_FILE,
        back_callback='pay_platega',
        instruction_text=_PLATEGA_INSTRUCTION,
    )


@router.callback_query(F.data.startswith('renew_platega_tariff:'))
async def renew_platega_select_tariff(callback: CallbackQuery):
    """Selecting a payment plan for Platega when renewing a key."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.utils.groups import get_tariffs_for_renewal

    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _PLATEGA_MIN_PRICE]
    if not rub_tariffs:
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html=_PLATEGA_TITLE,
            instruction_html=f'😔 Нет тарифов с ценой в рублях (от {_PLATEGA_MIN_PRICE} ₽) для продления.\nОбратитесь к администратору.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_PLATEGA_TITLE,
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_platega=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_platega:'))
async def renew_platega_create(callback: CallbackQuery, state: FSMContext):
    """Creates a Platega payment link to renew the key."""
    from database.requests import get_tariff_by_id, get_key_details_for_user, save_platega_transaction_id
    from bot.services.billing import create_platega_payment

    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _PLATEGA_MIN_PRICE:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            f'Минимальная сумма для Platega — {_PLATEGA_MIN_PRICE} ₽.',
            payment_provider_title='Platega',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
        payment_type=_PLATEGA_TYPE,
        create_func=create_platega_payment,
        save_func=save_platega_transaction_id,
        result_key=_PLATEGA_RESULT_KEY,
        title=_PLATEGA_TITLE,
        check_prefix=_PLATEGA_CHECK_PREFIX,
        error_name=_PLATEGA_ERROR,
        qr_filename=_PLATEGA_QR_FILE,
        back_callback=f'renew_platega_tariff:{key_id}',
        key=key, vpn_key_id=key_id,
        instruction_text=_PLATEGA_INSTRUCTION,
    )


@router.callback_query(F.data.startswith('check_platega:'))
async def check_platega_payment(callback: CallbackQuery, state: FSMContext):
    """Checks the status of a Platega payment by clicking “✅ I paid.”"""
    await _run_platega_check(
        callback.message, state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_platega_check(message, state, order_id: str,
                             telegram_id: int, callback=None) -> None:
    """
    General verification of Platega payment for the “I paid” button and deep-link return.
    """
    from bot.services.billing import check_platega_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_PLATEGA_TYPE,
        payment_id_field=_PLATEGA_RESULT_KEY,
        check_func=check_platega_payment_status,
        rate_limit_seconds=10,
        rate_limit_prefix='platega',
        callback=callback,
    )
