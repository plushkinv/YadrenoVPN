"""Administrator copy and visibility for the user-directory filter contract."""
from database.user_filters import (
    USER_CHANNEL_FILTERS, USER_FILTER_KEYS, normalize_user_filters,
)


USER_FILTER_LABELS = {
    'active': 'С активными ключами',
    'no_keys': 'Без ключей',
    'never_paid': 'Никогда не покупали',
    'expired': 'Ключ истёк',
    'used_trial': 'Пробный период',
    'banned': 'Забаненные',
    'bot': 'В боте',
    'site': 'На сайте',
    'bot_blocked': 'Заблокировали бота',
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
    return 'Фильтры (И): ' + ' И '.join(labels) if labels else 'Фильтры не выбраны — все пользователи'


def users_menu_text(stats: dict[str, int], *, web_enabled: bool) -> str:
    lines = [
        '👥 <b>Пользователи</b>', '', '📊 <b>Статистика:</b>',
        f"👤 Всего: <b>{stats['total']}</b>",
    ]
    lines.extend(
        f'{USER_FILTER_LABELS[key]}: <b>{stats[key]}</b>'
        for key in available_user_filters(web_enabled)
    )
    return '\n'.join(lines)
