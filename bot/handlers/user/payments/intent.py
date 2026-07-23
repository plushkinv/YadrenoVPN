"""Common tariff-first payment-intent UI for every core payment purpose."""
from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.payment_intents import (
    PURPOSE_KEY_PURCHASE,
    PURPOSE_KEY_RENEWAL,
    create_payment_intent,
    format_base_minor,
    load_payment_intent,
    quote_payment_intent,
)
from bot.services.payment_provider_adapters import (
    check_provider_invoice,
    create_provider_invoice,
    get_payment_provider_adapter,
    list_payment_provider_adapters,
)
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.utils.user_ui_texts import render_ui_text
from database.requests import (
    get_key_details_for_user,
    get_setting,
    get_tariff_by_id,
    get_user_balance,
    get_or_create_user,
    is_referral_enabled,
    get_referral_reward_type,
    save_payment_balance_deduction,
    update_payment_type,
    update_payment_intent_quote,
)

logger = logging.getLogger(__name__)
router = Router()


async def show_payment_method_select(
    target,
    intent,
    *,
    telegram_id: int,
) -> None:
    """Renders the common editable method page for an owned intent."""
    providers = list_payment_provider_adapters(intent, telegram_id=telegram_id)
    allow_balance = (
        intent.purpose != 'balance_topup'
        and intent.balance_deduct_minor <= 0
        and get_user_balance(intent.user_id) > 0
        and _balance_spending_enabled()
    )
    payable = max(0, int(intent.payable_amount_minor or 0))
    price_line = ""
    if payable != intent.nominal_amount_minor:
        price_line = render_ui_text(
            "payment.quote.price_line",
            old_price=format_base_minor(intent.nominal_amount_minor, intent.base_currency),
            new_price=format_base_minor(payable, intent.base_currency),
        )
    page_key = _intent_method_page_key(intent)
    key_fields = _intent_key_fields(intent, telegram_id)
    custom_rows = [
        [InlineKeyboardButton(
            text=str(provider.label),
            callback_data=f"payment_intent_provider:{intent.order_id}:{provider.provider_id}",
        )]
        for provider in providers
        if provider.custom
    ]
    await render_page(
        target,
        page_key=page_key,
        context=build_page_flow_context(
            target,
            order_id=intent.order_id,
            telegram_id=telegram_id,
            payment_purpose=intent.purpose,
            payment_provider_ids=[provider.provider_id for provider in providers if not provider.custom],
            payment_allow_balance=allow_balance,
            payment_amount_text=format_base_minor(payable, intent.base_currency),
            payment_nominal_text=format_base_minor(intent.nominal_amount_minor, intent.base_currency),
            payment_balance_deduct_text=format_base_minor(intent.balance_deduct_minor, intent.base_currency),
            payment_remaining_text=format_base_minor(payable, intent.base_currency),
            payment_tariff_name=_intent_tariff_name(intent),
            payment_discount_line_html=price_line,
            key_fields=key_fields,
        ),
        append_buttons=custom_rows or None,
    )


async def start_payment_intent_method_selection(
    target,
    state: FSMContext,
    intent,
    *,
    telegram_id: int,
) -> None:
    """Reserves the active promotion in base money before showing providers."""
    preview = quote_payment_intent(intent.order_id, 'balance')
    message = target.message if isinstance(target, CallbackQuery) else target
    if preview.unavailable_reason or not preview.raw.get('ok', True):
        logger.warning(
            "Payment Intent quote unavailable order=%s: %s",
            intent.order_id,
            preview.unavailable_reason,
        )
        await render_page(message, page_key="payment_unavailable")
        return
    if preview.is_free:
        update_payment_type(intent.order_id, 'promo_free')
        from bot.services.billing import complete_payment_flow

        await complete_payment_flow(
            order_id=intent.order_id,
            message=message,
            state=state,
            telegram_id=telegram_id,
            payment_type='promo_free',
            referral_amount=0,
        )
        return
    prepared = load_payment_intent(intent.order_id)
    if prepared is None:
        logger.error("Prepared Payment Intent cannot be loaded: %s", intent.order_id)
        await render_page(message, page_key="payment_order_unavailable")
        return
    await show_payment_method_select(
        target,
        prepared,
        telegram_id=telegram_id,
    )


