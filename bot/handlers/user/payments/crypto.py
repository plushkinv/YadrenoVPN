import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.utils.text import escape_html
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()
CRYPTO_PAYMENT_PAGE_KEY = 'crypto_payment'


def default_crypto_payment_page_text() -> str:
    """Default text of the transition screen to crypto-payment."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "💳 <b>Тариф:</b> %платеж_тариф%\n"
        "💰 <b>Сумма к оплате:</b> %платеж_сумма%\n"
        "%платеж_скидка_строка%"
        "\n%платеж_инструкция%"
    )


def _payment_discount_line(promo_lines: str | None) -> str:
    discount = (promo_lines or '').strip('\n')
    return f'{discount}\n' if discount else ''


def build_crypto_payment_page_context(
    *,
    title: str,
    tariff_name: str,
    price_str: str,
    days: int,
    crypto_url: str,
    key_name: str | None,
    promo_lines: str | None = None,
) -> dict:
    payment_link = f'<a href="{escape_html(str(crypto_url))}">странице оплаты</a>'
    return {
        'payment_provider_title_html': title,
        'payment_key_line_html': f"🔑 <b>Ключ:</b> {key_name}\n" if key_name else '',
        'payment_tariff_html': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_label': 'Продление' if key_name else 'Срок',
        'payment_term_text': f'+{days} дней' if key_name else f'{days} дней',
        'payment_url': str(crypto_url),
        'payment_link_html': payment_link,
        'payment_instruction_html': 'Нажмите кнопку ниже, чтобы перейти к генерации счета в @Ya_SellerBot.',
        'payment_hint_text': '',
        'payment_discount_line_html': _payment_discount_line(promo_lines),
    }


def _crypto_payment_runtime_rows(crypto_url: str, back_callback: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text='💰 Перейти к оплате', url=crypto_url)],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data=back_callback)],
    ]


async def _show_crypto_payment_status(
    callback: CallbackQuery,
    *,
    title_html: str,
    body_html: str | None = None,
    body_text: str | None = None,
) -> None:
    """Shows the page-backed status of crypto-flow."""
    from bot.handlers.user.payments.status_page import show_payment_status_message
    from bot.keyboards.admin import home_only_kb

    await show_payment_status_message(
        callback.message,
        title_html=title_html,
        body_html=body_html,
        body_text=body_text,
        payment_provider_title='Crypto',
        reply_markup=home_only_kb(),
    )


@router.callback_query(F.data.startswith('renew_crypto_tariff:'))
async def renew_crypto_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for renewal (Crypto)."""
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
            provider_title_html='💰 <b>Оплата криптовалютой</b>',
            instruction_html='😔 Нет доступных тарифов для продления.\n\nПопробуйте позже или обратитесь в поддержку.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💰 <b>Оплата криптовалютой</b>',
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_crypto=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_crypto:'))
async def renew_crypto_invoice(callback: CallbackQuery, state: FSMContext):
    """Invoice for payment for Crypto (for key renewal)."""
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, get_key_details_for_user, update_order_tariff, update_payment_type, get_setting
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
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
        update_payment_type(order_id, 'crypto')
    else:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='crypto', vpn_key_id=key_id)
    from bot.services.promotions import describe_quote_lines, prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='crypto',
        action='renewal',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='Crypto',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    crypto_item_url = get_setting('crypto_item_url')
    item_id = extract_item_id_from_url(crypto_item_url)
    if not item_id:
        await _show_crypto_payment_status(
            callback,
            title_html='❌ <b>Ошибка настройки крипто-платежей</b>',
            body_text='Попробуйте другой способ оплаты или обратитесь в поддержку.',
        )
        await callback.answer()
        return
    crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, price_cents=quote['final_amount'])
    cb_data = f'renew_crypto_tariff:{key_id}:{order_id}' if order_id else f'renew_crypto_tariff:{key_id}'
    price_usd = quote['final_amount'] / 100
    price_str = f'${price_usd:g}'.replace('.', ',')
    context = build_crypto_payment_page_context(
        title='💰 <b>Продление ключа</b>',
        tariff_name=escape_html(tariff['name']),
        price_str=price_str,
        days=int(tariff.get('duration_days') or 0),
        crypto_url=crypto_url,
        key_name=escape_html(key['display_name']),
        promo_lines=describe_quote_lines(quote),
    )
    context = build_page_flow_context(callback, **context)
    runtime_rows = _crypto_payment_runtime_rows(crypto_url, cb_data)
    await render_page(
        callback,
        page_key=CRYPTO_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=runtime_rows,
        fallback_text=default_crypto_payment_page_text(),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('pay_crypto'))
async def pay_crypto_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for Crypto payment."""
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
                provider_title_html='💰 <b>Оплата криптовалютой</b>',
                instruction_html='😔 Нет доступных тарифов.\n\nПопробуйте позже или обратитесь в поддержку.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='💰 <b>Оплата криптовалютой</b>',
        ),
        runtime_markup=tariff_select_kb(tariffs, order_id=order_id, is_crypto=True),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('crypto_pay:'))
async def pay_crypto_invoice(callback: CallbackQuery, state: FSMContext):
    """Create a link to pay for Crypto (Simple mode)."""
    from database.requests import get_tariff_by_id, update_order_tariff, get_setting, get_user_internal_id, create_pending_order
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='crypto')
    else:
        user_id = get_user_internal_id(callback.from_user.id)
        if not user_id:
            await callback.answer('❌ Ошибка пользователя', show_alert=True)
            return
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='crypto', vpn_key_id=None)
    from bot.services.promotions import describe_quote_lines, prepare_order_pricing
    from bot.handlers.user.payments.base import complete_promo_free_payment
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type='crypto',
        action='new_key',
    )
    if not quote['ok']:
        from bot.handlers.user.payments.status_page import show_payment_unavailable_status

        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title='Crypto',
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return
    crypto_item_url = get_setting('crypto_item_url')
    item_id = extract_item_id_from_url(crypto_item_url)
    if not item_id:
        await _show_crypto_payment_status(
            callback,
            title_html='❌ <b>Ошибка настройки крипто-платежей</b>',
            body_text='Попробуйте другой способ оплаты или обратитесь в поддержку.',
        )
        await callback.answer()
        return
    crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, price_cents=quote['final_amount'])
    price_usd = quote['final_amount'] / 100
    price_str = f'${price_usd:g}'.replace('.', ',')
    context = build_crypto_payment_page_context(
        title='💰 <b>Оплата криптовалютой</b>',
        tariff_name=escape_html(tariff['name']),
        price_str=price_str,
        days=int(tariff.get('duration_days') or 0),
        crypto_url=crypto_url,
        key_name=None,
        promo_lines=describe_quote_lines(quote),
    )
    context = build_page_flow_context(callback, **context)
    runtime_rows = _crypto_payment_runtime_rows(crypto_url, f'pay_crypto:{order_id}')
    await render_page(
        callback,
        page_key=CRYPTO_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=runtime_rows,
        fallback_text=default_crypto_payment_page_text(),
    )
    await callback.answer()
