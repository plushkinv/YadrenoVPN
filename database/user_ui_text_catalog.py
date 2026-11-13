"""Declarative catalog of core user UI fragments stored outside editable pages."""
from __future__ import annotations

from localization import legacy_text as _legacy

from dataclasses import dataclass, replace
import json
from localization import CATALOG_ROOT, seed_text


@dataclass(frozen=True)
class UserUITextDefinition:
    """Developer-owned metadata and Russian default for one runtime fragment."""

    text_key: str
    text_default: str
    text_format: str
    description: str
    placeholders: frozenset[str] = frozenset()
    optional_placeholders: frozenset[str] = frozenset()

    @property
    def render_placeholders(self) -> frozenset[str]:
        """Returns every value accepted by the fragment renderer."""
        return self.placeholders | self.optional_placeholders


USER_UI_TEXT_DEFINITIONS: tuple[UserUITextDefinition, ...] = (
    UserUITextDefinition('account.merge.balance_reason', _legacy('user_ui_text_catalog.191'),
                         'plain', 'Balance history description of an account transfer.'),
    UserUITextDefinition(
        'account.link.merge_prompt',
        _legacy('user_ui_text_catalog.192'),
        'html', 'Confirm transfer into the existing Telegram owner.',
        frozenset({'target_account_id', 'phone'})),
    UserUITextDefinition(
        'account.link.prompt',
        _legacy('user_ui_text_catalog.193'),
        'html', 'Explicit Telegram confirmation for an existing site account.',
        frozenset({'account_id'}),
    ),
    UserUITextDefinition('account.link.button.confirm', _legacy('user_ui_text_catalog.194'), 'button',
                         'Confirm the Telegram side of an account link.'),
    UserUITextDefinition('account.link.button.cancel', _legacy('user_ui_text_catalog.195'), 'button',
                         'Cancel this account link request.'),
    UserUITextDefinition(
        'account.link.confirmed',
        _legacy('user_ui_text_catalog.196'),
        'html', 'Telegram confirmation succeeded; the originating web session must finish.'),
    UserUITextDefinition('account.link.cancelled', _legacy('user_ui_text_catalog.197'),
                         'html', 'The unused account link was cancelled.'),
    UserUITextDefinition(
        'account.link.invalid',
        _legacy('user_ui_text_catalog.198'),
        'html', 'An expired, consumed or invalid account link.'),
    UserUITextDefinition(
        'account.link.conflict',
        _legacy('user_ui_text_catalog.199'),
        'html', 'The link no longer matches the confirmed accounts.'),
    UserUITextDefinition(
        'account.link.unavailable',
        _legacy('user_ui_text_catalog.200'),
        'html', 'The account link service is temporarily unavailable.'),
    UserUITextDefinition(
        "payment.invoice.purchase_description",
        _legacy('user_ui_text_catalog.201'),
        "plain",
        "Telegram invoice description for a new key purchase.",
        frozenset({"tariff_name", "days"}),
    ),
    UserUITextDefinition(
        "payment.invoice.renewal_description",
        _legacy('user_ui_text_catalog.202'),
        "plain",
        "Telegram invoice description for an existing key renewal.",
        frozenset({"key_name", "tariff_name"}),
    ),
    UserUITextDefinition(
        "payment.invoice.topup_description",
        _legacy('user_ui_text_catalog.203'),
        "plain",
        "Telegram invoice description for a balance top-up.",
        frozenset({"amount", "currency"}),
    ),
    UserUITextDefinition(
        "payment.invoice.pay_button",
        _legacy('user_ui_text_catalog.204'),
        "button",
        "Native Telegram invoice pay button label.",
        frozenset({"amount"}),
    ),
    UserUITextDefinition(
        "payment.invoice.change_method_button",
        _legacy('user_ui_text_catalog.205'),
        "button",
        "Button label for returning from an invoice to payment methods.",
    ),
    UserUITextDefinition(
        "payment.invoice.stale_error",
        _legacy('user_ui_text_catalog.206'),
        "plain",
        "Pre-checkout error shown for an obsolete or mismatched invoice.",
    ),
    UserUITextDefinition(
        "format.days_short",
        _legacy('user_ui_text_catalog.207'),
        "plain",
        "Compact day-count format shared by user-facing runtime data.",
        frozenset({"days"}),
    ),
    UserUITextDefinition(
        "format.time_left",
        _legacy('user_ui_text_catalog.208'),
        "plain",
        "Remaining key duration in complete days, hours and minutes.",
        frozenset({"days", "hours", "minutes"}),
    ),
    UserUITextDefinition(
        "format.duration_unlimited",
        _legacy('user_ui_text_catalog.209'),
        "plain",
        "Display value shared by user-facing screens for an unlimited duration.",
    ),
    UserUITextDefinition(
        "format.traffic_gb",
        _legacy('user_ui_text_catalog.210'),
        "plain",
        "Compact gigabyte limit shared by user-facing runtime data.",
        frozenset({"gb"}),
    ),
    UserUITextDefinition(
        "tariff.price_unset",
        _legacy('user_ui_text_catalog.211'),
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
        _legacy('user_ui_text_catalog.212'),
        "plain",
        "Traffic display before a VPN key is configured on a server.",
    ),
    UserUITextDefinition(
        "key.traffic.unlimited",
        _legacy('user_ui_text_catalog.213'),
        "plain",
        "Traffic limit display for an unlimited VPN key.",
    ),
    UserUITextDefinition(
        "key.traffic.used_unlimited",
        _legacy('user_ui_text_catalog.214'),
        "plain",
        "Used traffic display for an unlimited VPN key.",
        frozenset({"used"}),
    ),
    UserUITextDefinition(
        "key.traffic.limited",
        _legacy('user_ui_text_catalog.215'),
        "plain",
        "Used traffic display for a limited VPN key.",
        frozenset({"used", "limit", "percent"}),
    ),
    UserUITextDefinition(
        "key.tariff.custom",
        _legacy('user_ui_text_catalog.216'),
        "plain",
        "User-facing tariff name for a key issued through the protected admin plan.",
    ),
    UserUITextDefinition(
        "key.devices.item",
        (
            _legacy('user_ui_text_catalog.217')
        ),
        "html",
        "One registered device row on the key devices page.",
        frozenset({
            "index", "model", "os", "user_agent", "first_seen", "last_seen",
        }),
    ),
    UserUITextDefinition(
        "key.devices.empty",
        _legacy('user_ui_text_catalog.218'),
        "html",
        "Empty state on the key devices page.",
    ),
    UserUITextDefinition(
        "key.devices.load_error",
        _legacy('user_ui_text_catalog.219'),
        "html",
        "Panel read error shown on the key devices page.",
    ),
    UserUITextDefinition(
        "key.devices.unknown",
        _legacy('user_ui_text_catalog.220'),
        "plain",
        "Fallback for absent device metadata.",
    ),
    UserUITextDefinition(
        "key.devices.deleted",
        _legacy('user_ui_text_catalog.221'),
        "plain",
        "Callback notice after deleting one registered device.",
    ),
    UserUITextDefinition(
        "key.devices.delete_error",
        _legacy('user_ui_text_catalog.222'),
        "plain",
        "Callback error after a failed device deletion.",
    ),
    UserUITextDefinition(
        "trial.offer.summary",
        (
            _legacy('user_ui_text_catalog.223')
        ),
        "html",
        "User-facing summary of the selected trial offer.",
        frozenset({"tariff", "group", "duration", "traffic", "device_limit"}),
    ),
    UserUITextDefinition(
        "trial.eligibility.once_per_user",
        _legacy('user_ui_text_catalog.224'),
        "html",
        "Eligibility note when only one trial is allowed per user.",
    ),
    UserUITextDefinition(
        "trial.eligibility.once_per_group",
        _legacy('user_ui_text_catalog.225'),
        "html",
        "Eligibility note when one trial is allowed in every tariff group.",
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
        "key.deleted_list.item",
        "• <b>%name%</b>",
        "html",
        "One key-name row in the grouped expired-key deletion notification.",
        frozenset({"name"}),
    ),
    UserUITextDefinition(
        "key.deleted_list.more",
        _legacy('user_ui_text_catalog.226'),
        "html",
        "Overflow row in the grouped expired-key deletion notification.",
        frozenset({"count"}),
    ),
    UserUITextDefinition(
        "referral.no_levels",
        _legacy('user_ui_text_catalog.227'),
        "html",
        "Referral page fragment shown when no reward levels are configured.",
    ),
    UserUITextDefinition(
        "referral.level_row",
        _legacy('user_ui_text_catalog.228'),
        "html",
        "One repeated reward-level row on the referral page.",
        frozenset({"level", "percent", "referrals_count", "earned"}),
    ),
    UserUITextDefinition(
        "referral.balance_line",
        _legacy('user_ui_text_catalog.229'),
        "html",
        "Optional current-balance line on the referral page.",
        frozenset({"balance"}),
    ),
    UserUITextDefinition(
        "payment.quote.promo_line",
        _legacy('user_ui_text_catalog.230'),
        "html",
        "Promo discount line shared by payment quote screens.",
        frozenset({"promo_code", "discount"}),
    ),
    UserUITextDefinition(
        "payment.quote.price_line",
        _legacy('user_ui_text_catalog.231'),
        "html",
        "Original and discounted price line shared by payment quote screens.",
        frozenset({"old_price", "new_price"}),
    ),
    UserUITextDefinition(
        "promo.auto_coupon",
        _legacy('user_ui_text_catalog.232'),
        "html",
        "Coupon fragment generated automatically after an eligible payment.",
        frozenset({"promo_code"}),
        frozenset({"discount_percent"}),
    ),
)

