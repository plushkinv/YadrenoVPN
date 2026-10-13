"""Connection-free filter contract for the administrator's user directory."""
from collections.abc import Iterable

from .payment_semantics import paid_key_purchase_predicate


USER_FILTER_KEYS = (
    'active', 'no_keys', 'never_paid', 'expired', 'used_trial',
    'banned', 'bot', 'site', 'bot_blocked',
)
USER_CHANNEL_FILTERS = frozenset({'bot', 'site'})


class UserFilterError(ValueError):
    """An administrator user filter is outside the supported contract."""


def normalize_user_filters(value: object = ()) -> tuple[str, ...]:
    """Accept a canonical collection or a released single-filter value."""
    if isinstance(value, str):
        items = () if value == 'all' else (value,)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        items = value
    else:
        raise UserFilterError('Invalid user filter selection')
    selected = set()
    for item in items:
        if item == 'inactive':
            item = 'no_keys'
        if not isinstance(item, str) or item not in USER_FILTER_KEYS:
            raise UserFilterError('Unknown user filter')
        selected.add(item)
    return tuple(key for key in USER_FILTER_KEYS if key in selected)


def user_filter_conditions(value: object = ()) -> list[str]:
    """Build AND conditions for the fixed users alias ``u`` without joins."""
    active = """EXISTS (
        SELECT 1 FROM vpn_keys vk WHERE vk.user_id = u.id
        AND (vk.expires_at > datetime('now') OR vk.expires_at IS NULL)
    )"""
    predicates = {
        'active': active,
        'no_keys': 'NOT EXISTS (SELECT 1 FROM vpn_keys vk WHERE vk.user_id = u.id)',
        'never_paid': f"""NOT EXISTS (
            SELECT 1 FROM payments paid_order WHERE paid_order.user_id = u.id
            AND {paid_key_purchase_predicate('paid_order')}
        )""",
        'expired': f"""EXISTS (
            SELECT 1 FROM vpn_keys vk WHERE vk.user_id = u.id
            AND vk.expires_at <= datetime('now')
        ) AND NOT {active}""",
        'used_trial': 'COALESCE(u.used_trial, 0) = 1',
        'banned': 'u.is_banned = 1',
        'bot': 'u.telegram_id IS NOT NULL',
        'site': 'EXISTS (SELECT 1 FROM account_credentials c WHERE c.user_id = u.id)',
        'bot_blocked': 'u.is_bot_blocked = 1',
    }
    return ['u.merged_into_user_id IS NULL'] + [
        predicates[key] for key in normalize_user_filters(value)
    ]
