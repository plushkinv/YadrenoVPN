import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.utils.callbacks import safe_answer_callback

logger = logging.getLogger(__name__)
router = Router()

PAYMENT_DEEPLINK_PREFIX = 'pay_'
PAYMENT_DEEPLINK_PROVIDERS = {'yookassa', 'wata', 'platega', 'cardlink'}
QR_PAYMENT_PAGE_KEY = 'qr_payment'


def parse_payment_deeplink(start_param: str) -> Optional[dict]:
    """
    Parses a single deep-link return from the payment form.

    Format: pay_{provider}_{order_id}
    """
    if not start_param or not start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        return None

    payload = start_param[len(PAYMENT_DEEPLINK_PREFIX):]
    provider, separator, order_id = payload.partition('_')
    if not separator or provider not in PAYMENT_DEEPLINK_PROVIDERS or not order_id:
        return None

    return {
        'provider': provider,
        'order_id': order_id,
    }


async def handle_payment_deeplink(
    message: Message,
    state: FSMContext,
    start_param: str,
    user_internal_id: int,
    telegram_id: int,
) -> bool:
    """
    Processes payment deep-links from /start.

    Returns True if the parameter relates to payments and further processing of /start is not needed.
    """
    if not start_param:
        return False

    async def _show_deeplink_status(page_key: str, *, order_id: str | None = None) -> None:
        context = {'telegram_id': telegram_id}
        if order_id:
            context['order_id'] = order_id
        await render_page(message, page_key, context=context, force_new=True)

    if start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        parsed = parse_payment_deeplink(start_param)
        if not parsed:
            await _show_deeplink_status('payment_order_unavailable')
            return True

        provider = parsed['provider']
        order_id = parsed['order_id']

        from bot.services.payment_intents import load_payment_intent

        intent = load_payment_intent(order_id)
        if intent:
            from bot.services.billing import complete_payment_flow
            from bot.services.payment_provider_adapters import check_provider_invoice
            from database.requests import get_payment_provider_order

            provider_alias = {
                'yookassa': 'yookassa_qr',
                'wata': 'wata',
                'platega': 'platega',
                'cardlink': 'cardlink',
            }
            provider_order = get_payment_provider_order(order_id)
            if (
                intent.user_id != user_internal_id
                or not provider_order
                or provider_order.get('provider_id') != provider_alias.get(provider)
            ):
                await _show_deeplink_status('payment_order_unavailable')
                return True
            try:
                status = await check_provider_invoice(intent)
            except Exception as error:
                logger.warning('Intent deep-link check failed order=%s: %s', order_id, error)
                await _show_deeplink_status('payment_failed', order_id=order_id)
                return True
            if status == 'succeeded':
                await complete_payment_flow(
                    order_id=order_id,
                    message=message,
                    state=state,
                    telegram_id=telegram_id,
                    payment_type=intent.payment_type or '',
                    referral_amount=0,
                )
            elif status == 'canceled':
                await _show_deeplink_status('payment_canceled', order_id=order_id)
            else:
                await _show_deeplink_status('payment_pending', order_id=order_id)
            return True

        if provider == 'yookassa':
            from bot.handlers.user.payments.yookassa import _run_yookassa_check
            await _run_yookassa_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'wata':
            from bot.handlers.user.payments.wata import _run_wata_check
            await _run_wata_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'platega':
            from bot.handlers.user.payments.platega import _run_platega_check
            await _run_platega_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'cardlink':
            from bot.handlers.user.payments.cardlink import _run_cardlink_check
            await _run_cardlink_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        return True

    # Compatible with old Cardlink links from store settings.
    if start_param.startswith('cl_'):
        from database.requests import find_latest_pending_cardlink_order_for_user
        from bot.handlers.user.payments.cardlink import _run_cardlink_check

        order = find_latest_pending_cardlink_order_for_user(user_internal_id)
        if not order:
            await _show_deeplink_status('payment_order_unavailable')
            return True

        await _run_cardlink_check(
            message, state, order_id=order['order_id'],
            telegram_id=telegram_id, callback=None
        )
        return True

    return False


def _format_price_compact(cents: int) -> str:
    """Formats current base minor units compactly."""
    from bot.services.money import format_money_minor

    return format_money_minor(cents)

