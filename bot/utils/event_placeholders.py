"""Canonical placeholders for notifications, mailings and event templates."""
from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape as unescape_html_entities
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import quote

from bot.utils.text import escape_html


EventPlaceholderMode = Literal['html', 'plain', 'url']
EventType = Literal[
    'broadcast',
    'key_expiring',
    'key_traffic_low',
    'referral_new_ref',
    'referral_purchase',
]

EVENT_TYPES = frozenset({
    'broadcast',
    'key_expiring',
    'key_traffic_low',
    'referral_new_ref',
    'referral_purchase',
})

_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')

_EVENT_PLACEHOLDER_ALIASES_BY_NAME = {
    'event_type': (),
    'telegram_id': (),
    'user_name': ('%пользователь_имя%', '%user_display_name%'),
    'user_username': ('%пользователь_username%',),
    'user_registered_at': ('%пользователь_дата_регистрации%',),
    'user_balance': ('%баланс%',),
    'key_name': ('%ключ_имя%',),
    'key_days_left': ('%ключ_дней_до_окончания%',),
    'key_traffic_remaining_percent': ('%ключ_трафик_процент_остатка%',),
    'key_traffic_used': ('%ключ_трафик_использовано%',),
    'key_traffic_limit': ('%ключ_трафик_лимит%',),
    'referral_name': ('%реферал_имя%',),
    'referral_login': ('%реферал_логин%',),
    'referral_telegram_id': ('%реферал_telegram_id%',),
    'referral_level': ('%реферальный_уровень%',),
    'buyer_name': ('%покупатель_имя%',),
    'buyer_login': ('%покупатель_логин%',),
    'buyer_telegram_id': ('%покупатель_telegram_id%',),
    'payment_tariff': ('%платеж_тариф%',),
    'payment_amount': ('%платеж_сумма%',),
    'payment_term': ('%платеж_срок%',),
    'referral_reward': ('%реферальное_вознаграждение%',),
}
CANONICAL_EVENT_PLACEHOLDERS = frozenset(
    f'%{name}%' for name in _EVENT_PLACEHOLDER_ALIASES_BY_NAME
)
_EVENT_PLACEHOLDER_ALIASES: dict[str, str] = {}
for _name, _aliases in _EVENT_PLACEHOLDER_ALIASES_BY_NAME.items():
    _EVENT_PLACEHOLDER_ALIASES[f'%{_name}%'.casefold()] = _name
    for _alias in _aliases:
        _EVENT_PLACEHOLDER_ALIASES[_alias.casefold()] = _name


class _HtmlToTextParser(HTMLParser):
    """Extracts visible text from HTML for plain/url modes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return ''.join(self.parts)


def build_user_event_context(telegram_id: int | None) -> dict[str, Any]:
    """Returns the user's general values for broadcast/event templates."""
    if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
        return {}

    from bot.utils.datetime_format import format_date_for_display
    from bot.utils.page_dynamic_data import format_price_compact
    from database.requests import get_user_balance, get_user_by_telegram_id

    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return {'telegram_id': telegram_id}

    user_id = int(user.get('id') or 0)
    balance = get_user_balance(user_id) if user_id else 0
    username = _format_username(user.get('username'))
    display_name = _format_user_display_name(user)

    return {
        'telegram_id': telegram_id,
        'user_display_name': display_name,
        'user_username': username,
        'user_registered_at': format_date_for_display(user.get('created_at')),
        'user_balance_text': format_price_compact(balance),
    }


def render_event_placeholders(
    text: str | None,
    event_type: str,
    context: Mapping[str, Any] | None = None,
    *,
    mode: EventPlaceholderMode = 'html',
) -> str:
    """
    Substitutes canonical event placeholders.

    Unknown placeholders remain visible. Known but missing from
    event context are replaced with an empty string.
    """
    if text is None:
        return ''
    if event_type not in EVENT_TYPES:
        raise ValueError(f"неизвестный event_type: {event_type}")
    if context is None:
        runtime_context: dict[str, Any] = {}
    elif isinstance(context, Mapping):
        runtime_context = dict(context)
    else:
        raise ValueError('context должен быть mapping или None')

    runtime_context.setdefault('event_type', event_type)

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        name = _EVENT_PLACEHOLDER_ALIASES.get(placeholder.casefold())
        if name is None:
            return placeholder
        return _format_value(_resolve_event_value(name, runtime_context), mode)

    return _PLACEHOLDER_RE.sub(replace_match, str(text))


