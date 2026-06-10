import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.utils.text import escape_html, safe_edit_or_send
from bot.handlers.user.payments.base import (
    create_qr_payment_flow, check_qr_payment_flow
)

logger = logging.getLogger(__name__)

router = Router()

# Конфигурация провайдера Cardlink
_CL_TITLE = '🔗 <b>Оплата Cardlink</b>'
_CL_TYPE = 'cardlink'
_CL_ERROR = 'Cardlink'
_CL_QR_FILE = 'cardlink.png'
_CL_CHECK_PREFIX = 'check_cardlink'
_CL_RESULT_KEY = 'cardlink_bill_id'
_CL_MIN_PRICE = 10
# Подсказка Cardlink: после оплаты можно вернуться в бот по ссылке
_CL_HINT = (
    'После оплаты нажмите «✅ Я оплатил» — или просто вернитесь '
    'в бот по ссылке после оплаты, проверка запустится автоматически.'
)


@router.callback_query(F.data == 'pay_cardlink')
async def pay_cardlink_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты через Cardlink (новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _CL_MIN_PRICE]
    if not rub_tariffs:
        await safe_edit_or_send(
            callback.message,
            f'{_CL_TITLE}\n\n😔 Нет тарифов с ценой в рублях (от {_CL_MIN_PRICE} ₽).\nОбратитесь к администратору.',
            reply_markup=home_only_kb()
        )
        await callback.answer()
        return
    await safe_edit_or_send(
        callback.message,
        '🔗 <b>Оплата Cardlink (Карта/СБП)</b>\n\nВыберите тариф:\n\n'
        '<i>Оплата банковской картой или СБП через сервис Cardlink.</i>',
        reply_markup=tariff_select_kb(rub_tariffs, is_cardlink=True)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('cardlink_pay:'))
async def cardlink_pay_create(callback: CallbackQuery):
    """Создаёт счёт Cardlink для нового ключа и отправляет QR-фото."""
    from database.requests import get_tariff_by_id, save_cardlink_bill_id
    from bot.services.billing import create_cardlink_payment

    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _CL_MIN_PRICE:
        await callback.answer(f'❌ Минимальная сумма для Cardlink — {_CL_MIN_PRICE} ₽', show_alert=True)
        return

    await create_qr_payment_flow(
        callback=callback, tariff=tariff, price_rub=price_rub,
        payment_type=_CL_TYPE,
        create_func=create_cardlink_payment,
        save_func=save_cardlink_bill_id,
        result_key=_CL_RESULT_KEY,
        title=_CL_TITLE,
        check_prefix=_CL_CHECK_PREFIX,
        error_name=_CL_ERROR,
        qr_filename=_CL_QR_FILE,
        back_callback='pay_cardlink',
        hint_text=_CL_HINT,
    )


@router.callback_query(F.data.startswith('renew_cardlink_tariff:'))
async def renew_cardlink_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Cardlink при продлении ключа."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.utils.groups import get_tariffs_for_renewal

    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] >= _CL_MIN_PRICE]
    if not rub_tariffs:
        await callback.answer(f'😔 Нет тарифов с ценой в рублях (от {_CL_MIN_PRICE} ₽)', show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        f"🔗 <b>Оплата Cardlink (Карта/СБП)</b>\n\n🔑 Ключ: <b>{escape_html(key['display_name'])}</b>\n\nВыберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_cardlink=True)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_pay_cardlink:'))
async def renew_cardlink_create(callback: CallbackQuery):
    """Создаёт счёт Cardlink для продления ключа."""
    from database.requests import get_tariff_by_id, get_key_details_for_user, save_cardlink_bill_id
    from bot.services.billing import create_cardlink_payment

    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub < _CL_MIN_PRICE:
        await callback.answer(f'❌ Минимальная сумма для Cardlink — {_CL_MIN_PRICE} ₽', show_alert=True)
        return

    await create_qr_payment_flow(
        callback=callback, tariff=tariff, price_rub=price_rub,
        payment_type=_CL_TYPE,
        create_func=create_cardlink_payment,
        save_func=save_cardlink_bill_id,
        result_key=_CL_RESULT_KEY,
        title=_CL_TITLE,
        check_prefix=_CL_CHECK_PREFIX,
        error_name=_CL_ERROR,
        qr_filename=_CL_QR_FILE,
        back_callback=f'renew_cardlink_tariff:{key_id}',
        key=key, vpn_key_id=key_id,
        hint_text=_CL_HINT,
    )


@router.callback_query(F.data.startswith('check_cardlink:'))
async def check_cardlink_payment(callback: CallbackQuery, state: FSMContext):
    """Проверяет статус Cardlink-платежа по нажатию «✅ Я оплатил»."""
    await _run_cardlink_check(
        callback.message, state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_cardlink_check(message, state, order_id: str,
                              telegram_id: int, callback=None) -> None:
    """
    Общая логика проверки Cardlink-платежа.

    Используется как хендлером «✅ Я оплатил» (с callback), так и deep-link
    переходом по cl_Success / cl_Fail / cl_Result (без callback).
    """
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
