import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from bot.utils.user_pages import render_access_blocked_page
from bot.utils.action_dispatcher import (
    CoreActionRequest,
    dispatch_core_action,
    register_core_action_executor,
)
from database.requests import is_user_banned

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command('buy'))
async def cmd_buy(message: Message, state: FSMContext | None = None):
    """Command handler /buy - opens the key purchase page."""
    if is_user_banned(message.from_user.id):
        await render_access_blocked_page(message, force_new=True)
        return
    await dispatch_core_action(
        message,
        'key.purchase.start',
        source='command',
        state=state,
    )


async def _render_buy_page(target):
    """Renders the key purchase page.

    Args:
        target: Message or CallbackQuery
    """
    from database.requests import (
        is_crypto_configured, is_stars_enabled, is_cards_enabled,
        is_yookassa_qr_configured, is_wata_configured, is_platega_configured,
        is_cardlink_configured,
        is_demo_payment_enabled,
        get_all_tariffs,
        get_user_internal_id,
    )
    from bot.utils.page_renderer import render_page

    if isinstance(target, CallbackQuery):
        telegram_id = target.from_user.id
    else:
        telegram_id = target.from_user.id

    crypto_configured = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    yookassa_qr = is_yookassa_qr_configured()
    wata_enabled = is_wata_configured()
    platega_enabled = is_platega_configured()
    cardlink_enabled = is_cardlink_configured()
    demo_enabled = is_demo_payment_enabled()

    # Verification: at least one payment method is configured
    if not crypto_configured and not stars_enabled and not cards_enabled and not yookassa_qr and not wata_enabled and not platega_enabled and not cardlink_enabled and not demo_enabled:
        await render_page(
            target,
            page_key='prepayment_unavailable',
            force_new=isinstance(target, Message),
        )
        return

    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await render_page(
            target,
            page_key='prepayment_unavailable',
            force_new=isinstance(target, Message),
        )
        return
    from bot.utils.page_button_items import build_tariff_button_items

    context = {
        'telegram_id': telegram_id,
        'tariff_button_items': build_tariff_button_items(
            tariffs,
            'key_purchase',
            user_id=get_user_internal_id(telegram_id),
        ),
        'tariff_back_callback': 'start',
    }
    await render_page(
        target,
        page_key='prepayment',
        context=context,
        force_new=isinstance(target, Message),
    )


@router.callback_query(F.data == 'buy_key')
async def buy_key_handler(callback: CallbackQuery, state: FSMContext):
    """“Buy a key” page with terms and payment methods."""
    await dispatch_core_action(
        callback,
        'key.purchase.start',
        source='callback',
        state=state,
    )


async def _execute_purchase_start(request: CoreActionRequest) -> None:
    """Run the original purchase-page flow after action policy resolution."""
    if is_user_banned(request.telegram_id):
        await render_access_blocked_page(
            request.target,
            force_new=isinstance(request.target, Message),
        )
        if isinstance(request.target, CallbackQuery):
            await request.target.answer()
        return
    await _render_buy_page(request.target)
    if isinstance(request.target, CallbackQuery):
        await request.target.answer()


register_core_action_executor('key.purchase.start', _execute_purchase_start, replace=True)
