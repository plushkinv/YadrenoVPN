"""Utilities for substituting placeholders into edited texts."""
from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape as unescape_html_entities
from html.parser import HTMLParser
from typing import Any, Callable, Iterator, Literal, Optional
from urllib.parse import quote

from bot.utils.text import escape_html


_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')
_PARAMETERIZED_PLACEHOLDER_RE = re.compile(r'%([A-Za-z][A-Za-z0-9_]*)(?:\(([^%()\s]*)\))?%')
_PARAMETER_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r'%[^%\s]*[A-Za-zА-Яа-я_][^%\s]*%')
_URL_ESCAPE_RE = re.compile(r'%[0-9A-Fa-f]{2}')
PagePlaceholderMode = Literal['html', 'button_label', 'url', 'plain', 'url_component']
EventType = Literal[
    'broadcast', 'key_expiring', 'key_traffic_low', 'referral_new_ref', 'referral_purchase',
]
EVENT_TYPES = frozenset({
    'broadcast', 'key_expiring', 'key_traffic_low', 'referral_new_ref', 'referral_purchase',
})


_PAGE_PLACEHOLDER_ALIASES_BY_NAME = {
    'telegram_id': (),
    'bot_username': (),
    'telegram_link_domain': (),
    'page_key': (),
    'tariffs': ('%тарифы%',),
    'no_tariffs': ('%без_тарифов%',),
    'referral_link': ('%реферальная_ссылка%',),
    'referral_link_url': ('%реферальная_ссылка_url%',),
    'referral_stats': ('%реферальная_статистика%',),
    'profile': ('%профиль%',),
    'user_balance': ('%баланс%',),
    'user_name': ('%пользователь_имя%', '%user_display_name%'),
    'user_username': ('%пользователь_username%',),
    'user_registered_at': ('%пользователь_дата_регистрации%',),
    'keys_summary': ('%ключи_сводка%',),
    'keys_total': ('%ключи_всего%',),
    'keys_active': ('%ключи_активных%',),
    'keys_expired': ('%ключи_истекших%',),
    'retention_days': (),
    'deleted_key_count': (),
    'deleted_keys': (),
    'selected_server': ('%выбранный_сервер%',),
    'key_copy': ('%ключ_для_копирования%',),
    'key_link': ('%ключ_ссылка%',),
    'key_link_url': ('%ключ_ссылка_url%',),
    'key_info': ('%ключ_информация%',),
    'key_history': ('%ключ_история_операций%',),
    'devices_list': (),
    'keys_list': ('%список_ключей%',),
    'screen_data': ('%экран_данные%',),
    'key_replace_data': ('%замена_ключа_данные%',),
    'key_rename_data': ('%ключ_переименование_данные%',),
    'payment_provider': ('%платеж_провайдер%',),
    'payment_key_line': ('%платеж_ключ_строка%',),
    'payment_tariff': ('%платеж_тариф%',),
    'payment_amount': ('%платеж_сумма%',),
    'payment_nominal': ('%платеж_номинал%',),
    'payment_term_label': ('%платеж_срок_тип%',),
    'payment_term': ('%платеж_срок%',),
    'payment_link': ('%платеж_ссылка%',),
    'payment_link_url': ('%платеж_ссылка_url%',),
    'payment_instruction': ('%платеж_инструкция%',),
    'payment_hint': ('%платеж_подсказка%',),
    'payment_discount_line': ('%платеж_скидка_строка%',),
    'payment_balance': ('%платеж_баланс%',),
    'payment_balance_deduct': ('%платеж_списание_баланса%',),
    'payment_remaining': ('%платеж_остаток_к_оплате%',),
    'payment_topup_hint': ('%платеж_доплата_подсказка%',),
    'payment_base_currency': ('%платеж_базовая_валюта%',),
    'payment_error': ('%платеж_ошибка%',),
    'payment_coupon': (),
    'trial_offer': (),
    'trial_eligibility': (),
    'payment_wait_seconds': (),
    'payment_minimum': (),
    'promo_code': (),
    'promo_discount': (),
    'promo_expires_at': (),
    'support_title': ('%поддержка_заголовок%',),
    'support_instruction': ('%поддержка_инструкция%',),
    'support_status_title': ('%поддержка_статус_заголовок%',),
    'support_status_text': ('%поддержка_статус_текст%',),
    'promo_status_title': ('%промо_статус_заголовок%',),
    'promo_status_text': ('%промо_статус_текст%',),
    'key_status_title': ('%ключ_статус_заголовок%',),
    'key_status_text': ('%ключ_статус_текст%',),
}
CANONICAL_PAGE_PLACEHOLDERS = frozenset(
    f'%{name}%' for name in _PAGE_PLACEHOLDER_ALIASES_BY_NAME
)

