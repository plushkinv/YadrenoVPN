"""User promo-code flow rendered entirely from database-backed pages."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.states.user_states import PromoInput
from bot.utils.page_renderer import render_page
from bot.utils.text import get_message_text_for_storage
from database.requests import get_or_create_user, has_available_promo_codes, is_base62_code

logger = logging.getLogger(__name__)

router = Router()

PROMO_ENTER_PAGE_KEY = "promo_enter"
PROMO_FAILURE_PAGES = {
    "not_found": "promo_not_found",
    "inactive": "promo_inactive",
    "expired": "promo_expired",
    "exhausted": "promo_exhausted",
}


def promo_return_callback(
    key_id: int | None = None,
    order_id: str | None = None,
) -> str:
    """Return the stable callback for the payment flow that opened promo input."""
    if order_id:
        return f"payment_intent_methods:{order_id}"
    if key_id:
        return f"key_renew:{key_id}"
    return "buy_key"


async def render_promo_result_page(
    target: Message | CallbackQuery,
    page_key: str,
    *,
    key_id: int | None = None,
    order_id: str | None = None,
    promo: dict | None = None,
    force_new: bool = False,
) -> None:
    """Render one concrete promo result page with data-only context."""
    promo = promo or {}
    await render_page(
        target,
        page_key=page_key,
        context={
            "promo_return_callback": promo_return_callback(key_id, order_id),
            "promo_code": promo.get("code") or "",
            "promo_discount": int(promo.get("discount_percent") or 0),
        },
        force_new=force_new,
    )


@router.callback_query(F.data == "promo_enter")
@router.callback_query(F.data.startswith("promo_enter:"))
@router.callback_query(F.data.startswith("promo_enter_order:"))
async def promo_enter_handler(callback: CallbackQuery, state: FSMContext):
    """Request a promotional code or coupon from the user."""
    key_id = None
    order_id = None
    if callback.data.startswith("promo_enter_order:"):
        order_id = callback.data.split(":", 1)[1] or None
    elif ":" in callback.data:
        try:
            key_id = int(callback.data.split(":", 1)[1])
        except (TypeError, ValueError):
            key_id = None

    if not has_available_promo_codes():
        await render_promo_result_page(
            callback,
            "promo_unavailable",
            key_id=key_id,
            order_id=order_id,
        )
        await callback.answer()
        return

    await state.update_data(
        promo_key_id=key_id,
        promo_order_id=order_id,
        promo_message_id=callback.message.message_id,
        promo_chat_id=callback.message.chat.id,
    )
    await state.set_state(PromoInput.waiting_for_code)

    await render_page(
        callback,
        page_key=PROMO_ENTER_PAGE_KEY,
        context={
            "promo_key_id": key_id,
            "promo_return_callback": promo_return_callback(key_id, order_id),
        },
    )
    await callback.answer()


@router.message(PromoInput.waiting_for_code, F.text, ~F.text.startswith("/"))
async def promo_code_input_handler(message: Message, state: FSMContext):
    """Validate and save the user's active promo code."""
    from bot.services.promotions import activate_promo_code_for_user

    data = await state.get_data()
    key_id = data.get("promo_key_id")
    order_id = data.get("promo_order_id")
    code = get_message_text_for_storage(message, "plain").strip()

    try:
        await message.delete()
    except Exception:
        pass

    if not is_base62_code(code):
        await render_promo_result_page(
            message,
            "promo_invalid",
            key_id=key_id,
            order_id=order_id,
            force_new=True,
        )
        return

    user, _ = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        first_name=getattr(message.from_user, "first_name", None),
        last_name=getattr(message.from_user, "last_name", None),
    )
    result = activate_promo_code_for_user(user["id"], code)
    if not result["ok"]:
        await render_promo_result_page(
            message,
            PROMO_FAILURE_PAGES.get(result.get("reason"), "promo_unavailable"),
            key_id=key_id,
            order_id=order_id,
            promo=result.get("promo"),
            force_new=True,
        )
        return

    await state.clear()
    await render_promo_result_page(
        message,
        "promo_applied",
        key_id=key_id,
        order_id=order_id,
        promo=result["promo"],
        force_new=True,
    )
