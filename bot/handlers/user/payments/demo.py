from aiogram import Router, F
from aiogram.types import CallbackQuery
from bot.utils.page_renderer import render_page
from database.requests import get_all_tariffs, get_tariff_by_id, get_key_details_for_user
from bot.handlers.user.payments.tariff_select_page import show_payment_tariff_select_page

router = Router()
DEMO_PAYMENT_PAGE_KEY = 'demo_payment'


def _callback_bot_username(callback: CallbackQuery) -> str:
    bot = getattr(callback, 'bot', None)
    return (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )


def build_demo_payment_page_context(
    *,
    tariff_name: str,
    price_str: str,
    days: int,
    key_name: str | None = None,
    telegram_id: int | None = None,
    bot_username: str | None = None,
) -> dict:
    from bot.utils.user_ui_texts import render_ui_text

    context = {
        'payment_tariff_name': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_text': render_ui_text('format.days_short', days=days),
    }
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


@router.callback_query(F.data.startswith('demo_tariffs'))
async def demo_tariffs_handler(callback: CallbackQuery):
    """Selecting a tariff for demo payment (New key)."""
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
        
    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await render_page(callback, 'payment_unavailable')
        await callback.answer()
        return
    from bot.utils.page_button_items import build_provider_tariff_button_items

    await show_payment_tariff_select_page(
        callback,
        context={
            'telegram_id': callback.from_user.id,
            'tariff_back_callback': 'buy_key',
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                'demo',
                lambda tariff_id: (
                    f'demo_pay:{tariff_id}:{order_id}'
                    if order_id else f'demo_pay:{tariff_id}'
                ),
            ),
        },
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_demo_tariffs:'))
async def renew_demo_tariffs_handler(callback: CallbackQuery):
    """Selecting a tariff for demo payment (Extension)."""
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    key = get_key_details_for_user(key_id, callback.from_user.id)
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
        
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.page_button_items import build_provider_tariff_button_items

    await show_payment_tariff_select_page(
        callback,
        page_key='renew_payment',
        context={
            'telegram_id': callback.from_user.id,
            'key_id': key_id,
            'tariff_back_callback': f'key_renew:{key_id}',
            'tariff_button_items': build_provider_tariff_button_items(
                tariffs,
                'demo',
                lambda tariff_id: (
                    f'renew_demo_pay:{key_id}:{tariff_id}:{order_id}'
                    if order_id else f'renew_demo_pay:{key_id}:{tariff_id}'
                ),
            ),
            **build_key_page_context(key),
        },
    )
    await callback.answer()


@router.callback_query(F.data.startswith('demo_pay:'))
async def demo_pay_handler(callback: CallbackQuery):
    """Show payment demo screen (New key)."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return

    from bot.services.money import format_money_minor
    price_minor = int(tariff.get('price_minor') or int(float(tariff.get('price_rub') or 0) * 100))

    context = build_demo_payment_page_context(
        tariff_name=tariff['name'],
        price_str=format_money_minor(price_minor, tariff.get('base_currency') or 'RUB'),
        days=int(tariff['duration_days']),
        telegram_id=callback.from_user.id,
        bot_username=_callback_bot_username(callback),
    )
    context.update({
        'payment_methods_callback': 'demo_tariffs',
        'payment_cancel_callback': 'demo_tariffs',
    })
    await render_page(
        callback,
        page_key=DEMO_PAYMENT_PAGE_KEY,
        context=context,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_demo_pay:'))
async def renew_demo_pay_handler(callback: CallbackQuery):
    """Show payment demo screen (Renewal)."""
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await render_page(callback, 'payment_order_unavailable')
        await callback.answer()
        return

    from bot.services.money import format_money_minor
    price_minor = int(tariff.get('price_minor') or int(float(tariff.get('price_rub') or 0) * 100))

    context = build_demo_payment_page_context(
        tariff_name=tariff['name'],
        price_str=format_money_minor(price_minor, tariff.get('base_currency') or 'RUB'),
        days=int(tariff['duration_days']),
        key_name=key['display_name'],
        telegram_id=callback.from_user.id,
        bot_username=_callback_bot_username(callback),
    )
    back_callback = f'renew_demo_tariffs:{key_id}'
    context.update({
        'payment_methods_callback': back_callback,
        'payment_cancel_callback': back_callback,
    })
    await render_page(
        callback,
        page_key=DEMO_PAYMENT_PAGE_KEY,
        context=context,
    )
    await callback.answer()
