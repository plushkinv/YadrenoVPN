"""Safe page-display context for persisted trial offers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bot.utils.placeholders import TRIAL_OFFER_FIELDS_CONTEXT_KEY
from bot.utils.user_ui_texts import render_duration_days, render_ui_text
from database.db_trial import (
    TRIAL_SCOPE_ONCE_PER_GROUP,
    get_trial_usage_scope,
)


def build_trial_offer_page_context(
    offer: Mapping[str, Any],
    *,
    telegram_id: int | None = None,
) -> dict[str, Any]:
    """Builds the allowlisted display values for the shared trial page."""
    traffic_gb = max(0, int(offer.get('traffic_limit_gb') or 0))
    traffic = (
        render_ui_text('key.traffic.unlimited')
        if traffic_gb == 0
        else render_ui_text('format.traffic_gb', gb=traffic_gb)
    )
    fields = {
        'tariff': str(offer.get('tariff_name') or '—'),
        'group': str(offer.get('group_name') or '—'),
        'duration': render_duration_days(offer.get('duration_days')),
        'traffic': traffic,
        'device_limit': str(max(1, int(offer.get('max_ips') or 1))),
    }
    scope = get_trial_usage_scope()
    eligibility_key = (
        'trial.eligibility.once_per_group'
        if scope == TRIAL_SCOPE_ONCE_PER_GROUP
        else 'trial.eligibility.once_per_user'
    )
    context: dict[str, Any] = {
        'trial_offer_id': int(offer['offer_id']),
        'trial_offer_html': render_ui_text('trial.offer.summary', **fields),
        TRIAL_OFFER_FIELDS_CONTEXT_KEY: fields,
        'trial_eligibility_html': render_ui_text(eligibility_key),
    }
    if telegram_id is not None:
        context['telegram_id'] = int(telegram_id)
    return context


__all__ = ['build_trial_offer_page_context']
