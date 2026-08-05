import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from bot.utils.page_renderer import render_page

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
            from bot.services.payment_completion import complete_confirmed_payment
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
                await complete_confirmed_payment(
                    order_id,
                    bot=message.bot,
                    target=message,
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

        from bot.handlers.user.payments.legacy import run_legacy_provider_check

        await run_legacy_provider_check(
            provider,
            message,
            state,
            order_id=order_id,
            telegram_id=telegram_id,
            callback=None,
        )
        return True

    # Compatible with old Cardlink links from store settings.
    if start_param.startswith('cl_'):
        from database.requests import find_latest_pending_cardlink_order_for_user
        from bot.handlers.user.payments.legacy import run_legacy_provider_check

        order = find_latest_pending_cardlink_order_for_user(user_internal_id)
        if not order:
            await _show_deeplink_status('payment_order_unavailable')
            return True

        await run_legacy_provider_check(
            'cardlink',
            message,
            state,
            order_id=order['order_id'],
            telegram_id=telegram_id,
            callback=None,
        )
        return True

    return False


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
    
    Delegates general post-payment logic to complete_confirmed_payment().
    """
    from bot.services.payment_completion import complete_confirmed_payment
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
    
    await complete_confirmed_payment(
        order_id,
        bot=message.bot,
        target=message,
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


def _message_photo_file_id(message) -> str | None:
    photos = getattr(message, 'photo', None) or []
    if not photos:
        return None
    return getattr(photos[-1], 'file_id', None)


async def rerender_qr_payment_page_context(page_context, viewer_id: int) -> bool:
    """Redraws the saved QR payment screen after changing via /yaa."""
    context = dict(page_context.base_context or page_context.context or {})
    if not context:
        return False

    photo_file_id = _message_photo_file_id(page_context.message)

    await render_page(
        page_context.message,
        page_key=QR_PAYMENT_PAGE_KEY,
        route_key=getattr(page_context, 'route_key', None),
        visibility=getattr(page_context, 'base_visibility', None),
        context=context,
        text_replacements=getattr(
            page_context,
            'base_text_replacements',
            None,
        ),
        prepend_buttons=getattr(page_context, 'base_prepend_buttons', None),
        append_buttons=getattr(page_context, 'base_append_buttons', None),
        media_policy='runtime',
        runtime_media=photo_file_id,
        runtime_media_type='photo',
    )
    return True
