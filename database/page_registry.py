"""Canonical page identities and pure page-classification rules."""
from __future__ import annotations

import re
from dataclasses import dataclass


PAGE_KIND_CORE = 'core'
PAGE_KIND_CUSTOM = 'custom'
PAGE_KIND_LEGACY_CUSTOM = 'legacy_custom'
PAGE_KIND_UNKNOWN = 'unknown'
PAGE_KINDS = frozenset({
    PAGE_KIND_CORE,
    PAGE_KIND_CUSTOM,
    PAGE_KIND_LEGACY_CUSTOM,
    PAGE_KIND_UNKNOWN,
})

CUSTOM_PAGE_PREFIX = 'custom_'
_CUSTOM_PAGE_KEY_RE = re.compile(r'^custom_[a-z0-9_]+$')


# Pages exposed by the normal full-screen editor.
CORE_EDITOR_PAGE_KEYS = (
    'main',
    'help',
    'trial',
    'access_blocked',
    'prepayment',
    'prepayment_unavailable',
    'renew_payment',
    'my_keys',
    'my_keys_empty',
    'key_details',
    'key_devices',
    'key_status',
    'key_show_unconfigured',
    'renew_payment_unavailable',
    'key_replace_server_select',
    'key_replace_confirm',
    'key_subscription_host_select',
    'key_rename_prompt',
    'new_key_server_select',
    'new_key_no_servers',
    'referral',
    'key_delivery',
    'qr_payment',
    'demo_payment',
    'payment_tariff_select',
    'payment_method_select',
    'payment_completed',
    'payment_coupon_message',
    'balance_topup_amount',
    'balance_topup_result',
    'support_start',
    'support_status',
    'promo_enter',
    'promo_status',
    'show_id',
    'action_unavailable',
    'screen_unavailable',
    'trial_already_used',
    'balance_insufficient',
    'balance_topup_amount_invalid',
    'payment_method_select_renewal',
    'payment_method_select_topup',
    'payment_method_select_surcharge',
    'payment_link_renewal',
    'payment_link_topup',
    'payment_creating',
    'payment_pending',
    'payment_check_wait',
    'payment_canceled',
    'payment_unavailable',
    'payment_minimum_unavailable',
    'payment_order_unavailable',
    'payment_failed',
    'payment_auto_completed',
    'promo_invalid',
    'promo_not_found',
    'promo_inactive',
    'promo_expired',
    'promo_exhausted',
    'promo_unavailable',
    'promo_applied',
    'promo_link_saved',
    'support_reply_start',
    'support_format_unsupported',
    'support_thread_unavailable',
    'support_failed',
    'support_sent',
    'my_keys_key_deleted',
    'key_not_found',
    'key_progress',
    'key_operation_unavailable',
    'key_replace_server_unavailable',
    'key_operation_failed',
    'key_rename_invalid',
    'key_delivery_partial',
    'key_delivery_failed',
    'key_renewed',
    'expiry_notification_actions',
    'expired_keys_deleted',
    'lapsed_key_coupon',
)

# custom_profile is a shipped route target despite its historical name;
# support_reply is a shipped keyboard-only template.
CORE_PAGE_KEYS = frozenset((*CORE_EDITOR_PAGE_KEYS, 'custom_profile', 'support_reply'))


@dataclass(frozen=True)
class PageClassification:
    """Effective render classification of one page row."""

    page_key: str
    page_kind: str
    exists: bool
    render_allowed: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            'page_key': self.page_key,
            'page_kind': self.page_kind,
            'render_allowed': self.render_allowed,
            'classification_reason': self.reason,
        }


def is_valid_custom_page_key(page_key: object) -> bool:
    """Return whether a new custom page key follows the public namespace."""
    return isinstance(page_key, str) and bool(_CUSTOM_PAGE_KEY_RE.fullmatch(page_key))


def classify_page(
    page_key: object,
    page_kind: object,
    *,
    exists: bool = True,
) -> PageClassification:
    """Classify a stored page without reading or mutating the database."""
    normalized_key = str(page_key) if isinstance(page_key, str) else ''
    normalized_kind = str(page_kind) if isinstance(page_kind, str) else PAGE_KIND_UNKNOWN
    if not exists:
        return PageClassification(
            page_key=normalized_key,
            page_kind=PAGE_KIND_UNKNOWN,
            exists=False,
            render_allowed=False,
            reason='page_not_found',
        )
    if normalized_kind == PAGE_KIND_CORE:
        allowed = normalized_key in CORE_PAGE_KEYS
        return PageClassification(
            page_key=normalized_key,
            page_kind=normalized_kind,
            exists=True,
            render_allowed=allowed,
            reason='canonical_core' if allowed else 'core_key_not_in_registry',
        )
    if normalized_kind == PAGE_KIND_CUSTOM:
        allowed = (
            normalized_key not in CORE_PAGE_KEYS
            and is_valid_custom_page_key(normalized_key)
        )
        return PageClassification(
            page_key=normalized_key,
            page_kind=normalized_kind,
            exists=True,
            render_allowed=allowed,
            reason='valid_custom_key' if allowed else 'custom_kind_requires_custom_prefix',
        )
    if normalized_kind == PAGE_KIND_LEGACY_CUSTOM:
        return PageClassification(
            page_key=normalized_key,
            page_kind=normalized_kind,
            exists=True,
            render_allowed=True,
            reason='migration_grandfathered_legacy',
        )
    if normalized_kind == PAGE_KIND_UNKNOWN:
        return PageClassification(
            page_key=normalized_key,
            page_kind=normalized_kind,
            exists=True,
            render_allowed=False,
            reason='unclassified_page',
        )
    return PageClassification(
        page_key=normalized_key,
        page_kind=PAGE_KIND_UNKNOWN,
        exists=True,
        render_allowed=False,
        reason='invalid_page_kind',
    )


__all__ = [
    'CORE_EDITOR_PAGE_KEYS',
    'CORE_PAGE_KEYS',
    'CUSTOM_PAGE_PREFIX',
    'PAGE_KIND_CORE',
    'PAGE_KIND_CUSTOM',
    'PAGE_KIND_LEGACY_CUSTOM',
    'PAGE_KIND_UNKNOWN',
    'PAGE_KINDS',
    'PageClassification',
    'classify_page',
    'is_valid_custom_page_key',
]
