"""Generic handlers для кастомных payment providers расширений."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.utils.page_flow import build_page_flow_context
from bot.utils.text import escape_html, safe_edit_or_send
from bot.handlers.user.payments.tariff_select_page import (
    build_payment_tariff_select_page_context,
    show_payment_no_tariffs_page,
    show_payment_tariff_select_page,
)
from bot.handlers.user.payments.status_page import (
    show_payment_status_message,
    show_payment_unavailable_status,
)

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data.startswith('pe:'))
async def custom_payment_select_tariff(callback: CallbackQuery):
    provider_id = callback.data.split(':', 1)[1]
    provider = _get_available_provider(provider_id, {'telegram_id': callback.from_user.id})
    if provider is None:
        await callback.answer('❌ Способ оплаты недоступен', show_alert=True)
        return

    from database.requests import get_all_tariffs
    from bot.keyboards.user import custom_payment_tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = _eligible_rub_tariffs(get_all_tariffs(include_hidden=False), provider.minimum_amount_cents)
    if not tariffs:
        await show_payment_tariff_select_page(
            callback,
            context=build_payment_tariff_select_page_context(
                provider_title_html=f"💳 <b>{escape_html(provider.title)}</b>",
                instruction_html='😔 Нет тарифов с рублёвой ценой для этого способа оплаты.',
            ),
            runtime_markup=home_only_kb(),
        )
        await callback.answer()
        return

    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=f"💳 <b>{escape_html(provider.title)}</b>",
        ),
        runtime_markup=custom_payment_tariff_select_kb(
            tariffs,
            provider.provider_id,
            minimum_amount_cents=provider.minimum_amount_cents,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('re:'))
async def custom_payment_select_renew_tariff(callback: CallbackQuery):
    parts = callback.data.split(':')
    if len(parts) != 3:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    provider_id = parts[1]
    try:
        key_id = int(parts[2])
    except ValueError:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    provider = _get_available_provider(provider_id, {'telegram_id': callback.from_user.id, 'key_id': key_id})
    if provider is None:
        await callback.answer('❌ Способ оплаты недоступен', show_alert=True)
        return

    from database.requests import get_key_details_for_user
    from bot.keyboards.user import custom_payment_renew_tariff_select_kb
    from bot.utils.groups import get_tariffs_for_renewal

    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return

    tariffs = _eligible_rub_tariffs(
        get_tariffs_for_renewal(key.get('tariff_id', 0)),
        provider.minimum_amount_cents,
    )
    if not tariffs:
        await show_payment_no_tariffs_page(
            callback,
            provider_title_html=f"💳 <b>{escape_html(provider.title)}</b>",
            instruction_html='😔 Нет доступных тарифов для этого способа оплаты.',
            key_name=key['display_name'],
            back_callback=f'key_renew:{key_id}',
        )
        await callback.answer()
        return

    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=f"💳 <b>{escape_html(provider.title)}</b>",
            instruction_html='Выберите тариф для продления:',
            key_name=key['display_name'],
        ),
        runtime_markup=custom_payment_renew_tariff_select_kb(
            tariffs,
            provider.provider_id,
            key_id,
            minimum_amount_cents=provider.minimum_amount_cents,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('pet:'))
async def custom_payment_create(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    if len(parts) != 3:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    provider_id = parts[1]
    try:
        tariff_id = int(parts[2])
    except ValueError:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    await _create_custom_payment(callback, state, provider_id=provider_id, tariff_id=tariff_id)


@router.callback_query(F.data.startswith('ret:'))
async def custom_payment_create_renewal(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    if len(parts) != 4:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    provider_id = parts[1]
    try:
        key_id = int(parts[2])
        tariff_id = int(parts[3])
    except ValueError:
        await callback.answer('❌ Некорректная кнопка оплаты', show_alert=True)
        return
    await _create_custom_payment(
        callback,
        state,
        provider_id=provider_id,
        tariff_id=tariff_id,
        key_id=key_id,
    )


@router.callback_query(F.data.startswith('check_ext:'))
async def custom_payment_check(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(':', 1)[1]

    from database.requests import (
        find_order_by_order_id,
        get_payment_provider_order,
        get_user_internal_id,
        is_order_already_paid,
    )
    from bot.handlers.user.payments.base import finalize_payment_ui
    from bot.keyboards.admin import home_only_kb
    from bot.services.custom_payments import check_custom_payment_order

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.answer('❌ Ордер не найден', show_alert=True)
        return

    owner_user_id = get_user_internal_id(callback.from_user.id)
    if not owner_user_id or int(order.get('user_id') or 0) != int(owner_user_id):
        await callback.answer('❌ Ордер не найден', show_alert=True)
        return

    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await finalize_payment_ui(
            callback.message,
            state,
            '✅ Оплата уже была обработана ранее.',
            order,
            user_id=callback.from_user.id,
        )
        await callback.answer()
        return

    provider_order = get_payment_provider_order(order_id)
    if not provider_order:
        await callback.answer('⚠️ Нет данных о платеже. Попробуйте создать счёт заново.', show_alert=True)
        return
    if provider_order.get('status') == 'succeeded':
        await _complete_custom_payment_flow(callback, state, order, provider_order)
        return
    if provider_order.get('status') == 'canceled':
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Платёж отменён</b>',
            body_text='Попробуйте снова выбрать тариф и создать новый счёт.',
            reply_markup=home_only_kb(),
            force_new=True,
        )
        return

    await callback.answer('🔍 Проверяем платёж...')
    try:
        result = await check_custom_payment_order(provider_order['provider_id'], order)
    except Exception as e:
        logger.warning("Ошибка проверки custom payment %s: %s", order_id, e)
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Не удалось проверить статус платежа</b>',
            body_text='Попробуйте позже.',
            reply_markup=home_only_kb(),
            force_new=True,
        )
        return

    if result['status'] == 'succeeded':
        await _complete_custom_payment_flow(callback, state, order, provider_order)
        return

    if result['status'] == 'canceled':
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Платёж отменён</b>',
            body_text='Попробуйте снова выбрать тариф и создать новый счёт.',
            reply_markup=home_only_kb(),
            force_new=True,
        )
        return

    await show_payment_status_message(
        callback.message,
        title_html='⏳ <b>Платёж ещё не поступил</b>',
        body_text='Оплатите по ссылке и нажмите «✅ Я оплатил» снова.',
        force_new=True,
    )


async def _create_custom_payment(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    provider_id: str,
    tariff_id: int,
    key_id: int | None = None,
) -> None:
    provider = _get_available_provider(provider_id, {'telegram_id': callback.from_user.id, 'key_id': key_id})
    if provider is None:
        await callback.answer('❌ Способ оплаты недоступен', show_alert=True)
        return

    from database.requests import get_key_details_for_user, get_tariff_by_id, get_user_internal_id
    from bot.handlers.user.payments.base import (
        build_qr_payment_page_context,
        build_qr_payment_reply_markup,
        complete_promo_free_payment,
        format_qr_payment_text,
        remember_qr_payment_page_context,
    )
    from bot.keyboards.admin import home_only_kb
    from bot.keyboards.user import qr_payment_kb
    from bot.services.custom_payments import create_custom_payment_order
    from bot.services.promotions import describe_quote_lines, format_amount

    user_id = get_user_internal_id(callback.from_user.id)
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id) if key_id else None
    if not user_id or not tariff or (key_id and not key):
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return

    await show_payment_status_message(
        callback.message,
        title_html='⏳ Создаём ссылку на оплату...',
        body_html='',
        payment_provider_title=provider.title,
    )
    bot_info = await callback.bot.get_me()
    try:
        result = await create_custom_payment_order(
            provider.provider_id,
            user_id=user_id,
            telegram_id=callback.from_user.id,
            tariff=tariff,
            action='renewal' if key_id else 'new_key',
            vpn_key_id=key_id,
            key=key,
            bot_username=bot_info.username,
        )
    except Exception as e:
        logger.warning("Ошибка создания custom payment provider=%s tariff=%s: %s", provider.provider_id, tariff_id, e)
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Ошибка создания платежа</b>',
            body_text='Попробуйте другой способ оплаты или повторите позже.',
            payment_provider_title=provider.title,
            reply_markup=home_only_kb(),
        )
        await callback.answer()
        return

    if not result.get('ok'):
        await show_payment_unavailable_status(
            callback.message,
            str(result.get('reason') or 'Попробуйте позже.'),
            payment_provider_title=provider.title,
        )
        await callback.answer()
        return

    order_id = result['order_id']
    quote = result['quote']
    if result.get('is_free'):
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return

    payment_url = result['payment_url']
    promo_lines = describe_quote_lines(quote)
    payment_context = build_qr_payment_page_context(
        title=f"💳 <b>{escape_html(provider.title)}</b>",
        tariff_name=escape_html(tariff['name']),
        price_str=format_amount(quote['final_amount'], provider.payment_type),
        days=int(tariff.get('duration_days') or 0),
        qr_url=payment_url,
        key_name=escape_html(key['display_name']) if key else None,
        hint_text=None,
        instruction_text='Перейдите по {payment_link} и завершите оплату.',
        promo_lines=promo_lines,
    )
    payment_context.setdefault('bot_username', bot_info.username)
    payment_context = build_page_flow_context(callback, **payment_context)
    text = format_qr_payment_text(
        title=f"💳 <b>{escape_html(provider.title)}</b>",
        tariff_name=escape_html(tariff['name']),
        price_str=format_amount(quote['final_amount'], provider.payment_type),
        days=int(tariff.get('duration_days') or 0),
        qr_url=payment_url,
        key_name=escape_html(key['display_name']) if key else None,
        instruction_text='Перейдите по {payment_link} и завершите оплату.',
        promo_lines=promo_lines,
        telegram_id=payment_context.get('telegram_id'),
        bot_username=payment_context.get('bot_username'),
    )

    runtime_markup = qr_payment_kb(
        order_id,
        'check_ext',
        f"re:{provider.provider_id}:{key_id}" if key_id else f"pe:{provider.provider_id}",
        payment_url,
    )
    runtime_rows = getattr(runtime_markup, 'inline_keyboard', None)
    reply_markup = build_qr_payment_reply_markup(payment_context, runtime_rows) or runtime_markup
    rendered_message = await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=reply_markup,
        force_new=True,
    )
    remember_qr_payment_page_context(
        callback.from_user.id,
        rendered_message,
        payment_context,
        reply_markup,
        append_buttons=runtime_rows,
    )
    await callback.answer()


def _get_available_provider(provider_id: str, context: dict | None = None):
    from bot.utils.payment_provider_registry import get_payment_provider, is_payment_provider_enabled

    try:
        provider = get_payment_provider(provider_id)
    except ValueError:
        return None
    if provider is None:
        return None
    if not is_payment_provider_enabled(provider.provider_id, context or {}):
        return None
    return provider


async def _complete_custom_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
    order: dict,
    provider_order: dict,
) -> None:
    from bot.services.billing import complete_payment_flow

    await complete_payment_flow(
        order_id=str(order.get('order_id') or ''),
        message=callback.message,
        state=state,
        telegram_id=callback.from_user.id,
        payment_type=str(order.get('payment_type') or provider_order.get('payment_type')),
        referral_amount=_custom_payment_referral_amount(order),
    )


def _custom_payment_referral_amount(order: dict) -> int:
    try:
        if order.get('final_amount_cents') is not None:
            return int(order.get('final_amount_cents') or 0)
        return int(order.get('amount_cents') or 0)
    except (TypeError, ValueError):
        return 0


def _eligible_rub_tariffs(tariffs: list[dict], minimum_amount_cents: int) -> list[dict]:
    min_rub = minimum_amount_cents / 100
    return [
        tariff for tariff in tariffs
        if float(tariff.get('price_rub') or 0) > 0
        and float(tariff.get('price_rub') or 0) >= min_rub
    ]
