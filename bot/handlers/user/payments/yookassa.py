import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS
from bot.handlers.user.payments.base import (
    create_qr_payment_flow, check_qr_payment_flow, send_telegram_invoice_or_status
)
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()

# Конфигурация провайдера ЮКасса
_YK_TITLE = '📱 <b>ЮКасса</b>'
_YK_TYPE = 'yookassa_qr'
_YK_ERROR = 'ЮКасса'
_YK_QR_FILE = 'qr.png'
_YK_CHECK_PREFIX = 'check_yookassa_qr'
_YK_RESULT_KEY = 'yookassa_payment_id'
_YK_LOADING = '⏳ Создаём оплату через ЮКассу...'


# ============================================================================
# TG PAYMENTS (историческое внутреннее имя cards)
# ============================================================================

@router.callback_query(F.data.startswith('pay_cards'))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты через TG payments (новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html='💳 <b>TG payments</b>',
                instruction_html='😔 Нет доступных тарифов.\n\nПопробуйте позже или обратитесь в поддержку.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💳 <b>TG payments</b>',
        ),
        runtime_markup=tariff_select_kb(tariffs, order_id=order_id, is_cards=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('cards_pay:'))
async def pay_cards_invoice(callback: CallbackQuery, state: FSMContext):
    """Создание инвойса для оплаты через TG payments (новый ключ)."""
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
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            body_text='Попробуйте другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    days = tariff['duration_days']
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        if not user_id:
            await callback.answer('❌ Ошибка пользователя', show_alert=True)
            return
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=None)
    from bot.services.promotions import prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='cards',
        action='new_key',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    price_kopecks = quote['final_amount']
    price_rub = price_kopecks / 100
    if price_kopecks <= 0:
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            title_html='❌ <b>Цена в рублях не задана</b>',
            body_text='Выберите другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    import json

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

    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    promo_note = f" Промокод {quote['promo']['code']} -{quote['discount_percent']}%." if quote.get('promo') else ""
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='TG payments',
        log_context=f"cards:new_key order={order_id} tariff={tariff.get('id')}",
        title=bot_name,
        description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).{promo_note}",
        payload=f'vpn_key:{order_id}',
        provider_token=provider_token,
        currency='RUB',
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
        provider_data=json.dumps(provider_data),
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=f'💳 Оплатить {price_rub:g} ₽', pay=True))
            .row(InlineKeyboardButton(text='❌ Отмена', callback_data='buy_key'))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
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
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html='💳 <b>TG payments</b>',
            instruction_html='😔 Нет доступных тарифов для продления.\n\nПопробуйте позже или обратитесь в поддержку.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💳 <b>TG payments</b>',
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_cards=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_cards:'))
async def renew_cards_invoice(callback: CallbackQuery, state: FSMContext):
    """Инвойс для продления через TG payments."""
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
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            body_text='Попробуйте другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    if not user_id:
        return
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=key_id)
    from bot.services.promotions import prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='cards',
        action='renewal',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    price_kopecks = quote['final_amount']
    price_rub = price_kopecks / 100
    if price_kopecks <= 0:
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            title_html='❌ <b>Цена в рублях не задана</b>',
            body_text='Выберите другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    import json

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

    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    promo_note = f" Промокод {quote['promo']['code']} -{quote['discount_percent']}%." if quote.get('promo') else ""
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='TG payments',
        log_context=f"cards:renew order={order_id} tariff={tariff.get('id')} key={key_id}",
        title=bot_name,
        description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.{promo_note}",
        payload=f'renew:{order_id}',
        provider_token=provider_token,
        currency='RUB',
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
        provider_data=json.dumps(provider_data),
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=f"💳 Оплатить {price_rub:g} ₽", pay=True))
            .row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'renew_invoice_cancel:{key_id}:{tariff_id}'))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# ЮКАССА (прямой API, refactored → общие функции base.py)
# ============================================================================

@router.callback_query(F.data == 'pay_qr')
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты через ЮКассу (новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html=_YK_TITLE,
                instruction_html='😔 Для оплаты через ЮКассу не настроены цены в рублях.\nОбратитесь к администратору.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_YK_TITLE,
            instruction_html='Выберите тариф:\n\n<i>Оплата через ЮКасса — поддерживает банковские карты и СБП.</i>',
        ),
        runtime_markup=tariff_select_kb(rub_tariffs, is_qr=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('qr_pay:'))
async def qr_pay_create(callback: CallbackQuery, state: FSMContext):
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
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            title_html='❌ <b>Цена в рублях не задана</b>',
            body_text='Выберите другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='ЮКасса QR',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
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
    if order.get('final_amount_cents') is not None:
        return int(order.get('final_amount_cents') or 0)
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
    """Выбор тарифа для оплаты через ЮКассу при продлении ключа."""
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
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html=_YK_TITLE,
            instruction_html='😔 Нет тарифов с ценой в рублях для продления.\nОбратитесь к администратору.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=_YK_TITLE,
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_qr=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_qr:'))
async def renew_qr_create(callback: CallbackQuery, state: FSMContext):
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
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            title_html='❌ <b>Цена в рублях не задана</b>',
            body_text='Выберите другой способ оплаты или обратитесь в поддержку.',
            payment_provider_title='ЮКасса QR',
        )
        await callback.answer()
        return

    await create_qr_payment_flow(
        callback=callback, state=state, tariff=tariff, price_rub=price_rub,
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
