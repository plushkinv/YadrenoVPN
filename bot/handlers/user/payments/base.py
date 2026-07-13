import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.page_renderer import render_page
from bot.utils.text import escape_html, safe_edit_or_send

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

    async def _show_deeplink_status(title_html: str, body_text: str, provider_title: str = '') -> None:
        from bot.handlers.user.payments.status_page import show_payment_status_message
        from bot.keyboards.admin import home_only_kb

        await show_payment_status_message(
            message,
            title_html=title_html,
            body_text=body_text,
            payment_provider_title=provider_title,
            reply_markup=home_only_kb(),
            force_new=True,
        )

    if start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        parsed = parse_payment_deeplink(start_param)
        if not parsed:
            await _show_deeplink_status(
                '⚠️ <b>Платёжная ссылка устарела или повреждена</b>',
                'Откройте оплату заново из бота и попробуйте ещё раз.',
                'Платёжная ссылка',
            )
            return True

        provider = parsed['provider']
        order_id = parsed['order_id']

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
            await _show_deeplink_status(
                '⚠️ <b>Активная оплата Cardlink не найдена</b>',
                (
                    'Возможно, платёж уже обработан или ещё не создан.\n'
                    'Откройте «Купить ключ» и попробуйте снова.'
                ),
                'Cardlink',
            )
            return True

        await _run_cardlink_check(
            message, state, order_id=order['order_id'],
            telegram_id=telegram_id, callback=None
        )
        return True

    return False


def _format_price_compact(cents: int) -> str:
    """Formatting prices in a compact form."""
    if cents >= 10000:
        return f'{cents // 100} ₽'
    else:
        return f'{cents / 100:.2f} ₽'.replace('.', ',')

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
    """Pre-checkout confirmation for Telegram Stars."""
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
    
    if payload.startswith('renew:'):
        order_id = payload.split(':')[1]
    elif payload.startswith('vpn_key:'):
        order_id = payload.split(':')[1]
    else:
        order_id = payload
    
    await complete_payment_flow(
        order_id=order_id,
        message=message,
        state=state,
        telegram_id=message.from_user.id,
        payment_type=payment_type,
        referral_amount=payment.total_amount
    )

async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Completes the UI after successful payment.
    Shows a message and either transfers to the settings (draft) or to the main one.
    """
    from bot.keyboards.admin import home_only_kb
    from database.requests import get_key_details_for_user, get_user_by_id
    import logging
    logger = logging.getLogger(__name__)
    from bot.handlers.user.payments.keys_config import start_new_key_config
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
        from bot.handlers.user.payments.status_page import show_payment_status_message

        title_html, body_html = _parse_success_payment_status_text(text)
        await show_payment_status_message(
            message,
            title_html=title_html,
            body_html=body_html,
            payment_provider_title='Оплата',
            force_new=True,
        )
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
        from bot.handlers.user.keys import show_key_details
        await show_key_details(telegram_id=user_id, key_id=key_id, message=message, is_callback=False, prepend_text=text)


def _parse_success_payment_status_text(text: str) -> tuple[str, str]:
    """Converts legacy payment success text to title/body for payment_status."""
    body_html = str(text or '').strip()
    title_html = '✅ <b>Оплата принята</b>'

    success_prefix = '✅ Оплата прошла успешно!'
    accepted_prefix = '✅ Оплата принята'
    duplicate_prefix = '✅ Этот платёж уже был обработан ранее.'

    if body_html.startswith(success_prefix):
        title_html = '✅ <b>Оплата прошла успешно</b>'
        body_html = body_html[len(success_prefix):].lstrip()
    elif body_html.startswith(duplicate_prefix):
        title_html = '✅ <b>Платёж уже обработан</b>'
        body_html = ''
    elif body_html.startswith(accepted_prefix):
        title_html = '✅ <b>Оплата принята</b>'
        body_html = body_html[len(accepted_prefix):].lstrip(' .\n')

    return title_html, body_html


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
        await callback.answer('❌ Не удалось создать счёт', show_alert=True)
        return False

    try:
        await message.answer_invoice(**invoice_kwargs)
        return True
    except Exception as e:
        from bot.handlers.user.payments.status_page import show_payment_status_message
        from bot.keyboards.admin import home_only_kb

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
            body_html = (
                'Сумма тарифа меньше допустимого лимита платёжной системы.\n'
                'Выберите другой тариф или способ оплаты.'
            )
        else:
            logger.exception("Не удалось создать Telegram invoice (%s).", log_context)
            body_html = (
                'Платёжная система не приняла запрос на создание счёта.\n'
                'Попробуйте другой способ оплаты или обратитесь в поддержку.'
            )

        await show_payment_status_message(
            message,
            title_html='❌ <b>Не удалось создать счёт</b>',
            body_html=body_html,
            payment_provider_title=provider_title,
            reply_markup=home_only_kb(),
        )
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
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return

    await show_renew_payment_page(callback, key, key_id, force_new=True)
    await callback.answer()


# ============================================================================
# COMMON FUNCTIONS FOR QR PAYMENT PROVIDERS (wata, platega, cardlink, yookassa)
# ============================================================================


def default_qr_payment_page_text() -> str:
    """Default text of the QR payment technical page."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "💳 <b>Тариф:</b> %платеж_тариф%\n"
        "💰 <b>Сумма:</b> %платеж_сумма%\n"
        "⏳ <b>%платеж_срок_тип%:</b> %платеж_срок%\n"
        "%платеж_скидка_строка%"
        "\n%платеж_инструкция%\n\n"
        "<i>%платеж_подсказка%</i>"
    )


