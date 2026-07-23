"""Hidden balance top-up entry action and amount FSM."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.services.payment_intents import (
    PURPOSE_BALANCE_TOPUP,
    create_payment_intent,
)
from bot.services.money import parse_major_to_minor
from bot.states.user_states import PaymentTopup
from bot.utils.action_dispatcher import (
    CoreActionRequest,
    dispatch_core_action,
    register_core_action_executor,
)
from bot.utils.page_renderer import render_page
from bot.utils.text import get_message_text_for_storage
from database.requests import (
    get_referral_reward_type,
    get_base_currency,
    get_or_create_user,
    get_user_internal_id,
    is_cardlink_configured,
    is_cards_configured,
    is_crypto_configured,
    is_platega_configured,
    is_referral_enabled,
    is_stars_enabled,
    is_wata_configured,
    is_yookassa_qr_configured,
)

router = Router()
_PROMPT_MESSAGES: dict[int, Message] = {}


@router.callback_query(F.data == 'balance_topup')
async def balance_topup_entry(callback: CallbackQuery, state: FSMContext):
    """Dispatches the hidden stock action used by a custom page button."""
    await dispatch_core_action(
        callback,
        'balance.topup.start',
        source='callback',
        state=state,
    )


async def _execute_balance_topup_start(request: CoreActionRequest) -> None:
    """Opens amount input only when credited funds can actually be spent."""
    if not _balance_spending_enabled():
        await _show_blocked(request.target)
        return
    if not _topup_provider_configured(request.telegram_id):
        await _show_blocked(request.target)
        return
    state = request.state
    if state is None:
        await render_page(request.target, page_key='action_unavailable')
        return

    await state.set_state(PaymentTopup.waiting_for_amount)
    rendered = await render_page(
        request.target,
        page_key='balance_topup_amount',
        context={'payment_base_currency': get_base_currency()},
        force_new=isinstance(request.target, Message),
    )
    if rendered:
        _PROMPT_MESSAGES[request.telegram_id] = rendered
        await state.update_data(
            topup_prompt_message_id=rendered.message_id,
            topup_prompt_chat_id=rendered.chat.id,
        )
    if isinstance(request.target, CallbackQuery):
        await request.target.answer()


@router.message(PaymentTopup.waiting_for_amount, F.text, ~F.text.startswith('/'))
async def balance_topup_amount_input(message: Message, state: FSMContext):
    """Validates a Decimal base-currency amount and edits the original screen."""
    raw = get_message_text_for_storage(message, 'plain').strip().replace(',', '.')
    base_currency = get_base_currency()
    amount_minor = _parse_base_minor(raw, base_currency)
    prompt = _PROMPT_MESSAGES.get(message.from_user.id)
    try:
        await message.delete()
    except Exception:
        pass

    if amount_minor is None:
        await _render_amount_error(prompt or message)
        return

    user, _ = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        first_name=getattr(message.from_user, 'first_name', None),
        last_name=getattr(message.from_user, 'last_name', None),
    )

    intent = create_payment_intent(
        user_id=user['id'],
        purpose=PURPOSE_BALANCE_TOPUP,
        nominal_amount_minor=amount_minor,
    )
    await state.clear()
    _PROMPT_MESSAGES.pop(message.from_user.id, None)

    from bot.handlers.user.payments.intent import start_payment_intent_method_selection

    await start_payment_intent_method_selection(
        prompt or message,
        state,
        intent,
        telegram_id=message.from_user.id,
    )


async def _render_amount_error(target: Message) -> None:
    await render_page(
        target,
        page_key='balance_topup_amount_invalid',
    )


async def _show_blocked(target) -> None:
    await render_page(target, page_key='payment_unavailable')
    if isinstance(target, CallbackQuery):
        await target.answer()


def _parse_base_minor(value: str, currency: str | None = None) -> int | None:
    try:
        parsed = parse_major_to_minor(value, currency or get_base_currency())
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed > 9_000_000_000_000_000:
        return None
    return parsed


def _parse_rub_cents(value: str) -> int | None:
    """Deprecated test/extension alias for explicit RUB input."""
    return _parse_base_minor(value, 'RUB')


def _balance_spending_enabled() -> bool:
    return is_referral_enabled() and get_referral_reward_type() == 'balance'


def _topup_provider_configured(telegram_id: int) -> bool:
    if any((
        is_crypto_configured(),
        is_stars_enabled(),
        is_cards_configured(),
        is_yookassa_qr_configured(),
        is_wata_configured(),
        is_platega_configured(),
        is_cardlink_configured(),
    )):
        return True
    try:
        from bot.utils.payment_provider_registry import (
            is_payment_provider_enabled,
            list_payment_providers,
        )

        user_id = get_user_internal_id(telegram_id)
        return any(
            PURPOSE_BALANCE_TOPUP in provider.supported_purposes
            and is_payment_provider_enabled(
                provider.provider_id,
                {
                    'user_id': user_id,
                    'telegram_id': telegram_id,
                    'purpose': PURPOSE_BALANCE_TOPUP,
                },
            )
            for provider in list_payment_providers()
        )
    except Exception:
        return False


register_core_action_executor(
    'balance.topup.start',
    _execute_balance_topup_start,
    replace=True,
)

__all__ = ['router']
