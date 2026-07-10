import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from bot.utils.text import escape_html, safe_edit_or_send
from database.requests import get_all_tariffs, get_tariff_by_id, get_key_details_for_user
from bot.keyboards.user import tariff_select_kb, renew_tariff_select_kb
from config import ADMIN_IDS
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)

logger = logging.getLogger(__name__)

router = Router()
DEMO_PAYMENT_PAGE_KEY = 'demo_payment'


def default_demo_payment_page_text() -> str:
    """Дефолтный текст демонстрационного экрана оплаты."""
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


def render_demo_payment_page_text(context: dict) -> str:
    try:
        from bot.utils.page_renderer import render_page_text

        text = render_page_text(DEMO_PAYMENT_PAGE_KEY, context=context)
        if text is not None:
            return text
    except Exception as e:
        logger.warning("Не удалось отрендерить страницу %s: %s", DEMO_PAYMENT_PAGE_KEY, e)

    from bot.utils.placeholders import apply_page_placeholders

    fallback_context = {'page_key': DEMO_PAYMENT_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_demo_payment_page_text(),
        context=fallback_context,
        mode='html',
    ) or '(пусто)'


def _demo_payment_runtime_rows(back_callback: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text='⬅️ Назад к тарифами', callback_data=back_callback)],
        [InlineKeyboardButton(text='🈴 На главную', callback_data='start')],
    ]


def build_demo_payment_reply_markup(context: dict, runtime_rows: list[list[InlineKeyboardButton]]):
    from aiogram.types import InlineKeyboardMarkup

    try:
        from bot.utils.page_renderer import build_page_keyboard

        markup = build_page_keyboard(
            DEMO_PAYMENT_PAGE_KEY,
            context=context,
            append_buttons=runtime_rows,
        )
        if markup is not None:
            return markup
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру %s: %s", DEMO_PAYMENT_PAGE_KEY, e)

    return InlineKeyboardMarkup(inline_keyboard=runtime_rows)


def remember_demo_payment_page_context(
    telegram_id: int,
    message,
    context: dict,
    runtime_rows: list[list[InlineKeyboardButton]],
) -> None:
    if telegram_id not in ADMIN_IDS or message is None:
        return
    try:
        from bot.services.page_context import remember_page_context

        render_context = {'page_key': DEMO_PAYMENT_PAGE_KEY}
        render_context.update(context)
        remember_page_context(
            telegram_id,
            page_key=DEMO_PAYMENT_PAGE_KEY,
            message=message,
            context=render_context,
            append_buttons=runtime_rows,
        )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст demo_payment для /yaa: %s", e)


async def rerender_demo_payment_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённый демо-экран оплаты после изменения через /yaa."""
    context = dict(page_context.context or {})
    if not context or not page_context.append_buttons:
        return False

    rendered_message = await safe_edit_or_send(
        page_context.message,
        render_demo_payment_page_text(context),
        reply_markup=build_demo_payment_reply_markup(context, page_context.append_buttons),
    )
    remember_demo_payment_page_context(
        viewer_id,
        rendered_message,
        context,
        page_context.append_buttons,
    )
    return True

@router.callback_query(F.data.startswith('demo_tariffs'))
async def demo_tariffs_handler(callback: CallbackQuery):
    """Выбор тарифа для демонстрационной оплаты (Новый ключ)."""
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
    """Выбор тарифа для демонстрационной оплаты (Продление)."""
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
    """Показ демонстрационного экрана оплаты (Новый ключ)."""
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
    rendered_message = await safe_edit_or_send(
        callback.message,
        render_demo_payment_page_text(context),
        reply_markup=build_demo_payment_reply_markup(context, runtime_rows),
    )
    remember_demo_payment_page_context(
        callback.from_user.id,
        rendered_message,
        context,
        runtime_rows,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('renew_demo_pay:'))
async def renew_demo_pay_handler(callback: CallbackQuery):
    """Показ демонстрационного экрана оплаты (Продление)."""
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
    rendered_message = await safe_edit_or_send(
        callback.message,
        render_demo_payment_page_text(context),
        reply_markup=build_demo_payment_reply_markup(context, runtime_rows),
    )
    remember_demo_payment_page_context(
        callback.from_user.id,
        rendered_message,
        context,
        runtime_rows,
    )
    await callback.answer()
