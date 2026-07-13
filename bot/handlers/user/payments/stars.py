import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()

@router.callback_query(F.data.startswith('renew_stars_tariff:'))
async def renew_stars_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for renewal (Stars)."""
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
            provider_title_html='⭐ <b>Оплата звёздами</b>',
            instruction_html='😔 Нет доступных тарифов для продления.\n\nПопробуйте позже или обратитесь в поддержку.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='⭐ <b>Оплата звёздами</b>',
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_stars:'))
async def renew_stars_invoice(callback: CallbackQuery, state: FSMContext):
    """Invoice for renewal (Stars)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, get_key_details_for_user, update_order_tariff, update_payment_type
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
    if not user_id:
        return
    if order_id:
        update_order_tariff(order_id, tariff_id)
        update_payment_type(order_id, 'stars')
    else:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='stars', vpn_key_id=key_id)
    from bot.services.promotions import prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment, send_telegram_invoice_or_status
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='stars',
        action='renewal',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='Telegram Stars',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    promo_note = f" Промокод {quote['promo']['code']} -{quote['discount_percent']}%." if quote.get('promo') else ""
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='Telegram Stars',
        log_context=f"stars:renew order={order_id} tariff={tariff.get('id')} key={key_id}",
        title=bot_name,
        description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.{promo_note}",
        payload=f'renew:{order_id}',
        currency='XTR',
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=quote['final_amount'])],
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=f"⭐️ Оплатить {quote['final_amount']} XTR", pay=True))
            .row(InlineKeyboardButton(text='⬅️ Назад', callback_data=f'renew_invoice_cancel:{key_id}:{tariff_id}'))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith('pay_stars'))
async def pay_stars_select_tariff(callback: CallbackQuery):
    """Selecting a Stars payment plan."""
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
                provider_title_html='⭐ <b>Оплата звёздами</b>',
                instruction_html='😔 Нет доступных тарифов.\n\nПопробуйте позже или обратитесь в поддержку.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='⭐ <b>Оплата звёздами</b>',
        ),
        runtime_markup=tariff_select_kb(tariffs, order_id=order_id),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('stars_pay:'))
async def pay_stars_invoice(callback: CallbackQuery, state: FSMContext):
    """Creating an invoice for Stars payment."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, update_order_tariff, update_payment_type
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    days = tariff['duration_days']
    from database.requests import get_user_internal_id, create_pending_order
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='stars')
    else:
        if not user_id:
            await callback.answer('❌ Ошибка пользователя', show_alert=True)
            return
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='stars', vpn_key_id=None)
    from bot.services.promotions import prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment, send_telegram_invoice_or_status
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='stars',
        action='new_key',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='Telegram Stars',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    price_stars = quote['final_amount']
    promo_note = f" Промокод {quote['promo']['code']} -{quote['discount_percent']}%." if quote.get('promo') else ""
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='Telegram Stars',
        log_context=f"stars:new_key order={order_id} tariff={tariff.get('id')}",
        title=bot_name,
        description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).{promo_note}",
        payload=order_id,
        currency='XTR',
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_stars)],
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=f'⭐️ Оплатить {price_stars} XTR', pay=True))
            .row(InlineKeyboardButton(text='❌ Отмена', callback_data='buy_key'))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()
