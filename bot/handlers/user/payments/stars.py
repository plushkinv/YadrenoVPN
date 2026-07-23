import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from bot.handlers.user.payments.tariff_select_page import (
    show_payment_tariff_select_page,
)
from bot.utils.page_renderer import render_page

logger = logging.getLogger(__name__)

router = Router()

@router.callback_query(F.data.startswith('renew_stars_tariff:'))
async def renew_stars_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for renewal (Stars)."""
    from database.requests import get_key_details_for_user
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_button_items import build_provider_tariff_button_items
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    if not tariffs:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        page_key='renew_payment',
        context={
            'telegram_id': telegram_id,
            'key_id': key_id,
            'tariff_back_callback': f'key_renew:{key_id}',
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                'stars',
                lambda tariff_id: (
                    f'renew_pay_stars:{key_id}:{tariff_id}:{order_id}'
                    if order_id else f'renew_pay_stars:{key_id}:{tariff_id}'
                ),
            ),
            **build_key_page_context(key),
        },
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_stars:'))
async def renew_stars_invoice(callback: CallbackQuery, state: FSMContext):
    """Invoice for renewal (Stars)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_or_create_user, create_pending_order, get_key_details_for_user, update_order_tariff, update_payment_type
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    user_id = int(user['id'])
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
    from bot.utils.payment_invoice import (
        clamp_invoice_text,
        invoice_change_method_button,
        invoice_pay_button,
        renewal_invoice_description,
    )

    description = renewal_invoice_description(key['display_name'], tariff['name'])
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='Telegram Stars',
        log_context=f"stars:renew order={order_id} tariff={tariff.get('id')} key={key_id}",
        title=clamp_invoice_text(bot_name, 32),
        description=clamp_invoice_text(description, 255),
        payload=f'renew:{order_id}',
        currency='XTR',
        prices=[LabeledPrice(label=clamp_invoice_text(description, 80), amount=quote['final_amount'])],
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=invoice_pay_button(f"{quote['final_amount']} XTR"), pay=True))
            .row(InlineKeyboardButton(text=invoice_change_method_button(), callback_data=f'renew_invoice_cancel:{key_id}:{tariff_id}'))
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
    from bot.utils.page_button_items import build_provider_tariff_button_items
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context={
            'telegram_id': callback.from_user.id,
            'tariff_back_callback': 'buy_key',
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                'stars',
                lambda tariff_id: (
                    f'stars_pay:{tariff_id}:{order_id}'
                    if order_id else f'stars_pay:{tariff_id}'
                ),
            ),
        },
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
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return
    days = tariff['duration_days']
    from database.requests import get_or_create_user, create_pending_order
    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    user_id = int(user['id'])
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='stars')
    else:
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
    from bot.utils.payment_invoice import (
        clamp_invoice_text,
        invoice_change_method_button,
        invoice_pay_button,
        purchase_invoice_description,
    )

    description = purchase_invoice_description(tariff['name'], days)
    invoice_sent = await send_telegram_invoice_or_status(
        callback,
        provider_title='Telegram Stars',
        log_context=f"stars:new_key order={order_id} tariff={tariff.get('id')}",
        title=clamp_invoice_text(bot_name, 32),
        description=clamp_invoice_text(description, 255),
        payload=order_id,
        currency='XTR',
        prices=[LabeledPrice(label=clamp_invoice_text(description, 80), amount=price_stars)],
        reply_markup=(
            InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text=invoice_pay_button(f'{price_stars} XTR'), pay=True))
            .row(InlineKeyboardButton(text=invoice_change_method_button(), callback_data='buy_key'))
            .as_markup()
        ),
    )
    if not invoice_sent:
        return
    await callback.message.delete()
    await callback.answer()
