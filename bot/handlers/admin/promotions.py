
from localization import text as _tr
import datetime
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    promocode_detail_kb,
    promocodes_list_kb,
    promotion_cancel_kb,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import (
    render_admin_dialog,
    render_admin_dialog_from_input,
)
from bot.utils.telegram_links import build_telegram_link, get_telegram_link_domain
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from database.requests import (
    create_promo_code,
    get_promo_code_by_code,
    get_promo_code_by_id,
    get_promo_codes,
    is_base62_code,
    set_promo_code_active,
    update_promo_code,
)

logger = logging.getLogger(__name__)
router = Router()


def _format_date(value) -> str:
    return value or _tr('promotions.format_date.1')


def _parse_expires(value: str):
    value = value.strip()
    if value in {"0", "-", "нет", "Нет"}:
        return None
    dt = datetime.datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(hour=23, minute=59, second=59)


def _promocode_add_prompt(
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


async def _delete_input(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


def _promocode_text(promo: dict, bot_username: str | None = None) -> str:
    limit = promo.get("activation_limit")
    limit_text = str(limit) if limit else _tr('promotions.promocode_text.1')
    usage = int(promo.get("usage_count") or 0)
    status = _tr('groups.build_group_view_text.3') if promo.get("is_active") else _tr('groups.build_group_view_text.4')
    link_line = ""
    if bot_username:
        promo_link = build_telegram_link(bot_username, f"pr_{promo['code']}")
        link_line = _tr('promotions.promocode_text.2', p0=f'{escape_html(promo_link)}')
    return (
        _tr('promotions.promocode_text.3', p0=f"{escape_html(promo['code'])}", p1=f"{int(promo.get('discount_percent') or 0)}", p2=f"{escape_html(_format_date(promo.get('expires_at')))}", p3=f'{escape_html(limit_text)}', p4=f'{usage}', p5=f'{status}', p6=f'{link_line}')
    )


@router.callback_query(F.data == "admin_promocodes")
async def admin_promocodes(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await state.set_state(AdminStates.admin_menu)
    promocodes = get_promo_codes("promo")
    text = (
        _tr('promotions.admin_promocodes.1', p0=f'{escape_html(get_telegram_link_domain())}')
    )
    await safe_edit_or_send(callback.message, text, reply_markup=promocodes_list_kb(promocodes))
    await callback.answer()


@router.callback_query(F.data == "admin_promocode_add")
async def admin_promocode_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminStates.promocode_add_code)
    await render_admin_dialog(
        callback.message,
        state,
        _promocode_add_prompt(
            _tr('promotions.admin_promocode_add.1'),
            _tr('promotions.admin_promocode_add.2'),
        ),
        reply_markup=promotion_cancel_kb("admin_promocodes"),
    )
    await callback.answer()


@router.message(AdminStates.promocode_add_code, F.text, ~F.text.startswith("/"))
async def promocode_add_code(message: Message, state: FSMContext):
    code = get_message_text_for_storage(message, "plain").strip()
    if not is_base62_code(code):
        await render_admin_dialog_from_input(
            message,
            state,
            _promocode_add_prompt(
                _tr('promotions.admin_promocode_add.1'),
                _tr('promotions.admin_promocode_add.2'),
                error=(
                    _tr('promotions.promocode_add_code.1')
                ),
            ),
            reply_markup=promotion_cancel_kb("admin_promocodes"),
        )
        return
    if get_promo_code_by_code(code):
        await render_admin_dialog_from_input(
            message,
            state,
            _promocode_add_prompt(
                _tr('promotions.admin_promocode_add.1'),
                _tr('promotions.promocode_add_code.2'),
                error=_tr('promotions.promocode_add_code.3'),
            ),
            reply_markup=promotion_cancel_kb("admin_promocodes"),
        )
        return
    await state.update_data(promocode_code=code)
    await state.set_state(AdminStates.promocode_add_discount)
    await render_admin_dialog_from_input(
        message,
        state,
        _promocode_add_prompt(
            _tr('promotions.promocode_add_code.4'),
            _tr('coupon_generator.admin_coupons_generate.2'),
        ),
        reply_markup=promotion_cancel_kb("admin_promocodes"),
    )


@router.message(AdminStates.promocode_add_discount, F.text, ~F.text.startswith("/"))
async def promocode_add_discount(message: Message, state: FSMContext):
    value = get_message_text_for_storage(message, "plain").strip()
    if not value.isdigit() or not 0 <= int(value) <= 100:
        await render_admin_dialog_from_input(
            message,
            state,
            _promocode_add_prompt(
                _tr('promotions.promocode_add_code.4'),
                _tr('coupon_generator.admin_coupons_generate.2'),
                error=_tr('coupon_generator.admin_coupons_generate_discount.1'),
            ),
            reply_markup=promotion_cancel_kb("admin_promocodes"),
        )
        return
    await state.update_data(promocode_discount=int(value))
    await state.set_state(AdminStates.promocode_add_expires)
    await render_admin_dialog_from_input(
        message,
        state,
        _promocode_add_prompt(
            _tr('promotions.promocode_add_discount.1'),
            _tr('promotions.promocode_add_discount.2'),
        ),
        reply_markup=promotion_cancel_kb("admin_promocodes"),
    )


@router.message(AdminStates.promocode_add_expires, F.text, ~F.text.startswith("/"))
async def promocode_add_expires(message: Message, state: FSMContext):
    raw = get_message_text_for_storage(message, "plain").strip()
    try:
        expires_at = _parse_expires(raw)
    except ValueError:
        await render_admin_dialog_from_input(
            message,
            state,
            _promocode_add_prompt(
                _tr('promotions.promocode_add_discount.1'),
                _tr('promotions.promocode_add_discount.2'),
                error=_tr('promotions.promocode_add_expires.1'),
            ),
            reply_markup=promotion_cancel_kb("admin_promocodes"),
        )
        return
    await state.update_data(promocode_expires=expires_at)
    await state.set_state(AdminStates.promocode_add_limit)
    await render_admin_dialog_from_input(
        message,
        state,
        _tr('promotions.promocode_add_expires.2'),
        reply_markup=promotion_cancel_kb("admin_promocodes"),
    )


@router.message(AdminStates.promocode_add_limit, F.text, ~F.text.startswith("/"))
async def promocode_add_limit(message: Message, state: FSMContext):
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit():
        await render_admin_dialog_from_input(
            message,
            state,
            _promocode_add_prompt(
                _tr('promotions.promocode_add_limit.1'),
                _tr('promotions.promocode_add_limit.2'),
                error=_tr('promotions.promocode_add_limit.3'),
            ),
            reply_markup=promotion_cancel_kb("admin_promocodes"),
        )
        return
    data = await state.get_data()
    target = await render_admin_dialog_from_input(
        message,
        state,
        _tr('promotions.promocode_add_limit.4'),
    )
    promo_id = create_promo_code(
        code=data["promocode_code"],
        discount_percent=data["promocode_discount"],
        expires_at=data.get("promocode_expires"),
        activation_limit=int(raw),
        created_by_admin_id=message.from_user.id,
        source="admin",
        code_type="promo",
    )
    try:
        promo = get_promo_code_by_id(promo_id)
        bot_info = await message.bot.get_me()
        await render_admin_dialog(
            target,
            state,
            _promocode_text(promo, bot_info.username),
            reply_markup=promocode_detail_kb(promo),
        )
    finally:
        await state.clear()

@router.callback_query(F.data.startswith("admin_promocode_view:"))
async def admin_promocode_view(callback: CallbackQuery, state: FSMContext):
    promo_id = int(callback.data.split(":")[1])
    promo = get_promo_code_by_id(promo_id)
    if not promo:
        await callback.answer(_tr('promotions.admin_promocode_view.1'), show_alert=True)
        return
    bot_info = await callback.bot.get_me()
    await safe_edit_or_send(callback.message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promocode_toggle:"))
async def admin_promocode_toggle(callback: CallbackQuery):
    promo_id = int(callback.data.split(":")[1])
    promo = get_promo_code_by_id(promo_id)
    if not promo:
        await callback.answer(_tr('promotions.admin_promocode_view.1'), show_alert=True)
        return
    set_promo_code_active(promo_id, not bool(promo.get("is_active")))
    promo = get_promo_code_by_id(promo_id)
    bot_info = await callback.bot.get_me()
    await safe_edit_or_send(callback.message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promocode_edit_"))
async def admin_promocode_edit_start(callback: CallbackQuery, state: FSMContext):
    action, promo_id = callback.data.rsplit(":", 1)
    field = action.replace("admin_promocode_edit_", "")
    await state.update_data(promocode_edit_id=int(promo_id), promocode_edit_field=field)
    await state.set_state(AdminStates.promocode_edit_value)
    hints = {
        "discount": _tr('coupons.admin_coupon_setting_start.2'),
        "expires": _tr('promotions.admin_promocode_edit_start.1'),
        "limit": _tr('promotions.admin_promocode_edit_start.2'),
    }
    await safe_edit_or_send(callback.message, _tr('promotions.admin_promocode_edit_start.3', p0=f'{hints[field]}'), reply_markup=promotion_cancel_kb(f"admin_promocode_view:{promo_id}"))
    await callback.answer()


@router.message(AdminStates.promocode_edit_value, F.text, ~F.text.startswith("/"))
async def admin_promocode_edit_value(message: Message, state: FSMContext):
    await _delete_input(message)
    data = await state.get_data()
    promo_id = int(data["promocode_edit_id"])
    field = data["promocode_edit_field"]
    raw = get_message_text_for_storage(message, "plain").strip()
    try:
        if field == "discount":
            if not raw.isdigit() or not 0 <= int(raw) <= 100:
                raise ValueError()
            update_promo_code(promo_id, discount_percent=int(raw))
        elif field == "expires":
            update_promo_code(promo_id, expires_at=_parse_expires(raw))
        elif field == "limit":
            if not raw.isdigit():
                raise ValueError()
            update_promo_code(promo_id, activation_limit=int(raw))
    except ValueError:
        await safe_edit_or_send(message, _tr('promotions.admin_promocode_edit_value.1'), reply_markup=promotion_cancel_kb(f"admin_promocode_view:{promo_id}"), force_new=True)
        return
    await state.clear()
    promo = get_promo_code_by_id(promo_id)
    bot_info = await message.bot.get_me()
    await safe_edit_or_send(message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo), force_new=True)
