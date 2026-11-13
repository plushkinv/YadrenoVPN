"""Administrator wizard for generating batches of one-time coupons."""

from __future__ import annotations

from localization import text as _tr

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import promotion_cancel_kb
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import (
    render_admin_dialog,
    render_admin_dialog_from_input,
)
from bot.utils.text import get_message_text_for_storage
from database.requests import create_coupon_batch

router = Router()


def _coupon_generator_prompt(
    title: str,
    prompt: str,
    *,
    error: str | None = None,
) -> str:
    parts = [title]
    if error:
        parts.append(f"❌ {error}")
    parts.append(prompt)
    return "\n\n".join(parts)


@router.callback_query(F.data == "admin_coupons_generate")
async def admin_coupons_generate(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await state.set_state(AdminStates.coupon_generate_discount)
    await render_admin_dialog(
        callback.message,
        state,
        _coupon_generator_prompt(
            _tr('coupon_generator.admin_coupons_generate.1'),
            _tr('coupon_generator.admin_coupons_generate.2'),
        ),
        reply_markup=promotion_cancel_kb("admin_coupons"),
    )
    await callback.answer()


@router.message(
    AdminStates.coupon_generate_discount,
    F.text,
    ~F.text.startswith("/"),
)
async def admin_coupons_generate_discount(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        return
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 100:
        await render_admin_dialog_from_input(
            message,
            state,
            _coupon_generator_prompt(
                _tr('coupon_generator.admin_coupons_generate.1'),
                _tr('coupon_generator.admin_coupons_generate.2'),
                error=_tr('coupon_generator.admin_coupons_generate_discount.1'),
            ),
            reply_markup=promotion_cancel_kb("admin_coupons"),
        )
        return
    await state.update_data(coupon_generate_discount=int(raw))
    await state.set_state(AdminStates.coupon_generate_lifetime)
    await render_admin_dialog_from_input(
        message,
        state,
        _coupon_generator_prompt(
            _tr('coupon_generator.admin_coupons_generate_discount.2'),
            _tr('coupon_generator.admin_coupons_generate_discount.3'),
        ),
        reply_markup=promotion_cancel_kb("admin_coupons"),
    )


@router.message(
    AdminStates.coupon_generate_lifetime,
    F.text,
    ~F.text.startswith("/"),
)
async def admin_coupons_generate_lifetime(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        return
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await render_admin_dialog_from_input(
            message,
            state,
            _coupon_generator_prompt(
                _tr('coupon_generator.admin_coupons_generate_discount.2'),
                _tr('coupon_generator.admin_coupons_generate_discount.3'),
                error=_tr('coupon_generator.admin_coupons_generate_lifetime.1'),
            ),
            reply_markup=promotion_cancel_kb("admin_coupons"),
        )
        return
    await state.update_data(coupon_generate_lifetime=int(raw))
    await state.set_state(AdminStates.coupon_generate_count)
    await render_admin_dialog_from_input(
        message,
        state,
        _coupon_generator_prompt(
            _tr('coupon_generator.admin_coupons_generate_lifetime.2'),
            _tr('coupon_generator.admin_coupons_generate_lifetime.3'),
        ),
        reply_markup=promotion_cancel_kb("admin_coupons"),
    )


@router.message(
    AdminStates.coupon_generate_count,
    F.text,
    ~F.text.startswith("/"),
)
async def admin_coupons_generate_count(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        return
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 500:
        await render_admin_dialog_from_input(
            message,
            state,
            _coupon_generator_prompt(
                _tr('coupon_generator.admin_coupons_generate_lifetime.2'),
                _tr('coupon_generator.admin_coupons_generate_lifetime.3'),
                error=_tr('coupon_generator.admin_coupons_generate_count.1'),
            ),
            reply_markup=promotion_cancel_kb("admin_coupons"),
        )
        return
    data = await state.get_data()
    target = await render_admin_dialog_from_input(
        message,
        state,
        _tr('coupon_generator.admin_coupons_generate_count.2'),
    )
    coupons = create_coupon_batch(
        discount_percent=data["coupon_generate_discount"],
        lifetime_days=data["coupon_generate_lifetime"],
        count=int(raw),
        source="admin_generated",
        created_by_admin_id=message.from_user.id,
    )
    codes = "\n".join(coupon["code"] for coupon in coupons)
    text = (
        _tr('coupon_generator.admin_coupons_generate_count.3', p0=f"{data['coupon_generate_discount']}", p1=f"{data['coupon_generate_lifetime']}", p2=f'{len(coupons)}', p3=f'{html.escape(codes)}')
    )
    try:
        await render_admin_dialog(
            target,
            state,
            text,
            reply_markup=promotion_cancel_kb("admin_coupons"),
        )
    finally:
        await state.clear()