HISTORICAL_USER_UI_TEXT_DEFINITIONS = USER_UI_TEXT_DEFINITIONS
LOCALIZED_USER_UI_TEXT_DEFINITIONS = tuple(
    UserUITextDefinition(item['key'], seed_text('ui.' + item['key'], 'ru'),
                         item['format'], item['description'], frozenset(item['placeholders']))
    for item in json.loads((CATALOG_ROOT / 'user-fragments.json').read_text(encoding='utf-8'))
)
USER_UI_TEXT_DEFINITIONS = tuple(
    replace(item, text_default=seed_text('ui.' + item.text_key, 'ru'))
    for item in HISTORICAL_USER_UI_TEXT_DEFINITIONS
) + LOCALIZED_USER_UI_TEXT_DEFINITIONS

USER_UI_TEXT_CATALOG = {
    definition.text_key: definition for definition in USER_UI_TEXT_DEFINITIONS
}

if len(USER_UI_TEXT_CATALOG) != len(USER_UI_TEXT_DEFINITIONS):
    raise RuntimeError("Duplicate text_key in USER_UI_TEXT_DEFINITIONS")

if len(HISTORICAL_USER_UI_TEXT_DEFINITIONS) != 50:
    raise RuntimeError("The historical v132 UI catalog must contain exactly 50 entries")


__all__ = [
    "USER_UI_TEXT_CATALOG",
    "USER_UI_TEXT_DEFINITIONS",
    "UserUITextDefinition",
]
