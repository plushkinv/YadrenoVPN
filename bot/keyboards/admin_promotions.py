from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.admin_misc import back_button, home_button


def promocodes_list_kb(promocodes: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Добавить", callback_data="admin_promocode_add"))
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
    status_text = "🟢 Включено" if promo.get("is_active") else "⚪ Выключено"
    builder.row(InlineKeyboardButton(text=status_text, callback_data=f"admin_promocode_toggle:{promo['id']}"))
    builder.row(InlineKeyboardButton(text="📊 Размер скидки", callback_data=f"admin_promocode_edit_discount:{promo['id']}"))
    builder.row(InlineKeyboardButton(text="⏳ Срок действия", callback_data=f"admin_promocode_edit_expires:{promo['id']}"))
    builder.row(InlineKeyboardButton(text="🔢 Лимит активаций", callback_data=f"admin_promocode_edit_limit:{promo['id']}"))
    builder.row(back_button("admin_promocodes"), home_button())
    return builder.as_markup()


def coupons_menu_kb(enabled: bool, discount_percent: int, lifetime_days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🟢 Авто выдача при покупке" if enabled else "⚪ Авто выдача при покупке"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="admin_coupons_toggle_auto"))
    builder.row(InlineKeyboardButton(text=f"📊 Размер скидки: {discount_percent}%", callback_data="admin_coupons_edit_discount"))
    builder.row(InlineKeyboardButton(text=f"⏳ Время жизни: {lifetime_days} дн.", callback_data="admin_coupons_edit_lifetime"))
    builder.row(InlineKeyboardButton(text="🎲 Сгенерировать", callback_data="admin_coupons_generate"))
    builder.row(back_button("admin_marketing"), home_button())
    return builder.as_markup()


def promotion_cancel_kb(back_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=back_callback))
    return builder.as_markup()
