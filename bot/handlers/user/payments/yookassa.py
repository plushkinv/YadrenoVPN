import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS
from bot.handlers.user.payments.base import (
    create_qr_payment_flow, check_qr_payment_flow
)

logger = logging.getLogger(__name__)

router = Router()

# Конфигурация QR-провайдера ЮКасса
_YK_TITLE = '📱 <b>QR-код для оплаты</b>'
_YK_TYPE = 'yookassa_qr'
_YK_ERROR = 'ЮКасса'
_YK_QR_FILE = 'qr.png'
_YK_CHECK_PREFIX = 'check_yookassa_qr'
_YK_RESULT_KEY = 'yookassa_payment_id'
_YK_LOADING = '⏳ Создаём QR-код для оплаты...'


# ============================================================================
# ОПЛАТА КАРТОЙ (Telegram Payments API — не трогаем)
# ============================================================================

@router.callback_query(F.data.startswith('pay_cards'))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Картой (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await safe_edit_or_send(callback.message, '💳 <b>Оплата картой</b>\n\n😔 Нет доступных тарифов.\n\nПопробуйте позже или обратитесь в поддержку.', reply_markup=home_only_kb())
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, '💳 <b>Оплата картой</b>\n\nВыберите тариф:', reply_markup=tariff_select_kb(tariffs, order_id=order_id, is_cards=True))
    await callback.answer()

@router.callback_query(F.data.startswith('cards_pay:'))
async def pay_cards_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Картой (Новый ключ)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, update_order_tariff, get_setting
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer('❌ Провайдер платежей не настроен', show_alert=True)
        return
    days = tariff['duration_days']
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        if not user_id:
            await callback.answer('❌ Ошибка пользователя', show_alert=True)
            return
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=None)
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer('❌ Ошибка: цена тарифа в рублях не задана.', show_alert=True)
        return
    import json
    from aiogram.exceptions import TelegramBadRequest

    provider_data = {
        "receipt": {
            "customer": {
                "email": f"user_{order_id}@t.me"
            },
            "items": [
                {
                    "description": f"Тариф «{tariff['name']}»",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{price_rub:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",
                    "payment_subject": "service"
                }
            ]
        }
    }

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        await callback.message.answer_invoice(title=bot_name, description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).", payload=f'vpn_key:{order_id}', provider_token=provider_token, currency='RUB', prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)], provider_data=json.dumps(provider_data), reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text=f'💳 Оплатить {price_rub} ₽', pay=True)).row(InlineKeyboardButton(text='❌ Отмена', callback_data='buy_key')).as_markup())
    except TelegramBadRequest as e:
        if 'CURRENCY_TOTAL_AMOUNT_INVALID' in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer('❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.', show_alert=True)
            return
        logger.exception('Ошибка при отправке инвойса картой (новый ключ).')
        raise e
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith('renew_cards_tariff:'))
async def renew_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Картой)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    if not tariffs:
        await callback.answer('Нет доступных тарифов', show_alert=True)
        return
    await safe_edit_or_send(callback.message, f"💳 <b>Оплата картой</b>\n\n🔑 Ключ: <b>{escape_html(key['display_name'])}</b>\n\nВыберите тариф для продления:", reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_cards=True))
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_cards:'))
async def renew_cards_invoice(callback: CallbackQuery):
    """Инвойс для продления (Картой)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, get_key_details_for_user, update_order_tariff, get_setting
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('Ошибка тарифа или ключа', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer('❌ Провайдер платежей не настроен', show_alert=True)
        return
    if not user_id:
        return
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=key_id)
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer('❌ Ошибка: цена тарифа в рублях не задана.', show_alert=True)
        return
    import json
    from aiogram.exceptions import TelegramBadRequest
    
    provider_data = {
        "receipt": {
            "customer": {
                "email": f"user_{order_id}@t.me"
            },
            "items": [
                {
                    "description": f"Продление «{tariff['name']}»",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{price_rub:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",
                    "payment_subject": "service"
                }
            ]
        }
    }

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        await callback.message.answer_invoice(title=bot_name, description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.", payload=f'renew:{order_id}', provider_token=provider_token, currency='RUB', prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)], provider_data=json.dumps(provider_data), reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text=f"💳 Оплатить {tariff.get('price_rub', 0)} ₽", pay=True)).row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'renew_invoice_cancel:{key_id}:{tariff_id}')).as_markup())
    except TelegramBadRequest as e:
        if 'CURRENCY_TOTAL_AMOUNT_INVALID' in str(e):
            logger.warning(f"Ошибка платежа (CARDS_RENEW): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer('❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.', show_alert=True)
            return
        logger.exception('Ошибка при отправке инвойса картой (продление ключа).')
        raise e
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# QR-ОПЛАТА ЮКАССА (refactored → общие функции base.py)
# ============================================================================

@router.callback_query(F.data == 'pay_qr')
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await safe_edit_or_send(callback.message, '📱 <b>QR-оплата</b>\n\n😔 Для QR-оплаты не настроены цены в рублях.\nОбратитесь к администратору.', reply_markup=home_only_kb())
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, '📱 <b>QR-оплата (Карта/СБП)</b>\n\nВыберите тариф:\n\n<i>Оплата через ЮКассу — поддерживает банковские карты и СБП.</i>', reply_markup=tariff_select_kb(rub_tariffs, is_qr=True))
    await callback.answer()

@router.callback_query(F.data.startswith('qr_pay:'))
async def qr_pay_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для нового ключа и отправляет QR-фото."""
    from database.requests import get_tariff_by_id, save_yookassa_payment_id
    from bot.services.billing import create_yookassa_qr_payment

    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer('❌ Цена в рублях не задана для этого тарифа', show_alert=True)
        return

    await create_qr_payment_flow(
        callback=callback, tariff=tariff, price_rub=price_rub,
        payment_type=_YK_TYPE,
        create_func=create_yookassa_qr_payment,
        save_func=save_yookassa_payment_id,
        result_key=_YK_RESULT_KEY,
        title=_YK_TITLE,
        check_prefix=_YK_CHECK_PREFIX,
        error_name=_YK_ERROR,
        qr_filename=_YK_QR_FILE,
        back_callback='pay_qr',
        loading_text=_YK_LOADING,
    )


