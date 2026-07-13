import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.utils.text import escape_html
from bot.handlers.user.payments.base import (
    _format_price_compact,
    _is_cards_via_yookassa_direct,
    send_telegram_invoice_or_status,
)
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()
BALANCE_PAYMENT_PAGE_KEY = 'balance_payment'


def default_balance_payment_page_text() -> str:
    """Default text of the balance payment screen."""
    return (
        "💳 <b>Оплата тарифа «%платеж_тариф%»</b>\n\n"
        "💰 Сумма: %платеж_сумма%\n"
        "%платеж_скидка_строка%"
        "💎 Ваш баланс: %платеж_баланс%\n\n"
        "✅ С баланса будет списано: %платеж_списание_баланса%\n"
        "💳 К оплате: %платеж_остаток_к_оплате%"
        "%платеж_доплата_подсказка%"
    )


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
    hint = ''
    if no_topup_methods:
        hint = (
            '\n\n💡 <b>Для доплаты этой суммы нет подходящего способа оплаты.</b>\n'
            'Поднакопите ещё немного на реферальном балансе\n'
            'или оплатите тариф без использования баланса.'
        )
    return {
        'payment_provider_title_html': '💎 <b>Оплата с баланса</b>',
        'payment_tariff_html': tariff_name,
        'payment_amount_text': price_str,
        'payment_balance_text': balance_str,
        'payment_balance_deduct_text': deduct_str,
        'payment_remaining_text': remaining_str,
        'payment_discount_line_html': _payment_discount_line(promo_lines),
        'payment_topup_hint_html': hint,
    }


async def _show_balance_payment_screen(callback: CallbackQuery, state: FSMContext, tariff_id: int, user_internal_id: int, key_id: int=None):
    """
    Show the payment screen taking into account the balance according to the technical specifications.
    
    Called by the “💎 Use balance” button.
    
    Calculation:
        balance_to_deduct = min(balance, price)
        remaining_cents = price - balance_to_deduct
    
    Saves in FSM state: balance_to_deduct, tariff_price_cents, tariff_id, key_id
    """
    from database.requests import get_tariff_by_id, get_user_balance, is_cards_enabled, is_yookassa_qr_configured
    from bot.keyboards.user import balance_payment_kb
    from bot.services.promotions import build_quote, describe_quote_lines
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    quote = build_quote(
        user_id=user_internal_id,
        tariff=tariff,
        payment_type='balance',
    )
    tariff_price_cents = int(quote['final_amount'])
    if tariff_price_cents <= 0:
        if not quote.get('is_free'):
            await callback.answer('❌ Ошибка: цена тарифа не задана', show_alert=True)
            return
    balance_cents = get_user_balance(user_internal_id)
    balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = max(0, tariff_price_cents - balance_to_deduct)
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id)
    price_str = _format_price_compact(tariff_price_cents)
    balance_str = _format_price_compact(balance_cents)
    deduct_str = _format_price_compact(balance_to_deduct)
    remaining_str = _format_price_compact(remaining_cents)
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    cards_via_yookassa_direct = _is_cards_via_yookassa_direct()
    available_methods = []
    if yookassa_qr_enabled:
        available_methods.append('qr')
    if cards_enabled:
        if cards_via_yookassa_direct:
            available_methods.append('card')
        elif remaining_cents >= 10000:
            available_methods.append('card')
    context = build_balance_payment_page_context(
        tariff_name=escape_html(tariff['name']),
        price_str=price_str,
        balance_str=balance_str,
        deduct_str=deduct_str,
        remaining_str=remaining_str,
        promo_lines=describe_quote_lines(quote),
        no_topup_methods=remaining_cents > 0 and not available_methods,
    )
    context = build_page_flow_context(callback, **context)
    runtime_markup = balance_payment_kb(
        tariff_id=tariff_id,
        key_id=key_id,
        balance_cents=balance_cents,
        tariff_price_cents=tariff_price_cents,
        balance_to_deduct=balance_to_deduct,
        remaining_cents=remaining_cents,
        cards_enabled=cards_enabled,
        yookassa_qr_enabled=yookassa_qr_enabled,
        cards_via_yookassa_direct=cards_via_yookassa_direct,
    )
    runtime_rows = getattr(runtime_markup, 'inline_keyboard', None)
    await render_page(
        callback,
        page_key=BALANCE_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=runtime_rows,
        fallback_text=default_balance_payment_page_text(),
    )
    await callback.answer()