def _is_cards_via_yookassa_direct() -> bool:
    """
    Checks whether the YuKassa direct script is used for additional payment.
    
    Returns:
        True if the direct script YuKassa is available from 1 ₽,
        False if the Telegram Payments API is used with a minimum of about 100 ₽
    """
    from database.requests import get_setting
    return get_setting('cards_via_yookassa_direct', '0') == '1'


async def complete_promo_free_payment(
    callback: CallbackQuery,
    state: FSMContext,
    order_id: str,
    telegram_id: int,
) -> None:
    """Completes the order with a 100% discount without creating an account with the provider."""
    from database.requests import update_payment_type
    from bot.services.billing import complete_payment_flow

    update_payment_type(order_id, 'promo_free')
    await complete_payment_flow(
        order_id=order_id,
        message=callback.message,
        state=state,
        telegram_id=telegram_id,
        payment_type='promo_free',
        referral_amount=0,
    )

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Confirms legacy invoices and validates ownership/amount for v1 intents."""
    from database.requests import get_or_create_user
    from bot.services.payment_intents import load_payment_intent
    from bot.utils.user_ui_texts import get_ui_text

    order_id = _invoice_order_id(pre_checkout.invoice_payload)
    intent = load_payment_intent(order_id)
    if intent:
        owner, _ = get_or_create_user(
            pre_checkout.from_user.id,
            pre_checkout.from_user.username,
            pre_checkout.from_user.first_name,
            pre_checkout.from_user.last_name,
        )
        owner_id = int(owner["id"])
        expected_amount = _native_invoice_amount(intent)
        if (
            not owner_id
            or owner_id != intent.user_id
            or intent.status != 'pending'
            or pre_checkout.currency != intent.charge_currency
            or int(pre_checkout.total_amount) != expected_amount
        ):
            await pre_checkout.answer(
                ok=False,
                error_message=get_ui_text("payment.invoice.stale_error"),
            )
            return
    await pre_checkout.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, state: FSMContext):
    """
    Processing successful Stars or TG payments.
    
    Delegates general post-payment logic to complete_payment_flow().
    """
    from bot.services.billing import complete_payment_flow
    payment = message.successful_payment
    payload = payment.invoice_payload
    currency = payment.currency
    payment_type = 'stars' if currency == 'XTR' else 'cards'
    logger.info(f'Успешная оплата {payment_type}: {payload}, charge_id={payment.telegram_payment_charge_id}')
    
    order_id = _invoice_order_id(payload)

    from bot.services.payment_intents import load_payment_intent
    intent = load_payment_intent(order_id)
    if intent:
        from database.requests import get_user_internal_id, update_payment_provider_order_status

        owner_id = get_user_internal_id(message.from_user.id)
        if (
            not owner_id
            or owner_id != intent.user_id
            or payment.currency != intent.charge_currency
            or int(payment.total_amount) != _native_invoice_amount(intent)
        ):
            logger.error('Rejected mismatched successful intent payment order=%s', order_id)
            return
        update_payment_provider_order_status(
            order_id,
            'succeeded',
            provider_payment_id=payment.telegram_payment_charge_id,
        )
    
    await complete_payment_flow(
        order_id=order_id,
        message=message,
        state=state,
        telegram_id=message.from_user.id,
        payment_type=payment_type,
        referral_amount=payment.total_amount
    )


def _invoice_order_id(payload: str) -> str:
    """Extracts a core order id from legacy and v1 Telegram invoice payloads."""
    value = str(payload or '')
    if value.startswith('renew:') or value.startswith('vpn_key:'):
        return value.split(':', 1)[1]
    return value


def _native_invoice_amount(intent) -> int:
    """Returns Telegram's minor-unit amount for a persisted native invoice."""
    if intent.charge_amount is None:
        return 0
    if intent.charge_currency == 'XTR':
        return int(intent.charge_amount)
    return int(intent.charge_amount * 100)

