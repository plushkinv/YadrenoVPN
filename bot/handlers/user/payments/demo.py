from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from bot.utils.page_renderer import render_page
from bot.utils.text import escape_html
from database.requests import get_all_tariffs, get_tariff_by_id, get_key_details_for_user
from bot.keyboards.user import tariff_select_kb, renew_tariff_select_kb
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

router = Router()
DEMO_PAYMENT_PAGE_KEY = 'demo_payment'


def default_demo_payment_page_text() -> str:
    """Default text of the payment demo screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_инструкция%\n\n"
        "%платеж_ключ_строка%"
        "📦 <b>Тариф:</b> %платеж_тариф%\n"
        "📅 <b>%платеж_срок_тип%:</b> %платеж_срок%\n"
        "💰 <b>Сумма:</b> %платеж_сумма%\n\n"
        "<i>%платеж_подсказка%</i>"
    )


def _format_demo_price(price_rub: float) -> str:
    return f'{price_rub:g} ₽'


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
    context = {
        'payment_provider_title_html': '🏦 <b>Демонстрационная оплата</b>',
        'payment_key_line_html': f"🔑 <b>Ключ:</b> {key_name}\n" if key_name else '',
        'payment_tariff_html': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_label': 'Продление' if key_name else 'Срок',
        'payment_term_text': f'+{days} дн.' if key_name else f'{days} дн.',
        'payment_instruction_html': 'Это демо-режим. Реального списания не происходит.',
        'payment_hint_text': 'В рабочем режиме здесь появится форма оплаты российской картой.',
    }
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


def _demo_payment_runtime_rows(back_callback: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text='⬅️ Назад к тарифами', callback_data=back_callback)],
        [InlineKeyboardButton(text='🈴 На главную', callback_data='start')],
    ]


@router.callback_query(F.data.startswith('demo_tariffs'))
async def demo_tariffs_handler(callback: CallbackQuery):
    """Selecting a tariff for demo payment (New key)."""
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
        
    tariffs = get_all_tariffs(include_hidden=False)
    
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='🏦 <b>Демо оплата (РФ карта)</b>',
            instruction_html='Выберите тариф:\n\n<i>Этот способ используется только для демонстрации интерфейса оплаты.</i>',
        ),
        runtime_markup=tariff_select_kb(tariffs, order_id=order_id, is_demo=True),
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
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
        
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    if not tariffs:
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html='🏦 <b>Демо оплата РФ картой</b>',
            instruction_html='😔 Нет доступных тарифов для продления.\n\nПопробуйте позже или обратитесь в поддержку.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return
        
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html='🏦 <b>Демо оплата (РФ карта)</b>',
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_demo=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('demo_pay:'))
async def demo_pay_handler(callback: CallbackQuery):
    """Show payment demo screen (New key)."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)

    context = build_demo_payment_page_context(
        tariff_name=escape_html(tariff['name']),
        price_str=_format_demo_price(price_rub),
        days=int(tariff['duration_days']),
        telegram_id=callback.from_user.id,
        bot_username=_callback_bot_username(callback),
    )
    runtime_rows = _demo_payment_runtime_rows('demo_tariffs')
    await render_page(
        callback,
        page_key=DEMO_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=runtime_rows,
        fallback_text=default_demo_payment_page_text(),
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
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)

    context = build_demo_payment_page_context(
        tariff_name=escape_html(tariff['name']),
        price_str=_format_demo_price(price_rub),
        days=int(tariff['duration_days']),
        key_name=escape_html(key['display_name']),
        telegram_id=callback.from_user.id,
        bot_username=_callback_bot_username(callback),
    )
    runtime_rows = _demo_payment_runtime_rows(f'renew_demo_tariffs:{key_id}')
    await render_page(
        callback,
        page_key=DEMO_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=runtime_rows,
        fallback_text=default_demo_payment_page_text(),
    )
    await callback.answer()