async def _yookassa_referral_amount(order: dict, state: FSMContext) -> int:
    """
    Расчёт реферального вознаграждения для ЮКассы.

    При частичной оплате (баланс + QR) — берём remaining_cents из FSM state,
    иначе — стандартная цена тарифа.
    """
    state_data = await state.get_data()
    remaining_cents = state_data.get('remaining_cents', 0)
    if remaining_cents > 0:
        return remaining_cents
    from database.requests import get_tariff_by_id
    _tariff = get_tariff_by_id(order.get('tariff_id'))
    return int((_tariff.get('price_rub', 0) or 0) * 100) if _tariff else 0


@router.callback_query(F.data.startswith('check_yookassa_qr:'))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    """Проверяет статус QR-платежа ЮКасса по нажатию «✅ Я оплатил»."""
    await _run_yookassa_check(
        callback.message, state,
        order_id=callback.data.split(':', 1)[1],
        telegram_id=callback.from_user.id,
        callback=callback,
    )


async def _run_yookassa_check(message, state, order_id: str,
                              telegram_id: int, callback=None) -> None:
    """
    Общая проверка ЮКасса QR-платежа для кнопки «Я оплатил» и deep-link возврата.
    """
    from bot.services.billing import check_yookassa_payment_status

    await check_qr_payment_flow(
        message=message,
        state=state,
        order_id=order_id,
        telegram_id=telegram_id,
        payment_type=_YK_TYPE,
        payment_id_field=_YK_RESULT_KEY,
        check_func=check_yookassa_payment_status,
        pending_hint='Если только что оплатили — подождите пару секунд.',
        callback=callback,
        referral_override_func=_yookassa_referral_amount,
    )


@router.callback_query(F.data.startswith('renew_qr_tariff:'))
async def renew_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты при продлении ключа."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.utils.groups import get_tariffs_for_renewal
    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await callback.answer('😔 Нет тарифов с ценой в рублях', show_alert=True)
        return
    await safe_edit_or_send(callback.message, f"📱 <b>QR-оплата (Карта/СБП)</b>\n\n🔑 Ключ: <b>{escape_html(key['display_name'])}</b>\n\nВыберите тариф для продления:", reply_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_qr=True))
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_qr:'))
async def renew_qr_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для продления ключа."""
    from database.requests import get_tariff_by_id, get_key_details_for_user, save_yookassa_payment_id
    from bot.services.billing import create_yookassa_qr_payment

    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer('❌ Цена в рублях не задана', show_alert=True)
        return

    await create_qr_payment_flow(
        callback=callback, tariff=tariff, price_rub=price_rub,
        payment_type=_YK_TYPE,
        create_func=create_yookassa_qr_payment,
        save_func=save_yookassa_payment_id,
        result_key=_YK_RESULT_KEY,
        title=_YK_TITLE,
        check_prefix=_YK_CHECK_PREFIX,
        error_name=_YK_ERROR,
        qr_filename=_YK_QR_FILE,
        back_callback=f'renew_qr_tariff:{key_id}',
        loading_text=_YK_LOADING,
        key=key, vpn_key_id=key_id,
    )
