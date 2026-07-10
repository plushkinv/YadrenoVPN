"""Canonical-плейсхолдеры для уведомлений, рассылок и event-шаблонов."""
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

CANONICAL_EVENT_PLACEHOLDERS = frozenset({
    '%event_type%',
    '%telegram_id%',
    '%пользователь_имя%',
    '%пользователь_username%',
    '%пользователь_дата_регистрации%',
    '%баланс%',
    '%ключ_имя%',
    '%ключ_дней_до_окончания%',
    '%ключ_трафик_процент_остатка%',
    '%ключ_трафик_использовано%',
    '%ключ_трафик_лимит%',
    '%реферал_имя%',
    '%реферал_логин%',
    '%реферал_telegram_id%',
    '%реферальный_уровень%',
    '%покупатель_имя%',
    '%покупатель_логин%',
    '%покупатель_telegram_id%',
    '%платеж_тариф%',
    '%платеж_сумма%',
    '%платеж_срок%',
    '%реферальное_вознаграждение%',
})
_CANONICAL_EVENT_PLACEHOLDER_KEYS = {
    placeholder.casefold() for placeholder in CANONICAL_EVENT_PLACEHOLDERS
}


class _HtmlToTextParser(HTMLParser):
    """Извлекает видимый текст из HTML для plain/url режимов."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return ''.join(self.parts)


def build_user_event_context(telegram_id: int | None) -> dict[str, Any]:
    """Возвращает общие значения пользователя для broadcast/event-шаблонов."""
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
    Подставляет canonical event-плейсхолдеры.

    Неизвестные плейсхолдеры остаются видимыми. Известные, но отсутствующие в
    контексте события, заменяются пустой строкой.
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
        normalized = placeholder.casefold()
        if normalized not in _CANONICAL_EVENT_PLACEHOLDER_KEYS:
            return placeholder
        return _format_value(_resolve_event_value(normalized, runtime_context), mode)

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


def _resolve_event_value(normalized: str, context: Mapping[str, Any]) -> Any:
    if normalized == '%event_type%':
        return _context_value(context, 'event_type')
    if normalized == '%telegram_id%':
        return _context_value(context, 'telegram_id')
    if normalized == '%пользователь_имя%':
        return _context_value(context, 'user_display_name', 'user_name')
    if normalized == '%пользователь_username%':
        return _context_value(context, 'user_username', 'username')
    if normalized == '%пользователь_дата_регистрации%':
        return _context_value(context, 'user_registered_at')
    if normalized == '%баланс%':
        return _context_value(context, 'user_balance_text', 'balance_text')
    if normalized == '%ключ_имя%':
        return _context_value(context, 'key_name', 'key_display_name', 'custom_name')
    if normalized == '%ключ_дней_до_окончания%':
        return _context_value(context, 'key_days_left', 'days_left')
    if normalized == '%ключ_трафик_процент_остатка%':
        return _context_value(context, 'key_traffic_remaining_percent', 'traffic_remaining_percent')
    if normalized == '%ключ_трафик_использовано%':
        return _context_value(context, 'key_traffic_used_text', 'traffic_used_text')
    if normalized == '%ключ_трафик_лимит%':
        return _context_value(context, 'key_traffic_limit_text', 'traffic_limit_text')
    if normalized == '%реферал_имя%':
        return _context_value(context, 'referral_name')
    if normalized == '%реферал_логин%':
        return _context_value(context, 'referral_login')
    if normalized == '%реферал_telegram_id%':
        return _context_value(context, 'referral_telegram_id')
    if normalized == '%реферальный_уровень%':
        return _context_value(context, 'referral_level', 'level')
    if normalized == '%покупатель_имя%':
        return _context_value(context, 'buyer_name')
    if normalized == '%покупатель_логин%':
        return _context_value(context, 'buyer_login')
    if normalized == '%покупатель_telegram_id%':
        return _context_value(context, 'buyer_telegram_id')
    if normalized == '%платеж_тариф%':
        return _context_value(context, 'payment_tariff_name', 'tariff_name')
    if normalized == '%платеж_сумма%':
        return _context_value(context, 'payment_amount_text')
    if normalized == '%платеж_срок%':
        return _context_value(context, 'payment_period_text', 'period_text')
    if normalized == '%реферальное_вознаграждение%':
        return _context_value(context, 'referral_reward_text')
    return None


__all__ = [
    'CANONICAL_EVENT_PLACEHOLDERS',
    'EVENT_TYPES',
    'build_user_event_context',
    'render_event_placeholders',
]
