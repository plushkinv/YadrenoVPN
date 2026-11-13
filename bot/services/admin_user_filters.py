"""Administrator copy and visibility for the user-directory filter contract."""

from localization import text as _tr
from database.user_filters import (
    USER_CHANNEL_FILTERS, USER_FILTER_KEYS, normalize_user_filters,
)


USER_FILTER_LABELS = {
    'active': _tr('admin_user_filters.module.1'),
    'no_keys': _tr('admin_user_filters.module.2'),
    'never_paid': _tr('admin_user_filters.module.3'),
    'expired': _tr('admin_user_filters.module.4'),
    'used_trial': _tr('admin_user_filters.module.5'),
    'banned': _tr('admin_user_filters.module.6'),
    'bot': _tr('admin_user_filters.module.7'),
    'site': _tr('admin_user_filters.module.8'),
    'bot_blocked': _tr('admin_user_filters.module.9'),
}


def available_user_filters(web_enabled: bool) -> tuple[str, ...]:
    return tuple(
        key for key in USER_FILTER_KEYS
        if web_enabled or key not in USER_CHANNEL_FILTERS
    )


def visible_user_filters(value: object, web_enabled: bool) -> tuple[str, ...]:
    available = available_user_filters(web_enabled)
    return tuple(key for key in normalize_user_filters(value) if key in available)


def user_filter_status(value: object) -> str:
    labels = [USER_FILTER_LABELS[key] for key in normalize_user_filters(value)]
    return _tr('admin_user_filters.user_filter_status.1') + _tr('admin_user_filters.user_filter_status.2').join(labels) if labels else _tr('admin_user_filters.user_filter_status.3')


def users_menu_text(stats: dict[str, int], *, web_enabled: bool) -> str:
    lines = [
        _tr('admin_user_filters.users_menu_text.1'), '', _tr('servers.build_server_view.18'),
        _tr('admin_user_filters.users_menu_text.2', p0=f"{stats['total']}"),
    ]
    lines.extend(
        f'{USER_FILTER_LABELS[key]}: <b>{stats[key]}</b>'
        for key in available_user_filters(web_enabled)
    )
    return '\n'.join(lines)