@router.callback_query(F.data.startswith('payment_intent_tariff:'))
async def payment_intent_tariff_handler(callback: CallbackQuery, state: FSMContext):
    """Creates a trusted intent after purchase/renewal tariff selection."""
    try:
        _, purpose, tariff_raw, key_raw = callback.data.split(':', 3)
        tariff_id = int(tariff_raw)
        key_id = int(key_raw or 0)
    except (TypeError, ValueError):
        await _render_callback_page(callback, "action_unavailable")
        return
    if purpose not in {PURPOSE_KEY_PURCHASE, PURPOSE_KEY_RENEWAL}:
        await _render_callback_page(callback, "action_unavailable")
        return

    tariff = get_tariff_by_id(tariff_id)
    user_id = _get_or_create_internal_user_id(callback)
    if not tariff or not tariff.get('is_active'):
        await _render_callback_page(callback, "action_unavailable")
        return
    purpose_data = {'tariff_id': tariff_id}
    if purpose == PURPOSE_KEY_RENEWAL:
        key = get_key_details_for_user(key_id, callback.from_user.id)
        if not key:
            await _render_callback_page(callback, "key_not_found")
            return
        purpose_data['key_id'] = key_id

    intent = create_payment_intent(
        user_id=user_id,
        purpose=purpose,
        purpose_data=purpose_data,
    )
    await start_payment_intent_method_selection(
        callback,
        state,
        intent,
        telegram_id=callback.from_user.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('payment_intent_methods:'))
async def payment_intent_methods_handler(callback: CallbackQuery):
    """Returns from an invoice to the provider list of the same intent."""
    intent = await _owned_intent(callback)
    if not intent:
        return
    await show_payment_method_select(
        callback,
        intent,
        telegram_id=callback.from_user.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith('payment_intent_cancel:'))
async def payment_intent_cancel_handler(callback: CallbackQuery, state: FSMContext):
    """Returns through the validated declarative cancel target of the intent."""
    intent = await _owned_intent(callback)
    if not intent:
        return

    target = intent.navigation.cancel_target
    if intent.purpose == 'balance_topup' and target.kind == 'page' and target.value == 'balance_topup_amount':
        from bot.utils.action_dispatcher import dispatch_core_action

        await dispatch_core_action(
            callback,
            'balance.topup.start',
            source='callback',
            state=state,
        )
        return
    if intent.purpose == PURPOSE_KEY_PURCHASE and target.kind == 'page' and target.value == 'prepayment':
        from bot.utils.action_dispatcher import dispatch_core_action

        await dispatch_core_action(
            callback,
            'key.purchase.start',
            source='callback',
            state=state,
        )
        return
    if intent.purpose == PURPOSE_KEY_RENEWAL and target.kind == 'page' and target.value == 'renew_payment':
        from bot.utils.action_dispatcher import dispatch_core_action

        await dispatch_core_action(
            callback,
            'key.renew.start',
            {'key_id': int(intent.purpose_data.get('key_id') or 0)},
            source='callback',
            state=state,
        )
        return

    from bot.utils.extension_rendering import render_extension_page, render_extension_route

    render_target = render_extension_page if target.kind == 'page' else render_extension_route
    rendered, answered = await render_target(
        callback,
        target.value,
        {
            'order_id': intent.order_id,
            'payment_purpose': intent.purpose,
            'payment_nominal_text': format_base_minor(intent.nominal_amount_minor, intent.base_currency),
            'payment_amount_text': format_base_minor(intent.payable_amount_minor, intent.base_currency),
        },
    )
    if not rendered:
        await render_page(callback, page_key="screen_unavailable")
        await callback.answer()
    elif not answered:
        await callback.answer()


