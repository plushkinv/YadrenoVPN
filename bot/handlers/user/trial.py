"""User flow for primary and additional persisted trial offers."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.utils.action_dispatcher import (
    CoreActionRequest,
    dispatch_core_action,
    register_core_action_executor,
)

logger = logging.getLogger(__name__)

router = Router()

_USED_REASONS = frozenset({
    'legacy_trial_used',
    'trial_used',
    'group_trial_used',
})


def _positive_id_from_callback(callback_data: str, prefix: str) -> int | None:
    raw_value = str(callback_data or '')[len(prefix):]
    if not raw_value.isdecimal() or int(raw_value) <= 0:
        return None
    return int(raw_value)


async def _render_offer_state(
    callback: CallbackQuery,
    offer_id: int | None,
) -> None:
    """Validates one offer and renders its shared confirmation page."""
    from bot.utils.page_renderer import render_page
    from bot.utils.trial_offers import build_trial_offer_page_context
    from database.requests import (
        get_primary_trial_eligibility,
        get_trial_offer_eligibility,
    )

    eligibility = (
        get_trial_offer_eligibility(callback.from_user.id, offer_id)
        if offer_id is not None
        else get_primary_trial_eligibility(callback.from_user.id)
    )
    if not eligibility.get('eligible'):
        page_key = (
            'trial_already_used'
            if eligibility.get('reason') in _USED_REASONS
            else 'action_unavailable'
        )
        await render_page(callback, page_key=page_key)
        await callback.answer()
        return

    offer = eligibility.get('offer')
    if not isinstance(offer, dict):
        await render_page(callback, page_key='action_unavailable')
        await callback.answer()
        return

    await render_page(
        callback,
        page_key='trial',
        context=build_trial_offer_page_context(
            offer,
            telegram_id=callback.from_user.id,
        ),
    )
    await callback.answer()


@router.callback_query(F.data == 'trial_subscription')
async def show_trial_subscription(callback: CallbackQuery):
    """Shows the protected primary trial offer."""
    await _render_offer_state(callback, None)


@router.callback_query(F.data.startswith('trial_offer:'))
async def show_additional_trial_offer(callback: CallbackQuery):
    """Shows one validated offer referenced by a custom page button."""
    offer_id = _positive_id_from_callback(callback.data, 'trial_offer:')
    if offer_id is None:
        await _render_trial_page(callback, 'action_unavailable')
        return
    await _render_offer_state(callback, offer_id)


@router.callback_query(F.data == 'trial_activate')
async def activate_primary_trial_legacy(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Keeps the released primary activation callback compatible."""
    await dispatch_core_action(
        callback,
        'trial.activate',
        source='callback',
        state=state,
    )


@router.callback_query(F.data.startswith('trial_activate:'))
async def activate_selected_trial(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Activates the offer confirmed on the shared trial page."""
    offer_id = _positive_id_from_callback(callback.data, 'trial_activate:')
    if offer_id is None:
        await _render_trial_page(callback, 'action_unavailable')
        return
    await dispatch_core_action(
        callback,
        'trial.activate',
        {'offer_id': offer_id},
        source='callback',
        state=state,
    )


async def _execute_trial_activate(request: CoreActionRequest) -> None:
    """Creates one claimed trial draft through the shared key setup flow."""
    from bot.handlers.user.payments.keys_config import start_new_key_config
    from bot.services.trials import activate_trial_offer
    from database.requests import (
        find_order_by_order_id,
        get_or_create_user,
        get_primary_trial_offer,
    )

    target = request.target
    state = request.state
    if state is None:
        await _render_trial_page(target, 'action_unavailable')
        return

    offer_id = request.params.get('offer_id')
    if offer_id is None:
        primary = get_primary_trial_offer()
        offer_id = int(primary['offer_id']) if primary else None
    if offer_id is None:
        await _render_trial_page(target, 'action_unavailable')
        return

    user, _ = get_or_create_user(
        request.telegram_id,
        target.from_user.username,
        first_name=getattr(target.from_user, 'first_name', None),
        last_name=getattr(target.from_user, 'last_name', None),
    )
    result = await activate_trial_offer(int(user['id']), int(offer_id))
    if not result.get('ok'):
        page_key = (
            'trial_already_used'
            if result.get('reason') in _USED_REASONS
            else 'action_unavailable'
        )
        await _render_trial_page(target, page_key)
        return

    offer = result['offer']
    key_id = int(result['key_id'])
    order_id = str(result['order_id'])
    traffic_limit_bytes = max(0, int(offer.get('traffic_limit_gb') or 0)) * 1024 ** 3
    duration_days = max(0, int(offer.get('duration_days') or 0))

    logger.info(
        "User %s activated trial offer %s (tariff=%s, group=%s)",
        request.telegram_id,
        offer_id,
        offer['tariff_id'],
        offer['group_id'],
    )

    try:
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

        await emit_key_lifecycle_event_safe(
            'key_created',
            {
                'key_id': key_id,
                'user_id': int(user['id']),
                'tariff_id': int(offer['tariff_id']),
                'days': duration_days,
                'traffic_limit': traffic_limit_bytes,
                'order_id': order_id,
                'payment_type': 'trial',
                'source': 'trial',
                'trial_offer_id': int(offer_id),
            },
        )
    except Exception as hook_err:
        logger.warning(
            "Failed to emit lifecycle hooks for trial key %s: %s",
            key_id,
            hook_err,
        )

    try:
        from bot.services.notifications import notify_admins_payment

        trial_order = find_order_by_order_id(order_id)
        if trial_order:
            await notify_admins_payment(target.bot, trial_order)
    except Exception as notify_err:
        logger.warning("Failed to notify administrators about trial: %s", notify_err)

    await state.update_data(
        new_key_order_id=order_id,
        new_key_id=key_id,
        new_key_owner_telegram_id=request.telegram_id,
        new_key_owner_username=target.from_user.username,
    )
    target_message = target.message if isinstance(target, CallbackQuery) else target
    if isinstance(target, CallbackQuery):
        await target.answer()
        try:
            await target.message.delete()
        except Exception:
            pass
    await start_new_key_config(
        target_message,
        state,
        order_id,
        key_id,
        owner_telegram_id=request.telegram_id,
        owner_username=target.from_user.username,
    )


async def _render_trial_page(
    target: CallbackQuery | Message,
    page_key: str,
) -> None:
    """Renders one database-backed failure state for the trial flow."""
    from bot.utils.page_renderer import render_page

    await render_page(
        target,
        page_key=page_key,
        force_new=not isinstance(target, CallbackQuery),
    )
    if isinstance(target, CallbackQuery):
        await target.answer()


register_core_action_executor('trial.activate', _execute_trial_activate, replace=True)