def _format_payment_link(qr_url: str) -> str:
    return f'<a href="{escape_html(str(qr_url))}">ссылке на оплату</a>'


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
    payment_link = _format_payment_link(qr_url)
    if instruction_text is None:
        instruction_html = f"Отсканируйте QR код для перехода по {payment_link}."
    else:
        instruction_html = instruction_text.format(payment_link=payment_link)

    if hint_text is None:
        hint_text = 'После оплаты нажмите «✅ Я оплатил».'

    context = {
        'payment_provider_title_html': title,
        'payment_key_line_html': f"🔑 <b>Ключ:</b> {key_name}\n" if key_name else '',
        'payment_tariff_html': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_label': 'Продление' if key_name else 'Срок',
        'payment_term_text': f'+{days} дней' if key_name else f'{days} дней',
        'payment_url': str(qr_url),
        'payment_link_html': payment_link,
        'payment_instruction_html': instruction_html,
        'payment_hint_text': hint_text,
        'payment_discount_line_html': _format_payment_discount_line(promo_lines),
    }
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
        fallback_text=default_qr_payment_page_text(),
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
    loading_text: str = '⏳ Создаём ссылку на оплату...',
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
    from database.requests import get_user_internal_id, create_pending_order
    from bot.keyboards.user import qr_payment_kb
    from bot.keyboards.admin import home_only_kb
    from bot.services.promotions import describe_quote_lines, format_amount, prepare_order_pricing
    from bot.handlers.user.payments.status_page import (
        show_payment_status_message,
        show_payment_unavailable_status,
    )

    # User Validation
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return

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
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return

    price_rub = quote['final_amount'] / 100

    await show_payment_status_message(
        callback.message,
        title_html=escape_html(loading_text),
        body_html='',
        payment_provider_title=error_name,
    )

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username

        # Description for the provider
        if key:
            description = (
                f"Продление Ключа «{key['display_name']}»: "
                f"«{tariff['name']}» ({tariff['duration_days']} дн.)"
            )
        else:
            description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"

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
        save_func(order_id, result[result_key])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')

        if not qr_image_data or not qr_url:
            await show_payment_status_message(
                callback.message,
                title_html=f'❌ <b>{escape_html(error_name)} не вернул данные для оплаты</b>',
                body_text='Попробуйте позже.',
                payment_provider_title=error_name,
                reply_markup=home_only_kb()
            )
            return

        # Formation of text
        promo_lines = describe_quote_lines(quote)
        payment_context = build_qr_payment_page_context(
            title=title,
            tariff_name=escape_html(tariff['name']),
            price_str=format_amount(quote['final_amount'], payment_type),
            days=tariff['duration_days'],
            qr_url=qr_url,
            key_name=escape_html(key['display_name']) if key else None,
            hint_text=hint_text,
            instruction_text=instruction_text,
            promo_lines=promo_lines,
        )
        payment_context.setdefault('bot_username', bot_name)
        payment_context = build_page_flow_context(callback, **payment_context)

        # Sending a QR photo
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename=qr_filename)
        runtime_markup = qr_payment_kb(order_id, check_prefix, back_callback, qr_url)
        runtime_rows = getattr(runtime_markup, 'inline_keyboard', None)
        await render_page(
            callback,
            page_key=QR_PAYMENT_PAGE_KEY,
            context=payment_context,
            append_buttons=runtime_rows,
            force_new=True,
            fallback_text=default_qr_payment_page_text(),
            media_policy='runtime',
            runtime_media=photo,
            runtime_media_type='photo',
        )
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания {error_name}-счёта: {e}')
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Ошибка создания платежа</b>',
            body_html=(
                f'<i>{escape_html(str(e))}</i>\n\n'
                'Попробуйте другой способ оплаты.'
            ),
            payment_provider_title=error_name,
            reply_markup=home_only_kb()
        )

    await callback.answer()


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
        find_order_by_order_id, get_user_internal_id,
        is_order_already_paid, update_payment_type
    )
    from bot.handlers.user.payments.status_page import show_payment_status_message
    from bot.services.billing import complete_payment_flow
    from bot.keyboards.admin import home_only_kb

    async def _show_order_not_found() -> None:
        if callback:
            await callback.answer('❌ Ордер не найден', show_alert=True)
        else:
            await show_payment_status_message(
                message,
                title_html='❌ <b>Ордер не найден</b>',
                body_text='Откройте оплату заново из бота и попробуйте ещё раз.',
                reply_markup=home_only_kb(),
                send_func=safe_edit_or_send,
            )

    # 1. Order search
    order = find_order_by_order_id(order_id)
    if not order:
        await _show_order_not_found()
        return

    # 2. Verification of the order owner
    owner_user_id = get_user_internal_id(telegram_id)
    if not owner_user_id or int(order.get('user_id') or 0) != int(owner_user_id):
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
            '✅ Оплата уже была обработана ранее.',
            order, user_id=telegram_id
        )
        if callback:
            await callback.answer()
        return

    # 4. Payment_id validation
    payment_id = order.get(payment_id_field)
    if not payment_id:
        if callback:
            await callback.answer('⚠️ Нет данных о платеже. Попробуйте чуть позже.', show_alert=True)
        else:
            await show_payment_status_message(
                message,
                title_html='⚠️ <b>Нет данных о платеже</b>',
                body_text='Попробуйте чуть позже или откройте оплату заново.',
                reply_markup=home_only_kb(),
                send_func=safe_edit_or_send,
            )
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
            if callback:
                await callback.answer(
                    f'⏳ Подождите {wait} сек. перед повторной проверкой.',
                    show_alert=True
                )
            return
        await state.update_data({last_check_key: now})

    # 6. Notification of inspection
    if callback:
        await callback.answer('🔍 Проверяем платёж...')

    # 7. Call the verification API
    try:
        check_arg = order_id if check_arg_is_order_id else payment_id
        status = await check_func(check_arg)
    except Exception as e:
        logger.error(f'Ошибка проверки статуса {payment_type} {order_id}: {e}')
        await show_payment_status_message(
            message,
            title_html='❌ <b>Не удалось проверить статус платежа</b>',
            body_text='Попробуйте позже.',
            reply_markup=home_only_kb(),
            force_new=True,
            send_func=safe_edit_or_send,
        )
        return

    # 8. Processing the result
    if status == 'succeeded':
        update_payment_type(order_id, payment_type)

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
    elif status == 'canceled':
        await show_payment_status_message(
            message,
            title_html='❌ <b>Платёж отменён</b>',
            body_text='Похоже, платёж был отменён.\nПопробуйте снова выбрать тариф.',
            reply_markup=home_only_kb(),
            force_new=True,
            send_func=safe_edit_or_send,
        )
    else:
        pending_body = 'Оплатите по ссылке и нажмите «✅ Я оплатил» снова.'
        if pending_hint:
            pending_body += f'\n\n<i>{escape_html(pending_hint)}</i>'
        await show_payment_status_message(
            message,
            title_html='⏳ <b>Платёж ещё не поступил</b>',
            body_html=pending_body,
            force_new=True,
            send_func=safe_edit_or_send,
        )