async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Completes the UI after successful payment.
    Shows a message and either transfers to the settings (draft) or to the main one.
    """
    from database.requests import get_key_details_for_user, get_user_by_id
    import logging
    logger = logging.getLogger(__name__)
    from bot.handlers.user.payments.keys_config import start_new_key_config
    if order.get('purpose') == 'balance_topup':
        from bot.services.payment_intents import format_base_minor, load_payment_intent
        from bot.utils.extension_rendering import render_extension_page, render_extension_route

        intent = load_payment_intent(str(order.get('order_id') or ''))
        target = intent.navigation.success_target if intent else None
        base_currency = str(
            (intent.base_currency if intent else order.get('base_currency')) or 'RUB'
        )
        extra_context = {
            'telegram_id': user_id,
            'order_id': order.get('order_id'),
            'payment_purpose': order.get('purpose'),
            'payment_nominal_text': format_base_minor(
                int(order.get('nominal_amount_minor') or order.get('nominal_amount_cents') or 0),
                base_currency,
            ),
            'payment_amount_text': format_base_minor(
                int(order.get('payable_amount_minor') or order.get('payable_amount_cents') or 0),
                base_currency,
            ),
        }
        if target and target.kind == 'page':
            await render_extension_page(
                message,
                target.value,
                extra_context,
                force_new_for_message=True,
            )
        else:
            await render_extension_route(
                message,
                target.value if target else 'balance_topup_result',
                extra_context,
                force_new_for_message=True,
            )
        await state.clear()
        return
    key_id = order.get('vpn_key_id')
    logger.info(f"finalize_payment_ui: Order={order.get('order_id')}, Key={key_id}, User={user_id}")
    is_draft = False
    if key_id:
        key = get_key_details_for_user(key_id, user_id)
        if key:
            logger.info(f"Key details found: ID={key['id']}, ServerID={key.get('server_id')}")
            if not key.get('server_id'):
                is_draft = True
        else:
            logger.warning(f'Key {key_id} not found for user {user_id} via details check!')
    else:
        logger.info('No key_id in order object.')
    logger.info(f'Result: is_draft={is_draft}')
    if is_draft:
        owner_internal_id = order.get('user_id')
        if not owner_internal_id:
            raise RuntimeError(f"У заказа {order.get('order_id')} не указан владелец")
        owner = get_user_by_id(owner_internal_id)
        if not owner:
            raise RuntimeError(f"Владелец заказа {order.get('order_id')} не найден")
        if owner.get('telegram_id') != user_id:
            raise RuntimeError(
                f"Владелец заказа {order.get('order_id')} не совпадает с payment flow user_id={user_id}"
            )
        owner_username = owner.get('username')
        await start_new_key_config(
            message,
            state,
            order['order_id'],
            key_id,
            owner_telegram_id=user_id,
            owner_username=owner_username,
        )
    else:
        from bot.utils.key_pages import build_key_page_context
        from bot.utils.user_ui_texts import render_ui_text

        key = get_key_details_for_user(key_id, user_id)
        if not key:
            logger.warning('Paid order %s references a missing key %s', order.get('order_id'), key_id)
            await render_page(message, 'payment_failed', force_new=True)
            await state.clear()
            return
        period = order.get('period_days') or order.get('duration_days') or 0
        await render_page(
            message,
            'key_renewed',
            context={
                'telegram_id': user_id,
                'key_id': key_id,
                'payment_term_text': render_ui_text('format.days_short', days=period),
                **build_key_page_context(key),
            },
            force_new=True,
        )
        await state.clear()


async def send_telegram_invoice_or_status(
    callback: CallbackQuery,
    *,
    provider_title: str,
    log_context: str,
    **invoice_kwargs,
) -> bool:
    """
    Sends Telegram invoice and shows page-backed error if Telegram API
    did not accept the technical request to create an account.
    """
    message = getattr(callback, 'message', None)
    if message is None:
        await callback.answer()
        return False

    try:
        await message.answer_invoice(**invoice_kwargs)
        return True
    except Exception as e:
        error_text = str(e)
        if (
            'CURRENCY_TOTAL_AMOUNT_INVALID' in error_text
            or 'PRICE_TOTAL_AMOUNT_INVALID' in error_text
        ):
            logger.warning(
                "Telegram invoice rejected by amount limit (%s): %s",
                log_context,
                e,
            )
            page_key = 'payment_unavailable'
        else:
            logger.exception("Не удалось создать Telegram invoice (%s).", log_context)
            page_key = 'payment_failed'

        await render_page(message, page_key)
        await callback.answer()
        return False

@router.callback_query(F.data.startswith('renew_invoice_cancel:'))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Cancel the invoice and return to choosing a payment method."""
    from database.requests import get_key_details_for_user
    from bot.handlers.user.keys import show_renew_payment_page
    parts = callback.data.split(':')
    key_id = int(parts[1])
    telegram_id = callback.from_user.id

    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await render_page(callback, 'key_not_found')
        await callback.answer()
        return

    await show_renew_payment_page(callback, key, key_id, force_new=True)
    await callback.answer()