@router.callback_query(F.data == 'pay_use_balance')
async def pay_use_balance_buy_handler(callback: CallbackQuery, state: FSMContext):
    """Selecting a tariff for payment from the balance (new key)."""
    from database.requests import get_all_tariffs, get_user_internal_id, is_referral_enabled, get_referral_reward_type, get_user_balance
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer('❌ Оплата с баланса недоступна', show_alert=True)
        return
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
        return
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html='💎 <b>Оплата с баланса</b>',
                instruction_html='😔 Нет доступных тарифов с ценой в рублях.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💎 <b>Оплата с баланса</b>',
            instruction_html=f'Ваш баланс: <b>{_format_price_compact(balance_cents)}</b>\n\nВыберите тариф:',
        ),
        runtime_markup=tariff_select_kb(rub_tariffs, back_callback='buy_key', is_balance=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('pay_use_balance:'))
async def pay_use_balance_renew_handler(callback: CallbackQuery, state: FSMContext):
    """
    Processing the “Use Balance” button for renewal.
    Callback: pay_use_balance:{key_id}
    """
    from database.requests import get_user_internal_id, get_key_details_for_user, is_referral_enabled, get_referral_reward_type, get_user_balance, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer('❌ Оплата с баланса недоступна', show_alert=True)
        return
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
        return
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html='💎 <b>Оплата с баланса</b>',
                instruction_html='😔 Нет доступных тарифов с ценой в рублях.',
                key_name=key['display_name'],
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💎 <b>Оплата с баланса</b>',
            instruction_html=f'Ваш баланс: <b>{_format_price_compact(balance_cents)}</b>\n\nВыберите тариф:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_balance=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('balance_pay:'))
async def balance_pay_handler(callback: CallbackQuery, state: FSMContext):
    """
    Show the payment screen with the balance after selecting a tariff.
    Callback: balance_pay:{tariff_id} or balance_pay:{tariff_id}:{key_id}
    """
    from database.requests import get_user_internal_id, get_tariff_by_id
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    await _show_balance_payment_screen(callback, state, tariff_id, user_id, key_id=key_id)

