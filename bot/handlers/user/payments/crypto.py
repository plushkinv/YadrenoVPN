import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.handlers.user.payments.tariff_select_page import (
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()
CRYPTO_PAYMENT_PAGE_KEY = 'crypto_payment'


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
    from bot.utils.text import escape_html
    from bot.utils.user_ui_texts import render_ui_text

    context = {
        'payment_tariff_name': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_text': render_ui_text('format.days_short', days=days),
        'payment_url': str(crypto_url),
        'payment_link_html': f'<a href="{escape_html(str(crypto_url))}">{escape_html(str(crypto_url))}</a>',
        'payment_discount_line_html': _payment_discount_line(promo_lines),
    }
    if key_name:
        from bot.utils.placeholders import KEY_FIELDS_CONTEXT_KEY

        context[KEY_FIELDS_CONTEXT_KEY] = {'name': key_name}
    return context


async def _show_crypto_payment_status(
    callback: CallbackQuery,
    *,
    page_key: str,
) -> None:
    """Shows a concrete page-backed status of crypto-flow."""
    await render_page(callback, page_key)


@router.callback_query(F.data.startswith('renew_crypto_tariff:'))
async def renew_crypto_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for renewal (Crypto)."""
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
                'crypto',
                lambda tariff_id: (
                    f'renew_pay_crypto:{key_id}:{tariff_id}:{order_id}'
                    if order_id else f'renew_pay_crypto:{key_id}:{tariff_id}'
                ),
            ),
            **build_key_page_context(key),
        },
    )
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_crypto:'))
async def renew_crypto_invoice(callback: CallbackQuery, state: FSMContext):
    """Invoice for payment for Crypto (for key renewal)."""
    from database.requests import get_tariff_by_id, get_or_create_user, create_pending_order, get_key_details_for_user, update_order_tariff, update_payment_type, get_setting
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
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
            page_key='payment_unavailable',
        )
        await callback.answer()
        return
    crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, price_cents=quote['final_amount'])
    cb_data = f'renew_crypto_tariff:{key_id}:{order_id}' if order_id else f'renew_crypto_tariff:{key_id}'
    price_usd = quote['final_amount'] / 100
    price_str = f'${price_usd:g}'.replace('.', ',')
    context = build_crypto_payment_page_context(
        title='',
        tariff_name=tariff['name'],
        price_str=price_str,
        days=int(tariff.get('duration_days') or 0),
        crypto_url=crypto_url,
        key_name=key['display_name'],
        promo_lines=describe_quote_lines(quote),
    )
    context.update({
        'order_id': order_id,
        'payment_methods_callback': cb_data,
        'payment_cancel_callback': cb_data,
    })
    context = build_page_flow_context(callback, **context)
    await render_page(
        callback,
        page_key='payment_link_renewal',
        context=context,
    )
    await callback.answer()

@router.callback_query(F.data.startswith('pay_crypto'))
async def pay_crypto_select_tariff(callback: CallbackQuery):
    """Selecting a tariff for Crypto payment."""
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
                'crypto',
                lambda tariff_id: (
                    f'crypto_pay:{tariff_id}:{order_id}'
                    if order_id else f'crypto_pay:{tariff_id}'
                ),
            ),
        },
    )
    await callback.answer()

@router.callback_query(F.data.startswith('crypto_pay:'))
async def pay_crypto_invoice(callback: CallbackQuery, state: FSMContext):
    """Create a link to pay for Crypto (Simple mode)."""
    from database.requests import get_tariff_by_id, update_order_tariff, get_setting, get_or_create_user, create_pending_order
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
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
        update_order_tariff(order_id, tariff_id, payment_type='crypto')
    else:
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
            page_key='payment_unavailable',
        )
        await callback.answer()
        return
    crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, price_cents=quote['final_amount'])
    price_usd = quote['final_amount'] / 100
    price_str = f'${price_usd:g}'.replace('.', ',')
    context = build_crypto_payment_page_context(
        title='',
        tariff_name=tariff['name'],
        price_str=price_str,
        days=int(tariff.get('duration_days') or 0),
        crypto_url=crypto_url,
        key_name=None,
        promo_lines=describe_quote_lines(quote),
    )
    back_callback = f'pay_crypto:{order_id}'
    context.update({
        'order_id': order_id,
        'payment_methods_callback': back_callback,
        'payment_cancel_callback': back_callback,
    })
    context = build_page_flow_context(callback, **context)
    await render_page(
        callback,
        page_key=CRYPTO_PAYMENT_PAGE_KEY,
        context=context,
    )
    await callback.answer()