# Event-only names retain their published scope; common names are defined above.
_EVENT_ONLY_ALIASES_BY_NAME = {
    'event_type': (),
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
    'referral_reward': ('%реферальное_вознаграждение%',),
}
CANONICAL_EVENT_PLACEHOLDERS = CANONICAL_PAGE_PLACEHOLDERS | frozenset(
    f'%{name}%' for name in _EVENT_ONLY_ALIASES_BY_NAME
)
_PLACEHOLDER_ALIASES: dict[str, str] = {}
for _name, _aliases in {
    **_PAGE_PLACEHOLDER_ALIASES_BY_NAME, **_EVENT_ONLY_ALIASES_BY_NAME,
}.items():
    _PLACEHOLDER_ALIASES[f'%{_name}%'.casefold()] = _name
    for _alias in _aliases:
        _PLACEHOLDER_ALIASES[_alias.casefold()] = _name
_PARAMETERIZED_PAGE_PLACEHOLDERS = frozenset({
    'key',
    'payment_coupon',
    'tariffs',
    'trial_offer',
})


KEY_DELIVERY_RAW_CONTEXT_KEY = 'key_delivery_raw_value'
KEY_FIELDS_CONTEXT_KEY = 'key_fields'
KEY_PAGE_FIELDS = frozenset({
    'id',
    'name',
    'status',
    'traffic',
    'expires_at',
    'server',
    'tariff',
    'device_limit',
})
PAYMENT_COUPON_FIELDS_CONTEXT_KEY = 'payment_coupon_fields'
PAYMENT_COUPON_PAGE_FIELDS = frozenset({
    'code',
    'discount_percent',
    'lifetime_days',
})
TRIAL_OFFER_FIELDS_CONTEXT_KEY = 'trial_offer_fields'
TRIAL_OFFER_PAGE_FIELDS = frozenset({
    'tariff',
    'group',
    'duration',
    'traffic',
    'device_limit',
})
_PARAMETER_FIELDS = {
    'key': KEY_PAGE_FIELDS,
    'payment_coupon': PAYMENT_COUPON_PAGE_FIELDS,
    'trial_offer': TRIAL_OFFER_PAGE_FIELDS,
}
_EVENT_VALUE_KEYS = {
    'key_name': ('key_name', 'key_display_name', 'custom_name'),
    'key_days_left': ('key_days_left', 'days_left'),
    'key_traffic_remaining_percent': ('key_traffic_remaining_percent', 'traffic_remaining_percent'),
    'key_traffic_used': ('key_traffic_used_text', 'traffic_used_text'),
    'key_traffic_limit': ('key_traffic_limit_text', 'traffic_limit_text'),
    'referral_level': ('referral_level', 'level'),
    'referral_reward': ('referral_reward_text',),
}


def _iter_placeholder_matches(text: str) -> Iterator[re.Match[str]]:
    """Scan source tokens without letting URL escapes consume the next token."""
    offset = 0
    escape_end = -1
    while match := _PLACEHOLDER_RE.search(text, offset):
        token = match.group(0)
        in_escape_sequence = len(token) == 4 and (
            match.start() == escape_end or _URL_ESCAPE_RE.match(text, match.start() + 3)
        )
        if (
            token.casefold() not in _PLACEHOLDER_ALIASES
            and _URL_ESCAPE_RE.match(text, match.start())
            and (in_escape_sequence or not _PARAMETERIZED_PLACEHOLDER_RE.fullmatch(token))
        ):
            offset = match.start() + 3
            escape_end = offset
            continue
        yield match
        offset = match.end()


def _substitute_placeholders(text: str, replace: Callable[[re.Match[str]], str]) -> str:
    """Replace source tokens once; inserted values are never scanned again."""
    parts: list[str] = []
    offset = 0
    for match in _iter_placeholder_matches(text):
        parts.extend((text[offset:match.start()], replace(match)))
        offset = match.end()
    parts.append(text[offset:])
    return ''.join(parts)


