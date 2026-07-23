"""Declarative catalog of core user UI fragments stored outside editable pages."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserUITextDefinition:
    """Developer-owned metadata and Russian default for one runtime fragment."""

    text_key: str
    text_default: str
    text_format: str
    description: str
    placeholders: frozenset[str] = frozenset()


USER_UI_TEXT_DEFINITIONS: tuple[UserUITextDefinition, ...] = (
    UserUITextDefinition(
        "payment.invoice.purchase_description",
        "Оплата тарифа «%tariff_name%» (%days%).",
        "plain",
        "Telegram invoice description for a new key purchase.",
        frozenset({"tariff_name", "days"}),
    ),
    UserUITextDefinition(
        "payment.invoice.renewal_description",
        "Продление ключа «%key_name%»: «%tariff_name%».",
        "plain",
        "Telegram invoice description for an existing key renewal.",
        frozenset({"key_name", "tariff_name"}),
    ),
    UserUITextDefinition(
        "payment.invoice.topup_description",
        "Пополнение баланса на %amount% %currency%.",
        "plain",
        "Telegram invoice description for a balance top-up.",
        frozenset({"amount", "currency"}),
    ),
    UserUITextDefinition(
        "payment.invoice.pay_button",
        "💳 Оплатить %amount%",
        "button",
        "Native Telegram invoice pay button label.",
        frozenset({"amount"}),
    ),
    UserUITextDefinition(
        "payment.invoice.change_method_button",
        "🔄 Сменить способ",
        "button",
        "Button label for returning from an invoice to payment methods.",
    ),
    UserUITextDefinition(
        "payment.invoice.stale_error",
        "Счёт устарел или не соответствует выбранной оплате.",
        "plain",
        "Pre-checkout error shown for an obsolete or mismatched invoice.",
    ),
    UserUITextDefinition(
        "format.days_short",
        "%days% дн.",
        "plain",
        "Compact day-count format shared by user-facing runtime data.",
        frozenset({"days"}),
    ),
    UserUITextDefinition(
        "tariff.price_unset",
        "Цена не установлена",
        "plain",
        "Fallback display value when an administrator did not configure a tariff price.",
    ),
    UserUITextDefinition(
        "key.status.active",
        "🟢",
        "plain",
        "Display status for an active VPN key.",
    ),
    UserUITextDefinition(
        "key.status.expired",
        "🔴",
        "plain",
        "Display status for an expired VPN key.",
    ),
    UserUITextDefinition(
        "key.status.traffic_exhausted",
        "🔴",
        "plain",
        "Display status for a VPN key with exhausted traffic.",
    ),
    UserUITextDefinition(
        "key.traffic.needs_setup",
        "⚠️ Требует настройки",
        "plain",
        "Traffic display before a VPN key is configured on a server.",
    ),
    UserUITextDefinition(
        "key.traffic.unlimited",
        "Безлимит",
        "plain",
        "Traffic limit display for an unlimited VPN key.",
    ),
    UserUITextDefinition(
        "key.traffic.used_unlimited",
        "%used% (безлимит)",
        "plain",
        "Used traffic display for an unlimited VPN key.",
        frozenset({"used"}),
    ),
    UserUITextDefinition(
        "key.traffic.limited",
        "%used% из %limit% (%percent%%)",
        "plain",
        "Used traffic display for a limited VPN key.",
        frozenset({"used", "limit", "percent"}),
    ),
    UserUITextDefinition(
        "key.inbound.all_protocols",
        "Все протоколы",
        "plain",
        "Inbound display for a subscription containing every available protocol.",
    ),
    UserUITextDefinition(
        "key.history.operation_with_days",
        "   • %date%: %operation% (%days%)",
        "html",
        "Key operation history row that includes a day count.",
        frozenset({"date", "operation", "days"}),
    ),
    UserUITextDefinition(
        "key.history.operation",
        "   • %date%: %operation%",
        "html",
        "Key operation history row without a day count.",
        frozenset({"date", "operation"}),
    ),
    UserUITextDefinition(
        "key.history.payment",
        "   • %date%: %payment_type% (%amount%)",
        "html",
        "Payment row in key operation history.",
        frozenset({"date", "payment_type", "amount"}),
    ),
    UserUITextDefinition(
        "key.history.promo_suffix",
        ", 🎟 %promo_code%",
        "plain",
        "Optional promo-code suffix in key operation history.",
        frozenset({"promo_code"}),
    ),
    UserUITextDefinition(
        "referral.no_levels",
        "Пока нет активных уровней реферальной программы.",
        "html",
        "Referral page fragment shown when no reward levels are configured.",
    ),
    UserUITextDefinition(
        "referral.level_row",
        "✅ Уровень %level% (%percent%%): %referrals_count% чел. — %earned%",
        "html",
        "One repeated reward-level row on the referral page.",
        frozenset({"level", "percent", "referrals_count", "earned"}),
    ),
    UserUITextDefinition(
        "referral.balance_line",
        "💰 <b>Ваш баланс:</b> %balance%",
        "html",
        "Optional current-balance line on the referral page.",
        frozenset({"balance"}),
    ),
    UserUITextDefinition(
        "payment.quote.promo_line",
        "🎟 Промокод: <b>%promo_code%</b> (-%discount%%)",
        "html",
        "Promo discount line shared by payment quote screens.",
        frozenset({"promo_code", "discount"}),
    ),
    UserUITextDefinition(
        "payment.quote.price_line",
        "💵 Цена: <s>%old_price%</s> → <b>%new_price%</b>",
        "html",
        "Original and discounted price line shared by payment quote screens.",
        frozenset({"old_price", "new_price"}),
    ),
    UserUITextDefinition(
        "promo.auto_coupon",
        "🎫 <b>Купон на следующую покупку</b>\n\n<pre>%promo_code%</pre>",
        "html",
        "Coupon fragment generated automatically after an eligible payment.",
        frozenset({"promo_code"}),
    ),
)

USER_UI_TEXT_CATALOG = {
    definition.text_key: definition for definition in USER_UI_TEXT_DEFINITIONS
}

if len(USER_UI_TEXT_CATALOG) != len(USER_UI_TEXT_DEFINITIONS):
    raise RuntimeError("Duplicate text_key in USER_UI_TEXT_DEFINITIONS")

if len(USER_UI_TEXT_CATALOG) != 26:
    raise RuntimeError("The initial core user UI text catalog must contain exactly 26 entries")


__all__ = [
    "USER_UI_TEXT_CATALOG",
    "USER_UI_TEXT_DEFINITIONS",
    "UserUITextDefinition",
]