# ============================================================================
# COMMON FUNCTIONS FOR QR PAYMENT PROVIDERS (wata, platega, cardlink, yookassa)
# ============================================================================


def _format_payment_discount_line(promo_lines: str | None) -> str:
    discount = (promo_lines or '').strip('\n')
    return f'{discount}\n' if discount else ''


def build_qr_payment_page_context(
    *,
    title: str,
    tariff_name: str,
    price_str: str,
    days: int,
    qr_url: str,
    key_name: str | None,
    hint_text: str | None,
    instruction_text: str | None,
    promo_lines: str | None,
    telegram_id: int | None = None,
    bot_username: str | None = None,
) -> dict:
    from bot.utils.text import escape_html
    from bot.utils.user_ui_texts import render_ui_text

    safe_url = escape_html(str(qr_url))
    context = {
        'payment_provider_title': title,
        'payment_tariff_name': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_text': render_ui_text('format.days_short', days=days),
        'payment_url': str(qr_url),
        'payment_link_html': f'<a href="{safe_url}">{safe_url}</a>',
        'payment_instruction_html': safe_url,
        'payment_discount_line_html': _format_payment_discount_line(promo_lines),
    }
    if key_name:
        from bot.utils.placeholders import KEY_FIELDS_CONTEXT_KEY

        context[KEY_FIELDS_CONTEXT_KEY] = {'name': key_name}
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


def _message_photo_file_id(message) -> str | None:
    photos = getattr(message, 'photo', None) or []
    if not photos:
        return None
    return getattr(photos[-1], 'file_id', None)