class _HtmlToTextParser(HTMLParser):
    """Extracts visible text from HTML for button labels."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return ''.join(self.parts)


def apply_placeholder_replacements(
    text: str | None,
    replacements: Mapping[str, Any] | None,
) -> str:
    """
    Substitutes placeholder values in a case-insensitive manner.

    Comparison is done via Unicode-aware casefold(), so Russian letters
    in placeholders they work in any register. Unknown placeholders remain
    the text is unchanged, and the inserted values are not reprocessed.
    """
    if text is None:
        return ''
    if not replacements:
        return text

    normalized = {
        str(placeholder).casefold(): '' if value is None else str(value)
        for placeholder, value in replacements.items()
    }

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return normalized.get(placeholder.casefold(), placeholder)

    return _substitute_placeholders(text, replace_match)


def contains_placeholder(text: str | None) -> bool:
    """Checks whether placeholders of the form `%...%` remain in the row."""
    if not text:
        return False
    cleaned = _URL_ESCAPE_RE.sub('', str(text))
    return bool(_UNRESOLVED_PLACEHOLDER_RE.search(cleaned))


def _normalize_replacements(
    replacements: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if replacements is None:
        return {}
    if not isinstance(replacements, Mapping):
        raise ValueError('replacements должен быть mapping или None')
    return {
        str(placeholder).casefold(): '' if value is None else value
        for placeholder, value in replacements.items()
    }


def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise ValueError('context должен быть mapping или None')
    return dict(context)


def _html_to_plain_text(value: Any) -> str:
    parser = _HtmlToTextParser()
    parser.feed(str(value))
    text = parser.get_text()
    return ' '.join(unescape_html_entities(text).split())


def _format_value(
    value: Any,
    mode: PagePlaceholderMode,
    *,
    html_ready: bool = False,
    url_encode: bool = False,
) -> str:
    raw = '' if value is None else str(value)

    if mode == 'html':
        return raw if html_ready else escape_html(raw)

    if mode == 'button_label':
        return _html_to_plain_text(raw) if html_ready else ' '.join(raw.split())

    if mode in {'plain', 'url_component'}:
        plain = _html_to_plain_text(raw)
        return quote(plain, safe='') if mode == 'url_component' else plain

    if mode == 'url':
        plain = _html_to_plain_text(raw) if html_ready else raw
        return quote(plain, safe='') if url_encode else plain

    return raw


def _context_value(context: Mapping[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = context.get(key)
        if value is not None:
            return value
    return None


def _parse_placeholder_parameters(raw: str | None) -> dict[str, str] | None:
    if raw is None:
        return {}
    if raw == '':
        return None

    params: dict[str, str] = {}
    for item in raw.split(','):
        if '=' not in item:
            return None
        key, value = item.split('=', 1)
        if not key or not value or not _PARAMETER_NAME_RE.fullmatch(key):
            return None
        params[key.casefold()] = value
    return params


def _resolve_placeholder_name(
    placeholder: str,
    event_type: str | None = None,
) -> tuple[str, dict[str, str]] | None:
    normalized = placeholder.casefold()
    alias_name = _PLACEHOLDER_ALIASES.get(normalized)
    if alias_name is not None:
        if alias_name in _EVENT_ONLY_ALIASES_BY_NAME and event_type is None:
            return None
        return alias_name, {}

    match = _PARAMETERIZED_PLACEHOLDER_RE.fullmatch(placeholder)
    if not match:
        return None

    name = match.group(1).casefold()
    if name not in _PARAMETERIZED_PAGE_PLACEHOLDERS:
        return None

    params = _parse_placeholder_parameters(match.group(2))
    if params is None:
        return name, {'__invalid__': ''}
    return name, params


def _parse_positive_int(value: Any) -> int | None:
    if not isinstance(value, str) or not value.isdecimal():
        return None
    number = int(value)
    return number if number > 0 else None


def valid_placeholder_parameters(name: str, params: Mapping[str, str]) -> bool:
    """Apply the same bounded parameter contract in rendering and validation."""
    if not params:
        return name != 'key'
    if name == 'tariffs':
        return set(params) == {'group_id'} and _parse_positive_int(params['group_id']) is not None
    fields = _PARAMETER_FIELDS.get(name)
    return bool(fields and set(params) == {'field'} and params['field'].casefold() in fields)


def normalize_event_placeholder_context(
    event_type: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt released event context keys to the page engine without changing inputs."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f'неизвестный event_type: {event_type}')
    normalized = _normalize_context(context)
    normalized.setdefault('event_type', event_type)
    for canonical, sources in {
        'user_display_name': ('user_display_name', 'user_name'),
        'user_username': ('user_username', 'username'),
        'user_balance_text': ('user_balance_text', 'balance_text'),
        'payment_term_text': ('payment_period_text', 'period_text', 'payment_term_text'),
    }.items():
        value = _context_value(normalized, *sources)
        if value is not None:
            normalized[canonical] = value
    return normalized


def get_template_placeholder_specs(
    text: str,
    *,
    event_type: str | None = None,
) -> dict[str, tuple[str, dict[str, str]] | None]:
    """Inspect source tokens without resolving values or accessing runtime data."""
    if event_type is not None and event_type not in EVENT_TYPES:
        raise ValueError(f'неизвестный event_type: {event_type}')
    return {
        match.group(0): _resolve_placeholder_name(match.group(0), event_type)
        for match in _iter_placeholder_matches(text)
    }


def get_placeholder_contract(*, include_events: bool = False) -> dict[str, Any]:
    """Describe installed placeholder capabilities without recipient values."""
    return {
        'engine': 'page',
        'common': {f'%{name}%': list(aliases) for name, aliases in _PAGE_PLACEHOLDER_ALIASES_BY_NAME.items()},
        'event_only': {
            f'%{name}%': list(aliases) for name, aliases in _EVENT_ONLY_ALIASES_BY_NAME.items()
        } if include_events else {},
        'event_types': sorted(EVENT_TYPES) if include_events else [],
        'parameter_syntax': '%name(parameter=value)%',
        'parameters': {
            **{name: {'field': sorted(fields)} for name, fields in _PARAMETER_FIELDS.items()},
            'tariffs': {'group_id': 'positive_integer'},
        },
        'missing_context': 'empty',
        'unknown': 'preserved_at_render',
    }


def _resolve_tariffs_placeholder(
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    params: Mapping[str, str],
) -> str:
    if not params:
        return _format_value(_context_value(context, 'tariffs_html'), mode, html_ready=True)

    group_id = int(params['group_id'])

    from bot.utils.page_dynamic_data import build_tariff_text

    return _format_value(
        build_tariff_text(group_id=group_id, include_title=False),
        mode,
        html_ready=True,
    )


def _resolve_key_placeholder(
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    params: Mapping[str, str],
) -> str:
    """Resolves one allowlisted display field of the current key."""
    field = params['field'].casefold()

    values = context.get(KEY_FIELDS_CONTEXT_KEY)
    if not isinstance(values, Mapping):
        return ''
    return _format_value(values.get(field), mode)


def _resolve_payment_coupon_placeholder(
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    params: Mapping[str, str],
) -> str:
    """Resolves either the composite coupon or one allowlisted display field."""
    if not params:
        return _format_value(
            _context_value(context, 'payment_coupon_html'),
            mode,
            html_ready=True,
        )
    field = params['field'].casefold()

    values = context.get(PAYMENT_COUPON_FIELDS_CONTEXT_KEY)
    if not isinstance(values, Mapping):
        return ''
    return _format_value(values.get(field), mode)


def _resolve_trial_offer_placeholder(
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    params: Mapping[str, str],
) -> str:
    """Resolves either the composite offer or one allowlisted display field."""
    if not params:
        return _format_value(
            _context_value(context, 'trial_offer_html'),
            mode,
            html_ready=True,
        )
    field = params['field'].casefold()
    values = context.get(TRIAL_OFFER_FIELDS_CONTEXT_KEY)
    if not isinstance(values, Mapping):
        return ''
    return _format_value(values.get(field), mode)


def _resolve_registered_placeholder(
    placeholder: str,
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    event_type: str | None = None,
) -> str:
    resolved = _resolve_placeholder_name(placeholder, event_type)
    if resolved is None:
        return placeholder
    name, params = resolved
    if not valid_placeholder_parameters(name, params):
        return ''
    if name in _EVENT_ONLY_ALIASES_BY_NAME:
        return _format_value(_context_value(context, *_EVENT_VALUE_KEYS.get(name, (name,))), mode)

    if name == 'telegram_id':
        return _format_value(_context_value(context, 'telegram_id'), mode)
    if name == 'bot_username':
        return _format_value(_context_value(context, 'bot_username'), mode)
    if name == 'telegram_link_domain':
        from bot.utils.telegram_links import get_telegram_link_domain

        return _format_value(get_telegram_link_domain(), mode)
    if name == 'page_key':
        return _format_value(_context_value(context, 'page_key'), mode)
    if name == 'tariffs':
        return _resolve_tariffs_placeholder(context, mode, params)
    if name == 'key':
        return _resolve_key_placeholder(context, mode, params)
    if name == 'payment_coupon':
        return _resolve_payment_coupon_placeholder(context, mode, params)
    if name == 'trial_offer':
        return _resolve_trial_offer_placeholder(context, mode, params)
    if name == 'trial_eligibility':
        return _format_value(
            _context_value(context, 'trial_eligibility_html'),
            mode,
            html_ready=True,
        )
    if name == 'no_tariffs':
        return ''
    if name == 'referral_link':
        return _format_value(_context_value(context, 'referral_link'), mode)
    if name == 'referral_link_url':
        return _format_value(
            _context_value(context, 'referral_link'),
            mode,
            url_encode=(mode == 'url'),
        )
    if name == 'referral_stats':
        return _format_value(_context_value(context, 'referral_stats_html'), mode, html_ready=True)
    if name == 'profile':
        return _format_value(_context_value(context, 'user_profile_html'), mode, html_ready=True)
    if name == 'user_balance':
        return _format_value(_context_value(context, 'user_balance_text'), mode)
    if name == 'user_name':
        return _format_value(_context_value(context, 'user_display_name'), mode)
    if name == 'user_username':
        return _format_value(_context_value(context, 'user_username'), mode)
    if name == 'user_registered_at':
        return _format_value(_context_value(context, 'user_registered_at'), mode)
    if name == 'keys_summary':
        return _format_value(_context_value(context, 'keys_summary_html'), mode, html_ready=True)
    if name == 'keys_total':
        return _format_value(_context_value(context, 'keys_total_count'), mode)
    if name == 'keys_active':
        return _format_value(_context_value(context, 'keys_active_count'), mode)
    if name == 'keys_expired':
        return _format_value(_context_value(context, 'keys_expired_count'), mode)
    if name == 'retention_days':
        return _format_value(_context_value(context, 'retention_days'), mode)
    if name == 'deleted_key_count':
        return _format_value(_context_value(context, 'deleted_key_count'), mode)
    if name == 'deleted_keys':
        return _format_value(
            _context_value(context, 'deleted_keys_html'),
            mode,
            html_ready=True,
        )
    if name == 'selected_server':
        return _format_value(_context_value(context, 'selected_server_name'), mode)
    if name == 'keys_list':
        return _format_value(_context_value(context, 'keys_list_html'), mode, html_ready=True)
    if name == 'key_info':
        return _format_value(_context_value(context, 'key_info_html'), mode, html_ready=True)
    if name == 'key_history':
        return _format_value(_context_value(context, 'key_history_html'), mode, html_ready=True)
    if name == 'devices_list':
        return _format_value(_context_value(context, 'devices_list_html'), mode, html_ready=True)
    if name == 'screen_data':
        return _format_value(_context_value(context, 'screen_data_html'), mode, html_ready=True)
    if name == 'key_replace_data':
        return _format_value(_context_value(context, 'key_replace_data_html'), mode, html_ready=True)
    if name == 'key_rename_data':
        return _format_value(_context_value(context, 'key_rename_data_html'), mode, html_ready=True)

    if name == 'payment_provider':
        value = _context_value(context, 'payment_provider_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_provider_title'), mode)
    if name == 'payment_key_line':
        return _format_value(_context_value(context, 'payment_key_line_html'), mode, html_ready=True)
    if name == 'payment_tariff':
        value = _context_value(context, 'payment_tariff_html') if event_type is None else None
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_tariff_name', 'tariff_name'), mode)
    if name == 'payment_amount':
        return _format_value(_context_value(context, 'payment_amount_text'), mode)
    if name == 'payment_nominal':
        return _format_value(_context_value(context, 'payment_nominal_text'), mode)
    if name == 'payment_term_label':
        return _format_value(_context_value(context, 'payment_term_label'), mode)
    if name == 'payment_term':
        return _format_value(_context_value(context, 'payment_term_text'), mode)
    if name == 'payment_link':
        if mode == 'html':
            link_html = _context_value(context, 'payment_link_html')
            if link_html is not None:
                return _format_value(link_html, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_url'), mode)
    if name == 'payment_link_url':
        return _format_value(
            _context_value(context, 'payment_url'),
            mode,
            url_encode=(mode == 'url'),
        )
    if name == 'payment_instruction':
        return _format_value(_context_value(context, 'payment_instruction_html'), mode, html_ready=True)
    if name == 'payment_hint':
        return _format_value(_context_value(context, 'payment_hint_text'), mode)
    if name == 'payment_discount_line':
        return _format_value(_context_value(context, 'payment_discount_line_html'), mode, html_ready=True)
    if name == 'payment_balance':
        return _format_value(_context_value(context, 'payment_balance_text'), mode)
    if name == 'payment_balance_deduct':
        return _format_value(_context_value(context, 'payment_balance_deduct_text'), mode)
    if name == 'payment_remaining':
        return _format_value(_context_value(context, 'payment_remaining_text'), mode)
    if name == 'payment_topup_hint':
        return _format_value(_context_value(context, 'payment_topup_hint_html'), mode, html_ready=True)
    if name == 'payment_base_currency':
        return _format_value(_context_value(context, 'payment_base_currency'), mode)
    if name == 'payment_error':
        return _format_value(_context_value(context, 'payment_error_html'), mode, html_ready=True)
    if name == 'payment_wait_seconds':
        return _format_value(_context_value(context, 'payment_wait_seconds'), mode)
    if name == 'payment_minimum':
        return _format_value(_context_value(context, 'payment_minimum_text'), mode)
    if name == 'promo_code':
        return _format_value(_context_value(context, 'promo_code'), mode)
    if name == 'promo_discount':
        return _format_value(_context_value(context, 'promo_discount'), mode)
    if name == 'promo_expires_at':
        return _format_value(_context_value(context, 'promo_expires_at'), mode)
    if name == 'support_title':
        value = _context_value(context, 'support_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_title'), mode)
    if name == 'support_instruction':
        value = _context_value(context, 'support_instruction_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_instruction'), mode)
    if name == 'support_status_title':
        value = _context_value(context, 'support_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_status_title'), mode)
    if name == 'support_status_text':
        value = _context_value(context, 'support_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_status_body'), mode)
    if name == 'promo_status_title':
        value = _context_value(context, 'promo_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'promo_status_title'), mode)
    if name == 'promo_status_text':
        value = _context_value(context, 'promo_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'promo_status_body'), mode)
    if name == 'key_status_title':
        value = _context_value(context, 'key_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'key_status_title'), mode)
    if name == 'key_status_text':
        value = _context_value(context, 'key_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'key_status_body'), mode)

    raw_key = _context_value(context, KEY_DELIVERY_RAW_CONTEXT_KEY, 'key_raw_value')
    if name == 'key_copy':
        if raw_key is None:
            return ''
        if mode == 'html':
            return f"<code>{escape_html(str(raw_key))}</code>"
        return _format_value(raw_key, mode)
    if name == 'key_link':
        return _format_value(raw_key, mode)
    if name == 'key_link_url':
        return _format_value(raw_key, mode, url_encode=(mode == 'url'))

    return ''


def apply_page_placeholders(
    text: str | None,
    replacements: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    *,
    mode: PagePlaceholderMode = 'html',
    event_type: str | None = None,
) -> str:
    """
    Substitutes canonical placeholders for the page builder.

    Unknown placeholders remain visible. Known but not available in
    in the current context, the values are replaced with an empty string.
    """
    if text is None:
        return ''

    normalized_replacements = _normalize_replacements(replacements)
    runtime_context = (
        normalize_event_placeholder_context(event_type, context)
        if event_type is not None else _normalize_context(context)
    )

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        normalized = placeholder.casefold()
        if normalized in normalized_replacements:
            return _format_value(
                normalized_replacements[normalized],
                mode,
                html_ready=True,
                url_encode=normalized.endswith('_url%') and mode == 'url',
            )
        return _resolve_registered_placeholder(placeholder, runtime_context, mode, event_type)

    return _substitute_placeholders(str(text), replace_match)