@router.callback_query(F.data.startswith('pay_with_balance:'))
async def pay_with_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Full payment from the balance (when remaining_cents == 0).
    Atomic operation: write off + issue a key.
    
    When paying with balance, referral rewards are NOT accrued.
    """
    from database.requests import get_user_balance, get_tariff_by_id, get_or_create_user
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    telegram_id = callback.from_user.id
    (user, _) = get_or_create_user(
        telegram_id,
        callback.from_user.username,
        first_name=getattr(callback.from_user, 'first_name', None),
        last_name=getattr(callback.from_user, 'last_name', None),
    )
    user_internal_id = user['id']
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    from database.requests import create_pending_order
    from bot.handlers.user.payments.base import complete_promo_free_payment
    from bot.services.billing import complete_payment_flow
    from bot.services.promotions import prepare_order_pricing

    (_, order_id) = create_pending_order(
        user_id=user_internal_id,
        tariff_id=tariff_id,
        payment_type='balance',
        vpn_key_id=key_id,
    )
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_internal_id,
        tariff=tariff,
        payment_type='balance',
        action='renewal' if key_id else 'new_key',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='Баланс',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, telegram_id)
        await callback.answer()
        return

    current_balance = get_user_balance(user_internal_id)
    if current_balance < quote['final_amount']:
        await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
        return

    await state.update_data(
        balance_to_deduct=quote['final_amount'],
        tariff_price_cents=quote['final_amount'],
        remaining_cents=0,
        tariff_id=tariff_id,
        key_id=key_id,
    )
    await complete_payment_flow(
        order_id=order_id,
        message=callback.message,
        state=state,
        telegram_id=telegram_id,
        payment_type='balance',
        referral_amount=0,
    )
    await callback.answer()

@router.callback_query(F.data.startswith('pay_card_balance:'))
async def pay_card_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Partial payment: balance + TG payments.
    
    Takes data from FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Creates an invoice for remaining_cents (not for the full fare price!)
    """
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, get_user_balance, create_pending_order, get_setting
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        from bot.handlers.user.payments.status_page import show_payment_configuration_status

        await show_payment_configuration_status(
            callback.message,
            body_text='Попробуйте другой способ доплаты или обратитесь в поддержку.',
            payment_provider_title='TG payments',
        )
        await callback.answer()
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = tariff_price_cents - balance_to_deduct
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id, remaining_cents=remaining_cents)
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=key_id)
    from bot.services.promotions import build_quote
    from database.requests import reserve_promo_for_order, save_order_pricing_snapshot
    quote = build_quote(user_id=user_id, tariff=tariff, payment_type='balance', order_id=order_id)
    save_order_pricing_snapshot(
        order_id=order_id,
        payment_type='cards',
        original_amount=quote['original_amount'],
        discount_amount=quote['discount_amount'],
        final_amount=remaining_cents,
        amount_unit='cents',
        promo=quote['promo'],
    )
    if quote.get('promo'):
        reserve_promo_for_order(
            order_id=order_id,
            user_id=user_id,
            promo=quote['promo'],
            payment_type='cards',
            action='renewal' if key_id else 'new_key',
            original_amount=quote['original_amount'],
            discount_amount=quote['discount_amount'],
            final_amount=remaining_cents,
            amount_unit='cents',
        )
    price_rub = remaining_cents / 100
    price_kopecks = remaining_cents
    
    import json
    provider_data = {
        "receipt": {
            "customer": {
                "email": f"user_{order_id}@t.me"
            },
            "items": [
                {
                    "description": f"Доплата за «{tariff['name']}»",
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
    back_cb = f'key_renew:{key_id}' if key_id else 'buy_key'
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='TG payments',
        log_context=f"balance_cards order={order_id} tariff={tariff.get('id')} key={key_id}",
        title=bot_name,
        description=f"Оплата тарифа «{tariff['name']}» ({tariff['duration_days']} дн.).",
        payload=f'vpn_key:{order_id}',
        provider_token=provider_token,
        currency='RUB',
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
        provider_data=json.dumps(provider_data),
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=f'💳 Оплатить {price_rub:.2f} ₽', pay=True))
            .row(InlineKeyboardButton(text='❌ Отмена', callback_data=back_cb))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith('pay_qr_balance:'))
async def pay_qr_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Partial payment: balance + YuKassa.
    
    Takes data from FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Creates an invoice for remaining_cents / 100 rubles (UKassa accepts rubles)
    """
    from database.requests import get_tariff_by_id, get_user_internal_id, get_user_balance, create_pending_order, save_yookassa_payment_id
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from bot.handlers.user.payments.status_page import (
        show_payment_status_message,
    )
    from aiogram.types import BufferedInputFile
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = tariff_price_cents - balance_to_deduct
    remaining_rub = remaining_cents / 100
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id, remaining_cents=remaining_cents)
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='yookassa_qr', vpn_key_id=key_id)
    from bot.services.promotions import build_quote
    from database.requests import reserve_promo_for_order, save_order_pricing_snapshot
    quote = build_quote(user_id=user_id, tariff=tariff, payment_type='balance', order_id=order_id)
    save_order_pricing_snapshot(
        order_id=order_id,
        payment_type='yookassa_qr',
        original_amount=quote['original_amount'],
        discount_amount=quote['discount_amount'],
        final_amount=remaining_cents,
        amount_unit='cents',
        promo=quote['promo'],
    )
    if quote.get('promo'):
        reserve_promo_for_order(
            order_id=order_id,
            user_id=user_id,
            promo=quote['promo'],
            payment_type='yookassa_qr',
            action='renewal' if key_id else 'new_key',
            original_amount=quote['original_amount'],
            discount_amount=quote['discount_amount'],
            final_amount=remaining_cents,
            amount_unit='cents',
        )
    await show_payment_status_message(
        callback.message,
        title_html='⏳ Создаём оплату через ЮКассу...',
        body_html='',
        payment_provider_title='ЮКасса',
    )
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(amount_rub=remaining_rub, order_id=order_id, description=description, bot_name=bot_name)
        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])
        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await show_payment_status_message(
                callback.message,
                title_html='❌ <b>ЮКасса не вернула данные для оплаты</b>',
                body_text='Попробуйте позже.',
                payment_provider_title='ЮКасса',
                reply_markup=home_only_kb(),
            )
            return
        from bot.handlers.user.payments.base import (
            QR_PAYMENT_PAGE_KEY,
            build_qr_payment_page_context,
            default_qr_payment_page_text,
        )
        payment_context = build_qr_payment_page_context(
            title='📱 <b>ЮКасса</b>',
            tariff_name=escape_html(tariff['name']),
            price_str=f"{remaining_rub:.2f} ₽",
            days=tariff['duration_days'],
            qr_url=qr_url,
            key_name=None,
            hint_text=None,
            instruction_text=None,
            promo_lines=None,
        )
        payment_context.setdefault('bot_username', bot_name)
        payment_context = build_page_flow_context(callback, **payment_context)
        photo = BufferedInputFile(qr_image_data, filename='qr.png')
        back_cb = f'key_renew:{key_id}' if key_id else 'buy_key'
        runtime_markup = yookassa_qr_kb(order_id, back_callback=back_cb, qr_url=qr_url)
        runtime_rows = getattr(runtime_markup, 'inline_keyboard', None)
        await render_page(
            callback,
            page_key=QR_PAYMENT_PAGE_KEY,
            context=payment_context,
            append_buttons=runtime_rows,
            force_new=True,
            fallback_text=default_qr_payment_page_text(),
            media_policy='runtime',
            runtime_media=photo,
            runtime_media_type='photo',
        )
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания QR ЮКасса: {e}')
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Ошибка ЮКассы</b>',
            body_html=(
                f'<i>{escape_html(str(e))}</i>\n\n'
                'Попробуйте другой способ оплаты.'
            ),
            payment_provider_title='ЮКасса',
            reply_markup=home_only_kb(),
        )
    await callback.answer()
