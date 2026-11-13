"""Administrator UI for the built-in Crypto Pay payment provider."""
from __future__ import annotations

from localization import text as _tr

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.requests import (
    get_cryptobot_token,
    is_cryptobot_enabled,
    set_setting,
)

from bot.keyboards.admin import back_and_home_kb, cryptobot_management_kb
from bot.services.cryptobot import validate_cryptobot_token
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send


logger = logging.getLogger(__name__)
router = Router()
_MENU_MESSAGES: dict[int, Message] = {}


def _masked_token(token: str) -> str:
    if not token:
        return _tr('cryptobot.masked_token.1')
    if len(token) < 12:
        return _tr('cryptobot.masked_token.2')
    return (
        _tr('cryptobot.masked_token.3', p0=f'{escape_html(token[:6])}', p1=f'{escape_html(token[-4:])}')
    )


async def _render_management(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.payments_menu)
    enabled = is_cryptobot_enabled()
    status_emoji = "🟢" if enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if enabled else _tr('coupons.render_purchase_settings.2')
    text = (
        _tr('cryptobot.render_management.1', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{_masked_token(get_cryptobot_token())}')
    )
    rendered = await safe_edit_or_send(
        message,
        text,
        reply_markup=cryptobot_management_kb(enabled),
        show_web_page_preview=False,
    )
    if rendered is not None:
        _MENU_MESSAGES[getattr(message.chat, "id", 0)] = rendered


@router.callback_query(F.data == "admin_payments_cryptobot")
async def show_cryptobot_management_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Show the independent Crypto Pay settings screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await _render_management(callback.message, state)
    await callback.answer()


async def _set_enabled(
    callback: CallbackQuery,
    state: FSMContext,
    target_enabled: bool,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    current = is_cryptobot_enabled()
    if current == target_enabled:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if target_enabled else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(f"Crypto Pay {status}")
        return

    validated_token = ""
    if target_enabled:
        validated_token = get_cryptobot_token()
        if not validated_token:
            await callback.answer(
                _tr('cryptobot.set_enabled.1'),
                show_alert=True,
            )
            return
        try:
            await validate_cryptobot_token(validated_token)
        except Exception as error:
            logger.warning(
                "Crypto Pay token validation failed while enabling: admin=%s error=%s",
                callback.from_user.id,
                error,
            )
            await callback.answer(
                _tr('cryptobot.set_enabled.2'),
                show_alert=True,
            )
            return

    from bot.services.cryptobot import cryptobot_lifecycle_lock

    async with cryptobot_lifecycle_lock():
        if target_enabled and get_cryptobot_token() != validated_token:
            await callback.answer(
                _tr('cryptobot.set_enabled.3'),
                show_alert=True,
            )
            return
        set_setting("cryptobot_enabled", "1" if target_enabled else "0")
    await callback.answer(
        _tr('cryptobot.set_enabled.4') if target_enabled else _tr('cryptobot.set_enabled.5')
    )
    await _render_management(callback.message, state)


@router.callback_query(F.data.startswith("admin_cryptobot_mgmt_set:"))
async def cryptobot_management_set(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Enable or disable creation of new Crypto Pay invoices."""
    await _set_enabled(
        callback,
        state,
        callback.data.rsplit(":", 1)[1] == "1",
    )


@router.callback_query(F.data == "admin_cryptobot_mgmt_edit_token")
async def cryptobot_edit_token(callback: CallbackQuery, state: FSMContext) -> None:
    """Request and securely process a Crypto Pay API token."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await state.set_state(AdminStates.cryptobot_setup_token)
    await state.update_data(cryptobot_menu_message_id=callback.message.message_id)
    _MENU_MESSAGES[callback.from_user.id] = callback.message
    await safe_edit_or_send(
        callback.message,
        _tr('cryptobot.cryptobot_edit_token.1'),
        reply_markup=back_and_home_kb("admin_payments_cryptobot"),
    )
    await callback.answer()


async def _render_token_error(
    message: Message,
    state: FSMContext,
    text: str,
) -> None:
    target = _MENU_MESSAGES.get(message.from_user.id)
    rendered = await safe_edit_or_send(
        target or message,
        _tr('cryptobot.render_token_error.1', p0=f'{text}'),
        reply_markup=back_and_home_kb("admin_payments_cryptobot"),
        force_new=target is None,
    )
    if rendered is not None:
        _MENU_MESSAGES[message.from_user.id] = rendered
        await state.update_data(cryptobot_menu_message_id=rendered.message_id)


@router.message(AdminStates.cryptobot_setup_token)
async def cryptobot_token_input(message: Message, state: FSMContext) -> None:
    """Validate a new token before replacing the currently usable credential."""
    token = get_message_text_for_storage(message, "plain").strip()
    try:
        await message.delete()
    except Exception:
        pass

    if not token or any(ord(character) < 32 for character in token):
        await _render_token_error(message, state, _tr('cryptobot.cryptobot_token_input.1'))
        return
    try:
        await validate_cryptobot_token(token)
    except Exception as error:
        logger.warning(
            "Crypto Pay token validation failed: admin=%s error=%s",
            message.from_user.id,
            error,
        )
        await _render_token_error(
            message,
            state,
            _tr('cryptobot.cryptobot_token_input.2'),
        )
        return

    current_token = get_cryptobot_token()
    if token != current_token:
        from bot.services.payment_provider_cancellation import (
            replace_cryptobot_token_safely,
        )

        readiness = await replace_cryptobot_token_safely(
            token,
            bot=message.bot,
        )
        if not readiness.ready:
            reason = (
                _tr('cryptobot.cryptobot_token_input.3')
                if readiness.active
                else
                _tr('cryptobot.cryptobot_token_input.4')
            )
            await _render_token_error(
                message,
                state,
                reason,
            )
            return
    else:
        # The token was already validated above and does not require replacement.
        set_setting("cryptobot_api_token", token)
    logger.info("Crypto Pay token updated: admin=%s", message.from_user.id)
    target = _MENU_MESSAGES.pop(message.from_user.id, None)
    await _render_management(target or message, state)


__all__ = ["router", "show_cryptobot_management_menu"]
