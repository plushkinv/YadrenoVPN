"""Administrator screens for coupon generation and automatic issuance."""

from __future__ import annotations

from localization import text as _tr

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    coupon_lapsed_settings_kb,
    coupon_purchase_settings_kb,
    coupons_menu_kb,
    promotion_cancel_kb,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
from database.requests import (
    get_coupon_auto_discount_percent,
    get_coupon_auto_enabled,
    get_coupon_auto_lifetime_days,
    get_lapsed_coupon_delay_days,
    get_lapsed_coupon_discount_percent,
    get_lapsed_coupon_enabled,
    get_lapsed_coupon_lifetime_days,
    get_lapsed_coupon_statistics,
    get_purchase_auto_coupon_statistics,
    set_coupon_auto_discount_percent,
    set_coupon_auto_enabled,
    set_coupon_auto_lifetime_days,
    set_lapsed_coupon_delay_days,
    set_lapsed_coupon_discount_percent,
    set_lapsed_coupon_enabled,
    set_lapsed_coupon_lifetime_days,
)

router = Router()


async def _delete_input(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


def _format_conversion(value: Any) -> str:
    try:
        rendered = f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0%"
    return f"{rendered.rstrip('0').rstrip('.')}%"


def _statistics_text(
    stats: dict[str, Any],
    *,
    include_waiting: bool = False,
) -> str:
    lines = [
        _tr('coupons.statistics_text.1'),
        _tr('coupons.statistics_text.2', p0=f"{int(stats.get('issued') or 0)}"),
        _tr('coupons.statistics_text.3', p0=f"{int(stats.get('used') or 0)}"),
        _tr('coupons.statistics_text.4', p0=f"{int(stats.get('active') or 0)}"),
        _tr('coupons.statistics_text.5', p0=f"{int(stats.get('expired') or 0)}"),
        _tr('coupons.statistics_text.6', p0=f"{_format_conversion(stats.get('conversion_percent'))}"),
    ]
    if include_waiting:
        lines.append(
            _tr('coupons.statistics_text.7', p0=f"{int(stats.get('waiting') or 0)}")
        )
    return "\n".join(lines)


async def _render_coupons_root(
    message: Message,
    state: FSMContext,
    *,
    force_new: bool = False,
) -> Message:
    await state.set_state(AdminStates.admin_menu)
    text = (
        _tr('coupons.render_coupons_root.1')
    )
    return await safe_edit_or_send(
        message,
        text,
        reply_markup=coupons_menu_kb(
            get_coupon_auto_enabled(),
            get_lapsed_coupon_enabled(),
        ),
        force_new=force_new,
    )


async def _render_purchase_settings(
    message: Message,
    state: FSMContext,
    *,
    force_new: bool = False,
) -> Message:
    await state.set_state(AdminStates.admin_menu)
    enabled = get_coupon_auto_enabled()
    discount = get_coupon_auto_discount_percent()
    lifetime = get_coupon_auto_lifetime_days()
    text = (
        _tr('coupons.render_purchase_settings.3', p0=f'{_statistics_text(get_purchase_auto_coupon_statistics())}', p1=f"{(_tr('coupons.render_purchase_settings.1') if enabled else _tr('coupons.render_purchase_settings.2'))}", p2=f'{discount}', p3=f'{lifetime}')
    )
    return await safe_edit_or_send(
        message,
        text,
        reply_markup=coupon_purchase_settings_kb(
            enabled,
            discount,
            lifetime,
        ),
        force_new=force_new,
    )


async def _render_lapsed_settings(
    message: Message,
    state: FSMContext,
    *,
    force_new: bool = False,
) -> Message:
    await state.set_state(AdminStates.admin_menu)
    enabled = get_lapsed_coupon_enabled()
    discount = get_lapsed_coupon_discount_percent()
    lifetime = get_lapsed_coupon_lifetime_days()
    delay = get_lapsed_coupon_delay_days()
    text = (
        _tr('coupons.render_lapsed_settings.1', p0=f'{_statistics_text(get_lapsed_coupon_statistics(), include_waiting=True)}', p1=f"{(_tr('coupons.render_purchase_settings.1') if enabled else _tr('coupons.render_purchase_settings.2'))}", p2=f'{discount}', p3=f'{lifetime}', p4=f'{delay}')
    )
    return await safe_edit_or_send(
        message,
        text,
        reply_markup=coupon_lapsed_settings_kb(
            enabled,
            discount,
            lifetime,
            delay,
        ),
        force_new=force_new,
    )


@router.callback_query(F.data == "admin_coupons")
async def admin_coupons(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await _render_coupons_root(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "admin_coupons_purchase")
async def admin_coupons_purchase(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await _render_purchase_settings(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "admin_coupons_lapsed")
async def admin_coupons_lapsed(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await _render_lapsed_settings(callback.message, state)
    await callback.answer()


async def _set_coupon_scenario_enabled(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    scenario: str,
    target_enabled: bool | None,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    if scenario == "purchase":
        current = get_coupon_auto_enabled()
        setter = set_coupon_auto_enabled
        renderer = _render_purchase_settings
        label = _tr('coupons.set_coupon_scenario_enabled.1')
    else:
        current = get_lapsed_coupon_enabled()
        setter = set_lapsed_coupon_enabled
        renderer = _render_lapsed_settings
        label = _tr('coupons.set_coupon_scenario_enabled.2')

    desired = not current if target_enabled is None else target_enabled
    if desired == current:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if desired else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(f"{label} {status}")
        return

    setter(desired)
    await renderer(callback.message, state)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_coupons_purchase_set:"))
async def admin_coupons_purchase_set(
    callback: CallbackQuery,
    state: FSMContext,
):
    target_enabled = str(callback.data).rsplit(":", 1)[1] == "1"
    await _set_coupon_scenario_enabled(
        callback,
        state,
        scenario="purchase",
        target_enabled=target_enabled,
    )


@router.callback_query(F.data.startswith("admin_coupons_lapsed_set:"))
async def admin_coupons_lapsed_set(
    callback: CallbackQuery,
    state: FSMContext,
):
    target_enabled = str(callback.data).rsplit(":", 1)[1] == "1"
    await _set_coupon_scenario_enabled(
        callback,
        state,
        scenario="lapsed",
        target_enabled=target_enabled,
    )


@router.callback_query(F.data == "admin_coupons_purchase_toggle")
async def admin_coupons_purchase_toggle(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Keep old administrator messages compatible after the UI update."""
    await _set_coupon_scenario_enabled(
        callback,
        state,
        scenario="purchase",
        target_enabled=None,
    )


@router.callback_query(F.data == "admin_coupons_lapsed_toggle")
async def admin_coupons_lapsed_toggle(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Keep old administrator messages compatible after the UI update."""
    await _set_coupon_scenario_enabled(
        callback,
        state,
        scenario="lapsed",
        target_enabled=None,
    )


@router.callback_query(F.data.startswith("admin_coupons_setting:"))
async def admin_coupon_setting_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    parts = str(callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer(_tr('coupons.admin_coupon_setting_start.1'), show_alert=True)
        return
    _prefix, kind, field = parts
    allowed = {
        ("purchase", "discount"),
        ("purchase", "lifetime"),
        ("lapsed", "discount"),
        ("lapsed", "lifetime"),
        ("lapsed", "delay"),
    }
    if (kind, field) not in allowed:
        await callback.answer(_tr('coupons.admin_coupon_setting_start.1'), show_alert=True)
        return

    prompts = {
        "discount": _tr('coupons.admin_coupon_setting_start.2'),
        "lifetime": _tr('coupons.admin_coupon_setting_start.3'),
        "delay": _tr('coupons.admin_coupon_setting_start.4'),
    }
    back_callback = (
        "admin_coupons_purchase"
        if kind == "purchase"
        else "admin_coupons_lapsed"
    )
    await state.update_data(
        coupon_setting_kind=kind,
        coupon_setting_field=field,
        coupon_setting_message_id=callback.message.message_id,
    )
    await state.set_state(AdminStates.coupon_setting_value)
    await safe_edit_or_send(
        callback.message,
        _tr('coupons.admin_coupon_setting_start.5', p0=f'{prompts[field]}'),
        reply_markup=promotion_cancel_kb(back_callback),
    )
    await callback.answer()


def _save_coupon_setting(kind: str, field: str, value: int) -> None:
    if kind == "purchase" and field == "discount":
        set_coupon_auto_discount_percent(value)
    elif kind == "purchase" and field == "lifetime":
        set_coupon_auto_lifetime_days(value)
    elif kind == "lapsed" and field == "discount":
        set_lapsed_coupon_discount_percent(value)
    elif kind == "lapsed" and field == "lifetime":
        set_lapsed_coupon_lifetime_days(value)
    elif kind == "lapsed" and field == "delay":
        set_lapsed_coupon_delay_days(value)
    else:
        raise ValueError("Unknown coupon setting")


@router.message(
    AdminStates.coupon_setting_value,
    F.text,
    ~F.text.startswith("/"),
)
async def admin_coupon_setting_save(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    raw = get_message_text_for_storage(message, "plain").strip()
    await _delete_input(message)

    kind = str(data.get("coupon_setting_kind") or "")
    field = str(data.get("coupon_setting_field") or "")
    original_message_id = data.get("coupon_setting_message_id")
    try:
        target = message.model_copy(
            update={"message_id": int(original_message_id)}
        )
    except (TypeError, ValueError):
        target = message
    back_callback = (
        "admin_coupons_purchase"
        if kind == "purchase"
        else "admin_coupons_lapsed"
    )

    valid = raw.isdigit()
    value = int(raw) if valid else -1
    if field == "discount":
        valid = valid and 0 <= value <= 100
    elif field == "lifetime":
        valid = valid and value > 0
    elif field == "delay":
        valid = valid and 1 <= value <= 30
    else:
        valid = False

    if not valid:
        await safe_edit_or_send(
            target,
            _tr('coupons.admin_coupon_setting_save.1'),
            reply_markup=promotion_cancel_kb(back_callback),
            force_new=target is message,
        )
        return

    try:
        _save_coupon_setting(kind, field, value)
    except ValueError:
        await safe_edit_or_send(
            target,
            _tr('coupons.admin_coupon_setting_save.1'),
            reply_markup=promotion_cancel_kb(back_callback),
            force_new=target is message,
        )
        return

    await state.clear()
    if kind == "purchase":
        await _render_purchase_settings(
            target,
            state,
            force_new=target is message,
        )
    else:
        await _render_lapsed_settings(
            target,
            state,
            force_new=target is message,
        )
