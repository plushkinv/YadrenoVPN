
from localization import text as _tr
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.admin_misc import (
    back_button,
    home_button,
    state_pair_buttons,
)


def promocodes_list_kb(promocodes: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_promotions.promocodes_list_kb.1'), callback_data="admin_promocode_add"))
    for promo in promocodes[:20]:
        status = "🟢" if promo.get("is_active") else "⚪"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {promo['code']}",
                callback_data=f"admin_promocode_view:{promo['id']}",
            )
        )
    builder.row(back_button("admin_marketing"), home_button())
    return builder.as_markup()


def promocode_detail_kb(promo: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    status_text = _tr('admin_promotions.promocode_detail_kb.1') if promo.get("is_active") else _tr('admin_promotions.promocode_detail_kb.2')
    builder.row(InlineKeyboardButton(text=status_text, callback_data=f"admin_promocode_toggle:{promo['id']}"))
    builder.row(InlineKeyboardButton(text=_tr('admin_promotions.promocode_detail_kb.3'), callback_data=f"admin_promocode_edit_discount:{promo['id']}"))
    builder.row(InlineKeyboardButton(text=_tr('admin_promotions.promocode_detail_kb.4'), callback_data=f"admin_promocode_edit_expires:{promo['id']}"))
    builder.row(InlineKeyboardButton(text=_tr('admin_promotions.promocode_detail_kb.5'), callback_data=f"admin_promocode_edit_limit:{promo['id']}"))
    builder.row(back_button("admin_promocodes"), home_button())
    return builder.as_markup()


def coupons_menu_kb(
    purchase_enabled: bool,
    lapsed_enabled: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    purchase_status = "🟢" if purchase_enabled else "⚪"
    lapsed_status = "🟢" if lapsed_enabled else "⚪"
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupons_menu_kb.1', p0=f'{purchase_status}'),
            callback_data="admin_coupons_purchase",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupons_menu_kb.2', p0=f'{lapsed_status}'),
            callback_data="admin_coupons_lapsed",
        )
    )
    builder.row(InlineKeyboardButton(text=_tr('admin_promotions.coupons_menu_kb.3'), callback_data="admin_coupons_generate"))
    builder.row(back_button("admin_marketing"), home_button())
    return builder.as_markup()


def coupon_purchase_settings_kb(
    enabled: bool,
    discount_percent: int,
    lifetime_days: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_payments.wata_management_kb.1'),
        "admin_coupons_purchase_set:1",
        _tr('admin_payments.wata_management_kb.2'),
        "admin_coupons_purchase_set:0",
    ))
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupon_purchase_settings_kb.1', p0=f'{discount_percent}'),
            callback_data="admin_coupons_setting:purchase:discount",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupon_purchase_settings_kb.2', p0=f'{lifetime_days}'),
            callback_data="admin_coupons_setting:purchase:lifetime",
        )
    )
    builder.row(
        back_button("admin_coupons"),
        home_button(),
    )
    return builder.as_markup()


def coupon_lapsed_settings_kb(
    enabled: bool,
    discount_percent: int,
    lifetime_days: int,
    delay_days: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_payments.wata_management_kb.1'),
        "admin_coupons_lapsed_set:1",
        _tr('admin_payments.wata_management_kb.2'),
        "admin_coupons_lapsed_set:0",
    ))
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupon_purchase_settings_kb.1', p0=f'{discount_percent}'),
            callback_data="admin_coupons_setting:lapsed:discount",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupon_purchase_settings_kb.2', p0=f'{lifetime_days}'),
            callback_data="admin_coupons_setting:lapsed:lifetime",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_promotions.coupon_lapsed_settings_kb.1', p0=f'{delay_days}'),
            callback_data="admin_coupons_setting:lapsed:delay",
        )
    )
    builder.row(
        back_button("admin_coupons"),
        home_button(),
    )
    return builder.as_markup()


def promotion_cancel_kb(back_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data=back_callback))
    return builder.as_markup()
