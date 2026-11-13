"""Shared admin-facing contract for broadcast audience filters."""

from __future__ import annotations

from localization import text as _tr

from typing import Any

from database.db_stats import BROADCAST_FILTER_KEYS, normalize_broadcast_filters


BROADCAST_FILTER_LABELS = {
    'active': _tr('admin_user_filters.module.1'),
    'inactive': _tr('broadcast_audience.module.1'),
    'never_paid': _tr('admin_user_filters.module.3'),
    'expired': _tr('admin_user_filters.module.4'),
    'used_trial': _tr('broadcast_audience.module.2'),
}


def broadcast_filter_labels(filters: object) -> list[str]:
    """Return selected filter labels in canonical order."""
    return [
        BROADCAST_FILTER_LABELS[key]
        for key in normalize_broadcast_filters(filters)
    ]


def broadcast_filter_summary(filters: object) -> str:
    """Render the selection for an administrator-facing summary."""
    labels = broadcast_filter_labels(filters)
    if not labels:
        return _tr('broadcast_audience.broadcast_filter_summary.1')
    return _tr('admin_user_filters.user_filter_status.2').join(labels)


def broadcast_filter_status(filters: object) -> str:
    """Render the complete administrator-facing filter status line."""
    labels = broadcast_filter_labels(filters)
    if not labels:
        return _tr('admin_user_filters.user_filter_status.3')
    return _tr('admin_user_filters.user_filter_status.1') + _tr('admin_user_filters.user_filter_status.2').join(labels)


def broadcast_audience_state(
    filters: object,
    recipient_count: int,
) -> dict[str, Any]:
    """Build the structured audience state exposed to the contextual editor."""
    selected = normalize_broadcast_filters(filters)
    selected_set = set(selected)
    count = max(0, int(recipient_count))
    return {
        'contract_version': 2,
        'selected_filters': list(selected),
        'available_filters': [
            {
                'key': key,
                'label': BROADCAST_FILTER_LABELS[key],
                'selected': key in selected_set,
            }
            for key in BROADCAST_FILTER_KEYS
        ],
        'logic': 'and',
        'empty_means_all': True,
        'recipient_count': count,
        'launch_blocker': (
            _tr('broadcast_audience.broadcast_audience_state.1')
            if count == 0
            else ''
        ),
    }


__all__ = [
    'BROADCAST_FILTER_LABELS',
    'broadcast_audience_state',
    'broadcast_filter_labels',
    'broadcast_filter_status',
    'broadcast_filter_summary',
]