@router.callback_query(F.data.startswith('payment_intent_provider:'))
async def payment_intent_provider_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Quotes the selected provider, creates its invoice and renders one screen."""
    intent = await _owned_intent(callback)
    if not intent:
        return
    provider_id = callback.data.rsplit(':', 1)[-1]
    adapter = get_payment_provider_adapter(provider_id)
    available_ids = {
        item.provider_id
        for item in list_payment_provider_adapters(
            intent,
            telegram_id=callback.from_user.id,
        )
    }
    if not adapter or provider_id not in available_ids:
        await _render_callback_page(callback, "payment_unavailable")
        return
    if adapter.presentation == 'placeholder':
        tariff = get_tariff_by_id(intent.tariff_id) if intent.tariff_id else None
        await render_page(
            callback,
            page_key='demo_payment',
            context=build_page_flow_context(
                callback,
                order_id=intent.order_id,
                payment_purpose=intent.purpose,
                payment_tariff_name=str((tariff or {}).get('name') or f'#{intent.tariff_id or 0}'),
                payment_amount_text=format_base_minor(intent.payable_amount_minor, intent.base_currency),
                payment_term_text=render_ui_text(
                    'format.days_short',
                    days=int((tariff or {}).get('duration_days') or 0),
                ),
            ),
        )
        await callback.answer()
        return

    try:
        quote = quote_payment_intent(intent.order_id, adapter.payment_type)
    except ValueError as error:
        logger.warning(
            'Intent quote unavailable order=%s provider=%s: %s',
            intent.order_id,
            adapter.provider_id,
            error,
        )
        await _show_unavailable(callback)
        return
    if quote.unavailable_reason or not quote.raw.get('ok', True):
        logger.warning(
            "Payment provider unavailable order=%s provider=%s: %s",
            intent.order_id,
            adapter.provider_id,
            quote.unavailable_reason,
        )
        await _show_unavailable(callback)
        return
    minimum = _provider_minimum(adapter)
    if not _provider_minimum_satisfied(adapter, quote):
        await _render_callback_page(
            callback,
            "payment_minimum_unavailable",
            payment_minimum_text=_format_provider_minimum(intent, quote, minimum, adapter),
            order_id=intent.order_id,
        )
        return
    if quote.is_free:
        update_payment_type(intent.order_id, 'promo_free')
        await _complete_intent(
            callback,
            state,
            intent.order_id,
            payment_type='promo_free',
            referral_amount=0,
        )
        return

    bot_info = await callback.bot.get_me()
    invoice = await create_provider_invoice(
        load_payment_intent(intent.order_id) or intent,
        quote,
        telegram_id=callback.from_user.id,
        bot_username=bot_info.username or '',
    )
    if invoice.status == 'succeeded':
        await _complete_intent(
            callback,
            state,
            intent.order_id,
            payment_type=adapter.payment_type,
            referral_amount=0,
        )
        return
    if invoice.presentation == 'telegram_invoice':
        await _send_telegram_invoice(callback, intent, quote, adapter, bot_info.first_name)
    else:
        await _render_link_invoice(callback, intent, quote, adapter, invoice)
    await callback.answer()


@router.callback_query(F.data.startswith('payment_intent_check:'))
async def payment_intent_check_handler(callback: CallbackQuery, state: FSMContext):
    """Uses the same provider checker and fulfillment dispatcher as polling/webhooks."""
    intent = await _owned_intent(callback)
    if not intent:
        return
    try:
        status = await check_provider_invoice(intent)
    except Exception as error:
        logger.warning('Manual intent check failed order=%s: %s', intent.order_id, error)
        await _render_callback_page(callback, "payment_failed", order_id=intent.order_id)
        return
    if status == 'succeeded':
        await _complete_intent(
            callback,
            state,
            intent.order_id,
            payment_type=intent.payment_type or '',
            referral_amount=0,
        )
        return
    if status == 'canceled':
        await _render_callback_page(callback, "payment_canceled", order_id=intent.order_id)
        return
    await _render_callback_page(callback, "payment_pending", order_id=intent.order_id)


@router.callback_query(F.data.startswith('payment_intent_balance:'))
async def payment_intent_balance_handler(callback: CallbackQuery, state: FSMContext):
    """Applies internal balance as a funding component, never as top-up payment."""
    intent = await _owned_intent(callback)
    if not intent:
        return
    await apply_payment_intent_balance(callback, state, intent)


async def apply_payment_intent_balance(callback: CallbackQuery, state: FSMContext, intent) -> None:
    """Apply balance to a validated intent and render the next database-backed screen."""
    if intent.purpose == 'balance_topup' or not _balance_spending_enabled():
        await _render_callback_page(callback, "action_unavailable")
        return
    try:
        quote = quote_payment_intent(intent.order_id, 'balance')
    except ValueError as error:
        logger.warning("Balance intent cannot be quoted order=%s: %s", intent.order_id, error)
        await _render_callback_page(callback, "payment_order_unavailable")
        return
    if quote.unavailable_reason:
        logger.warning(
            "Balance quote unavailable order=%s: %s",
            intent.order_id,
            quote.unavailable_reason,
        )
        await _show_unavailable(callback)
        return
    balance = get_user_balance(intent.user_id)
    deduction = min(balance, quote.payable_amount_minor)
    if deduction <= 0:
        await _render_callback_page(
            callback,
            "balance_insufficient",
            order_id=intent.order_id,
            payment_balance_text=format_base_minor(balance, intent.base_currency),
            payment_amount_text=format_base_minor(quote.payable_amount_minor, intent.base_currency),
        )
        return
    save_payment_balance_deduction(intent.order_id, deduction)
    remaining = max(0, quote.payable_amount_minor - deduction)
    if remaining == 0:
        update_payment_intent_quote(
            intent.order_id,
            payment_type='balance',
            payable_amount_minor=0,
            charge_amount='0',
            charge_currency=intent.base_currency,
            rate_snapshot=dict(quote.rate_snapshot),
            compatibility_amount_cents=0,
            compatibility_amount_stars=0,
        )
        await _complete_intent(
            callback,
            state,
            intent.order_id,
            payment_type='balance',
            referral_amount=0,
        )
        return
    quote_payment_intent(intent.order_id, 'balance')
    updated = load_payment_intent(intent.order_id)
    if not updated:
        await _render_callback_page(callback, "payment_failed", order_id=intent.order_id)
        return
    await show_payment_method_select(
        callback,
        updated,
        telegram_id=callback.from_user.id,
    )
    await callback.answer()


async def _send_telegram_invoice(callback, intent, quote, adapter, bot_name: str) -> None:
    from bot.handlers.user.payments.base import send_telegram_invoice_or_status
    from bot.utils.payment_invoice import (
        clamp_invoice_text,
        invoice_change_method_button,
        invoice_pay_button,
    )

    amount = int(quote.raw.get('final_amount') or 0)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=invoice_pay_button(_format_charge(quote)),
        pay=True,
    ))
    builder.row(InlineKeyboardButton(
        text=invoice_change_method_button(),
        callback_data=f'payment_intent_methods:{intent.order_id}',
    ))
    kwargs = {
        'title': clamp_invoice_text(bot_name, 32),
        'description': clamp_invoice_text(intent.description, 255),
        'payload': intent.order_id,
        'currency': quote.charge_currency,
        'prices': [LabeledPrice(label=clamp_invoice_text(intent.description, 80), amount=amount)],
        'reply_markup': builder.as_markup(),
    }
    if adapter.provider_id == 'cards':
        kwargs['provider_token'] = get_setting('cards_provider_token', '')
        kwargs['provider_data'] = json.dumps({
            'receipt': {
                'customer': {'email': f'user_{intent.order_id}@t.me'},
                'items': [{
                    'description': clamp_invoice_text(intent.description, 128),
                    'quantity': '1.00',
                    'amount': {
                        'value': f'{quote.charge_amount:.2f}',
                        'currency': 'RUB',
                    },
                    'vat_code': 1,
                    'payment_mode': 'full_prepayment',
                    'payment_subject': 'service',
                }],
            },
        }, ensure_ascii=False)
    await send_telegram_invoice_or_status(
        callback,
        provider_title=adapter.title,
        log_context=f'intent:{intent.order_id}:{adapter.provider_id}',
        **kwargs,
    )


async def _render_link_invoice(callback, intent, quote, adapter, invoice) -> None:
    if not invoice.payment_url:
        raise RuntimeError('Link provider did not return a payment URL')
    page_key = _intent_link_page_key(intent)
    context = build_page_flow_context(
        callback,
        order_id=intent.order_id,
        payment_provider_title=adapter.title,
        payment_tariff_name=_intent_tariff_name(intent),
        payment_amount_text=_format_charge(quote),
        payment_nominal_text=format_base_minor(intent.nominal_amount_minor, intent.base_currency),
        payment_url=invoice.payment_url,
        payment_can_check=adapter.provider_id != 'crypto',
        payment_discount_line_html=_quote_discount_line(quote),
        key_fields=_intent_key_fields(intent, callback.from_user.id),
    )
    runtime_media = None
    if invoice.qr_image_data:
        runtime_media = BufferedInputFile(
            invoice.qr_image_data,
            filename=f'{adapter.provider_id}.png',
        )
    await render_page(
        callback,
        page_key=page_key,
        context=context,
        media_policy='runtime',
        runtime_media=runtime_media,
        runtime_media_type='photo' if runtime_media else None,
    )


async def _complete_intent(
    callback: CallbackQuery,
    state: FSMContext,
    order_id: str,
    *,
    payment_type: str,
    referral_amount: int,
) -> None:
    from bot.services.billing import complete_payment_flow

    await complete_payment_flow(
        order_id=order_id,
        message=callback.message,
        state=state,
        telegram_id=callback.from_user.id,
        payment_type=payment_type,
        referral_amount=referral_amount,
    )
    try:
        await callback.answer()
    except Exception:
        pass


async def _owned_intent(callback: CallbackQuery):
    try:
        order_id = callback.data.split(':', 1)[1].split(':', 1)[0]
    except (AttributeError, IndexError):
        order_id = ''
    intent = load_payment_intent(order_id)
    user_id = _get_or_create_internal_user_id(callback)
    if not intent or intent.user_id != user_id:
        logger.warning(
            "Payment Intent is missing or not owned order=%s telegram_id=%s",
            order_id,
            callback.from_user.id,
        )
        await _render_callback_page(callback, "payment_order_unavailable")
        return None
    return intent


async def _show_unavailable(callback: CallbackQuery) -> None:
    await _render_callback_page(callback, "payment_unavailable")


def _get_or_create_internal_user_id(callback: CallbackQuery) -> int:
    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    return int(user["id"])


async def _render_callback_page(
    callback: CallbackQuery,
    page_key: str,
    **context_values,
) -> None:
    await render_page(
        callback,
        page_key=page_key,
        context=build_page_flow_context(
            callback,
            telegram_id=callback.from_user.id,
            **context_values,
        ),
    )
    await callback.answer()


def _intent_method_page_key(intent) -> str:
    if int(intent.balance_deduct_minor or 0) > 0:
        return "payment_method_select_surcharge"
    if intent.purpose == PURPOSE_KEY_RENEWAL:
        return "payment_method_select_renewal"
    if intent.purpose == "balance_topup":
        return "payment_method_select_topup"
    return "payment_method_select"


def _intent_link_page_key(intent) -> str:
    if intent.purpose == PURPOSE_KEY_RENEWAL:
        return "payment_link_renewal"
    if intent.purpose == "balance_topup":
        return "payment_link_topup"
    return "qr_payment"


def _intent_key_fields(intent, telegram_id: int) -> dict[str, object]:
    key_id = int(intent.purpose_data.get("key_id") or intent.vpn_key_id or 0)
    if key_id <= 0:
        return {}
    key = get_key_details_for_user(key_id, telegram_id)
    return {
        "id": key_id,
        "name": str((key or {}).get("display_name") or f"#{key_id}"),
    }


def _intent_tariff_name(intent) -> str:
    """Return business data for page placeholders, not the stored invoice wording."""
    tariff = get_tariff_by_id(intent.tariff_id) if intent.tariff_id else None
    return str((tariff or {}).get('name') or f'#{intent.tariff_id or 0}')


def _quote_discount_line(quote) -> str:
    if not quote.discount_percent:
        return ""
    lines = []
    if quote.promo_code:
        lines.append(render_ui_text(
            "payment.quote.promo_line",
            promo_code=quote.promo_code,
            discount=quote.discount_percent,
        ))
    lines.append(render_ui_text(
        "payment.quote.price_line",
        old_price=format_base_minor(quote.nominal_amount_minor, quote.base_currency),
        new_price=format_base_minor(quote.payable_amount_minor, quote.base_currency),
    ))
    return "\n".join(lines) + "\n"


def _balance_spending_enabled() -> bool:
    return is_referral_enabled() and get_referral_reward_type() == 'balance'


def _provider_minimum(adapter) -> int:
    minimums = {
        'crypto': 1,
        'stars': 1,
        'cards': 10000,
        'yookassa_qr': 100,
        'wata': 1000,
        'platega': 1000,
        'cardlink': 1000,
    }
    minimum = minimums.get(adapter.provider_id, 0)
    if adapter.custom:
        from bot.utils.payment_provider_registry import get_payment_provider

        provider = get_payment_provider(adapter.provider_id)
        minimum = int(provider.minimum_amount_minor or 0) if provider else 0
    return minimum


def _provider_minimum_satisfied(adapter, quote) -> bool:
    amount = int(quote.raw.get('final_amount') or 0)
    minimum = _provider_minimum(adapter)
    return amount == 0 or amount >= minimum


def _format_provider_minimum(intent, quote, minimum: int, adapter) -> str:
    if adapter.custom:
        return format_base_minor(minimum, intent.base_currency)
    currency = str(quote.charge_currency or intent.base_currency).upper()
    if currency in {"RUB", "USD"}:
        value = f"{minimum / 100:g}".replace(".", ",")
    else:
        value = str(minimum)
    if currency == "RUB":
        return f"{value} ₽"
    if currency == "USD":
        return f"${value}"
    if currency == "XTR":
        return f"{value} ⭐"
    return f"{value} {currency}"


def _format_charge(quote) -> str:
    value = format(quote.charge_amount, 'f')
    if '.' in value:
        value = value.rstrip('0').rstrip('.')
    value = value or '0'
    if quote.charge_currency == 'RUB':
        return f'{value.replace(".", ",")} ₽'
    if quote.charge_currency == 'USD':
        return f'${value.replace(".", ",")}'
    if quote.charge_currency == 'XTR':
        return f'{value} ⭐'
    return f'{value.replace(".", ",")} USDT'


__all__ = [
    'router',
    'show_payment_method_select',
    'start_payment_intent_method_selection',
]