async def rerender_qr_payment_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved QR payment screen after changing via /yaa."""
    context = dict(page_context.context or {})
    if not context:
        return False

    photo_file_id = _message_photo_file_id(page_context.message)

    await render_page(
        page_context.message,
        page_key=QR_PAYMENT_PAGE_KEY,
        context=context,
        append_buttons=page_context.append_buttons,
        media_policy='runtime',
        runtime_media=photo_file_id,
        runtime_media_type='photo',
    )
    return True

async def create_qr_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
    tariff: dict,
    price_rub: float,
    payment_type: str,
    create_func,
    save_func,
    result_key: str,
    title: str,
    check_prefix: str,
    error_name: str,
    qr_filename: str,
    back_callback: str,
    loading_text: str | None = None,
    key: dict = None,
    vpn_key_id: int = None,
    hint_text: str = None,
    instruction_text: str = None,
) -> None:
    """
    A universal flow for creating a QR invoice for any provider.

    Performs: user validation → order creation → provider API call →
    saving the payment ID → generating text → sending a QR photo.

    Args:
        callback: Callback from the tariff selection button
        tariff: Tariff dictionary (already validated)
        price_rub: Amount to be paid in rubles
        payment_type: Payment type ('wata', 'platega', 'cardlink', 'yookassa_qr')
        create_func: Async function for creating a payment (amount_rub, order_id, description, bot_name)
        save_func: Function for saving payment ID in an order (order_id, payment_id)
        result_key: Key in the API result dict (eg 'wata_link_id')
        title: Message title (eg '🌊 <b>WATA Payment</b>')
        check_prefix: Check callback prefix (e.g. 'check_wata')
        error_name: Error provider name (eg 'WATA')
        qr_filename: QR image file name (eg 'wata.png')
        back_callback: Callback of the Back button on the QR screen
        loading_text: Loading text
        key: Key dictionary when renewing (None for purchase)
        vpn_key_id: Key ID when renewing (None for purchase)
        hint_text: Custom hint (None → standard)
        instruction_text: User instructions with payment link
    """
    from database.requests import (
        create_pending_order,
        get_or_create_user,
        schedule_payment_auto_check,
    )
    from bot.services.promotions import describe_quote_lines, format_amount, prepare_order_pricing
    from bot.handlers.user.payments.status_page import show_payment_unavailable_status
    from bot.utils.payment_invoice import purchase_invoice_description, renewal_invoice_description

    user, _ = get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name,
    )
    user_id = int(user['id'])

    # Creating an order
    (_, order_id) = create_pending_order(
        user_id=user_id, tariff_id=tariff['id'],
        payment_type=payment_type, vpn_key_id=vpn_key_id
    )

    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type=payment_type,
        action='renewal' if vpn_key_id else 'new_key',
    )
    if not quote['ok']:
        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title=error_name,
        )
        await safe_answer_callback(callback)
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await safe_answer_callback(callback)
        return

    price_rub = quote['final_amount'] / 100

    await safe_answer_callback(callback)
    await render_page(callback, 'payment_creating')

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username

        # Description for the provider
        if key:
            description = renewal_invoice_description(key['display_name'], tariff['name'])
        else:
            description = purchase_invoice_description(tariff['name'], tariff['duration_days'])

        # Provider API call
        create_kwargs = {
            'amount_rub': price_rub,
            'order_id': order_id,
            'description': description,
            'bot_name': bot_name,
        }
        try:
            import inspect
            signature = inspect.signature(create_func)
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            if accepts_kwargs or 'user_telegram_id' in signature.parameters:
                create_kwargs['user_telegram_id'] = callback.from_user.id
        except (TypeError, ValueError):
            pass
        result = await create_func(**create_kwargs)
        save_result = save_func(order_id, result[result_key])
        if save_result is False:
            raise RuntimeError(
                f'Не удалось сохранить внешний идентификатор {error_name}'
            )
        try:
            schedule_payment_auto_check(order_id, payment_type, first_delay_seconds=120)
        except Exception as queue_error:
            logger.error(
                'Не удалось поставить платёж в очередь автопроверки provider=%s order=%s: %s',
                payment_type,
                order_id,
                queue_error,
                exc_info=True,
            )

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')

        if not qr_image_data or not qr_url:
            logger.warning('Provider %s returned incomplete payment data for order %s', error_name, order_id)
            await render_page(callback, 'payment_failed')
            return

        # Formation of text
        promo_lines = describe_quote_lines(quote)
        payment_context = build_qr_payment_page_context(
            title=title,
            tariff_name=tariff['name'],
            price_str=format_amount(quote['final_amount'], payment_type),
            days=tariff['duration_days'],
            qr_url=qr_url,
            key_name=key['display_name'] if key else None,
            hint_text=hint_text,
            instruction_text=instruction_text,
            promo_lines=promo_lines,
        )
        payment_context.setdefault('bot_username', bot_name)
        payment_context.update({
            'order_id': order_id,
            'payment_check_callback': f'{check_prefix}:{order_id}',
            'payment_methods_callback': f'payment_legacy_methods:{order_id}',
            'payment_cancel_callback': back_callback,
            'payment_can_check': True,
        })
        payment_context = build_page_flow_context(callback, **payment_context)

        # Sending a QR photo
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename=qr_filename)
        await render_page(
            callback,
            page_key='payment_link_renewal' if key else QR_PAYMENT_PAGE_KEY,
            context=payment_context,
            force_new=True,
            media_policy='runtime',
            runtime_media=photo,
            runtime_media_type='photo',
        )
    except Exception as e:
        logger.warning('Не удалось создать платёж %s order=%s: %s', error_name, order_id, e)
        await render_page(callback, 'payment_failed')
async def check_qr_payment_flow(
    message,
    state: FSMContext,
    order_id: str,
    telegram_id: int,
    payment_type: str,
    payment_id_field: str,
    check_func,
    check_arg_is_order_id: bool = False,
    rate_limit_seconds: int = 0,
    rate_limit_prefix: str = '',
    pending_hint: str = None,
    callback: CallbackQuery = None,
    referral_override_func=None,
) -> None:
    """
    Universal flow for checking the status of a QR payment.

    Performs: order search → owner check → “already paid” check →
    payment_id validation → rate-limiting → verification API call → result processing.

    Args:
        message: Message object (callback.message or Message from deep-link)
        state: FSM context
        order_id: Order ID
        telegram_id: Telegram user ID
        payment_type: Payment type ('wata', 'platega', 'cardlink', 'yookassa_qr')
        payment_id_field: Name of the payment ID field in the order ('wata_link_id', 'cardlink_bill_id', ...)
        check_func: Async function for checking status (payment_id) -> str ('succeeded'/'canceled'/...)
        check_arg_is_order_id: True if check_func accepts order_id instead of payment_id (WATA)
        rate_limit_seconds: Rate-limit interval (0 - no limit)
        rate_limit_prefix: Prefix of the rate-limit key in FSM ('wata', 'platega', ...)
        pending_hint: Additional hint in the “pending” status (None → standard)
        callback: CallbackQuery (None for deep-link call)
        referral_override_func: Function(order, state) -> int for non-standard
                                calculation of referral reward (yookassa with balance)
    """
    import time
    from database.requests import (
        find_order_by_order_id, get_or_create_user,
        cancel_pending_order, is_order_already_paid, update_payment_auto_check,
        update_payment_type,
    )
    from bot.services.billing import complete_payment_flow

    async def _show_order_not_found() -> None:
        await render_page(callback or message, 'payment_order_unavailable', force_new=callback is None)
        if callback:
            await safe_answer_callback(callback)

    # 1. Order search
    order = find_order_by_order_id(order_id)
    if not order:
        await _show_order_not_found()
        return

    # 2. Verification of the order owner
    current_user, _ = get_or_create_user(telegram_id)
    owner_user_id = int(current_user['id'])
    if int(order.get('user_id') or 0) != owner_user_id:
        logger.warning(
            'Попытка проверить чужой QR-платёж: order=%s, telegram_id=%s, owner=%s',
            order_id, telegram_id, order.get('user_id')
        )
        await _show_order_not_found()
        return

    # 3. Already paid?
    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await finalize_payment_ui(
            message, state,
            'already_paid',
            order, user_id=telegram_id
        )
        if callback:
            await safe_answer_callback(callback)
        return

    # 4. Payment_id validation
    payment_id = order.get(payment_id_field)
    if not payment_id:
        await render_page(callback or message, 'payment_order_unavailable', force_new=callback is None)
        if callback:
            await safe_answer_callback(callback)
        return

    # 5. Rate-limiting
    if rate_limit_seconds > 0:
        state_data = await state.get_data()
        last_check_key = f'{rate_limit_prefix}_last_check_{order_id}'
        last_check = state_data.get(last_check_key, 0)
        now = time.time()
        elapsed = now - last_check
        if last_check and elapsed < rate_limit_seconds:
            wait = int(rate_limit_seconds - elapsed)
            await render_page(
                callback or message,
                'payment_check_wait',
                context={'payment_wait_seconds': wait},
                force_new=callback is None,
            )
            if callback:
                await safe_answer_callback(callback)
            return
        await state.update_data({last_check_key: now})

    # 6. Notification of inspection
    if callback:
        await safe_answer_callback(callback)

    # 7. Call the verification API
    try:
        check_arg = order_id if check_arg_is_order_id else payment_id
        check_kwargs = {}
        try:
            import inspect
            signature = inspect.signature(check_func)
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_kwargs or 'order_id' in signature.parameters:
                check_kwargs['order_id'] = order_id
        except (TypeError, ValueError):
            pass
        status = await check_func(check_arg, **check_kwargs)
    except Exception as e:
        logger.error(f'Ошибка проверки статуса {payment_type} {order_id}: {e}')
        await render_page(callback or message, 'payment_failed', force_new=True)
        return

    # 8. Processing the result
    if status == 'succeeded':
        update_payment_type(order_id, payment_type)
        update_payment_auto_check(
            order_id,
            state='provider_succeeded',
            next_delay_seconds=0,
        )

        # Referral reward
        if referral_override_func:
            referral_amount = await referral_override_func(order, state)
        else:
            if order.get('final_amount_cents') is not None:
                referral_amount = int(order.get('final_amount_cents') or 0)
            else:
                from database.requests import get_tariff_by_id
                _tariff = get_tariff_by_id(order.get('tariff_id'))
                referral_amount = int((_tariff.get('price_rub', 0) or 0) * 100) if _tariff else 0

        logger.info(f"{payment_type} referral: order={order_id}, referral_amount={referral_amount}")

        # Removing QR photos
        try:
            await message.delete()
        except Exception:
            pass

        await complete_payment_flow(
            order_id=order_id,
            message=message,
            state=state,
            telegram_id=telegram_id,
            payment_type=payment_type,
            referral_amount=referral_amount
        )
        completed_order = find_order_by_order_id(order_id)
        if completed_order and completed_order.get('status') == 'paid':
            update_payment_auto_check(order_id, state='completed')
    elif status == 'canceled':
        cancel_pending_order(order_id)
        update_payment_auto_check(order_id, state='canceled')
        await render_page(callback or message, 'payment_canceled', force_new=True)
    else:
        await render_page(callback or message, 'payment_pending', force_new=True)