def _format_username(username: Any) -> str:
    if not username:
        return ''
    value = str(username).strip()
    if not value:
        return ''
    return value if value.startswith('@') else f'@{value}'


def _format_user_display_name(user: Mapping[str, Any]) -> str:
    parts = [
        str(user.get('first_name') or '').strip(),
        str(user.get('last_name') or '').strip(),
    ]
    full_name = ' '.join(part for part in parts if part)
    if full_name:
        return full_name
    username = _format_username(user.get('username'))
    if username:
        return username
    telegram_id = user.get('telegram_id')
    return f"ID {telegram_id}" if telegram_id else 'пользователь'


def _html_to_plain_text(value: Any) -> str:
    parser = _HtmlToTextParser()
    parser.feed(str(value))
    text = parser.get_text()
    return ' '.join(unescape_html_entities(text).split())


def _format_value(value: Any, mode: EventPlaceholderMode) -> str:
    raw = '' if value is None else str(value)
    if mode == 'html':
        return escape_html(raw)
    if mode == 'plain':
        return _html_to_plain_text(raw)
    if mode == 'url':
        return quote(_html_to_plain_text(raw), safe='')
    return raw


def _context_value(context: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = context.get(key)
        if value is not None:
            return value
    return None


def _resolve_event_value(name: str, context: Mapping[str, Any]) -> Any:
    if name == 'event_type':
        return _context_value(context, 'event_type')
    if name == 'telegram_id':
        return _context_value(context, 'telegram_id')
    if name == 'user_name':
        return _context_value(context, 'user_display_name', 'user_name')
    if name == 'user_username':
        return _context_value(context, 'user_username', 'username')
    if name == 'user_registered_at':
        return _context_value(context, 'user_registered_at')
    if name == 'user_balance':
        return _context_value(context, 'user_balance_text', 'balance_text')
    if name == 'key_name':
        return _context_value(context, 'key_name', 'key_display_name', 'custom_name')
    if name == 'key_days_left':
        return _context_value(context, 'key_days_left', 'days_left')
    if name == 'key_traffic_remaining_percent':
        return _context_value(context, 'key_traffic_remaining_percent', 'traffic_remaining_percent')
    if name == 'key_traffic_used':
        return _context_value(context, 'key_traffic_used_text', 'traffic_used_text')
    if name == 'key_traffic_limit':
        return _context_value(context, 'key_traffic_limit_text', 'traffic_limit_text')
    if name == 'referral_name':
        return _context_value(context, 'referral_name')
    if name == 'referral_login':
        return _context_value(context, 'referral_login')
    if name == 'referral_telegram_id':
        return _context_value(context, 'referral_telegram_id')
    if name == 'referral_level':
        return _context_value(context, 'referral_level', 'level')
    if name == 'buyer_name':
        return _context_value(context, 'buyer_name')
    if name == 'buyer_login':
        return _context_value(context, 'buyer_login')
    if name == 'buyer_telegram_id':
        return _context_value(context, 'buyer_telegram_id')
    if name == 'payment_tariff':
        return _context_value(context, 'payment_tariff_name', 'tariff_name')
    if name == 'payment_amount':
        return _context_value(context, 'payment_amount_text')
    if name == 'payment_term':
        return _context_value(context, 'payment_period_text', 'period_text')
    if name == 'referral_reward':
        return _context_value(context, 'referral_reward_text')
    return None


__all__ = [
    'CANONICAL_EVENT_PLACEHOLDERS',
    'EVENT_TYPES',
    'build_user_event_context',
    'render_event_placeholders',
]
