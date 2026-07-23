"""Utilities for substituting placeholders into edited texts."""
from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape as unescape_html_entities
from html.parser import HTMLParser
from typing import Any, Literal, Optional
from urllib.parse import quote

from bot.utils.text import escape_html


_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')
_PARAMETERIZED_PLACEHOLDER_RE = re.compile(r'%([A-Za-z][A-Za-z0-9_]*)(?:\(([^%()\s]*)\))?%')
_PARAMETER_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r'%[^%\s]*[A-Za-zА-Яа-я_][^%\s]*%')
_URL_ESCAPE_RE = re.compile(r'%[0-9A-Fa-f]{2}')
PagePlaceholderMode = Literal['html', 'button_label', 'url']


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
    'selected_server': ('%выбранный_сервер%',),
    'key_copy': ('%ключ_для_копирования%',),
    'key_link': ('%ключ_ссылка%',),
    'key_link_url': ('%ключ_ссылка_url%',),
    'key_info': ('%ключ_информация%',),
    'key_history': ('%ключ_история_операций%',),
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
    'payment_wait_seconds': (),
    'payment_minimum': (),
    'promo_code': (),
    'promo_discount': (),
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
_PAGE_PLACEHOLDER_ALIASES: dict[str, str] = {}
for _name, _aliases in _PAGE_PLACEHOLDER_ALIASES_BY_NAME.items():
    _PAGE_PLACEHOLDER_ALIASES[f'%{_name}%'.casefold()] = _name
    for _alias in _aliases:
        _PAGE_PLACEHOLDER_ALIASES[_alias.casefold()] = _name
_PARAMETERIZED_PAGE_PLACEHOLDERS = frozenset({'key', 'tariffs'})


KEY_DELIVERY_RAW_CONTEXT_KEY = 'key_delivery_raw_value'
KEY_FIELDS_CONTEXT_KEY = 'key_fields'
KEY_PAGE_FIELDS = frozenset({
    'id',
    'name',
    'status',
    'traffic',
    'expires_at',
    'server',
    'inbound',
    'protocol',
    'tariff',
    'device_limit',
})


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

    return _PLACEHOLDER_RE.sub(replace_match, text)


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


def _resolve_placeholder_name(placeholder: str) -> tuple[str, dict[str, str]] | None:
    normalized = placeholder.casefold()
    alias_name = _PAGE_PLACEHOLDER_ALIASES.get(normalized)
    if alias_name is not None:
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


def _resolve_tariffs_placeholder(
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
    params: Mapping[str, str],
) -> str:
    if not params:
        return _format_value(_context_value(context, 'tariffs_html'), mode, html_ready=True)

    if set(params) != {'group_id'}:
        return ''
    group_id = _parse_positive_int(params.get('group_id'))
    if group_id is None:
        return ''

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
    if set(params) != {'field'}:
        return ''

    field = params.get('field', '').casefold()
    if field not in KEY_PAGE_FIELDS:
        return ''

    values = context.get(KEY_FIELDS_CONTEXT_KEY)
    if not isinstance(values, Mapping):
        return ''
    return _format_value(values.get(field), mode)


def _resolve_registered_placeholder(
    placeholder: str,
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
) -> str:
    resolved = _resolve_placeholder_name(placeholder)
    if resolved is None:
        return placeholder
    name, params = resolved

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
    if name == 'selected_server':
        return _format_value(_context_value(context, 'selected_server_name'), mode)
    if name == 'keys_list':
        return _format_value(_context_value(context, 'keys_list_html'), mode, html_ready=True)
    if name == 'key_info':
        return _format_value(_context_value(context, 'key_info_html'), mode, html_ready=True)
    if name == 'key_history':
        return _format_value(_context_value(context, 'key_history_html'), mode, html_ready=True)
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
        value = _context_value(context, 'payment_tariff_html')
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
) -> str:
    """
    Substitutes canonical placeholders for the page builder.

    Unknown placeholders remain visible. Known but not available in
    in the current context, the values are replaced with an empty string.
    """
    if text is None:
        return ''

    normalized_replacements = _normalize_replacements(replacements)
    runtime_context = _normalize_context(context)

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
        return _resolve_registered_placeholder(placeholder, runtime_context, mode)

    return _PLACEHOLDER_RE.sub(replace_match, str(text))
