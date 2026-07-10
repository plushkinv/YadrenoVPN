"""Утилиты для подстановки плейсхолдеров в редактируемые тексты."""
from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape as unescape_html_entities
from html.parser import HTMLParser
from typing import Any, Literal, Optional
from urllib.parse import quote

from bot.utils.text import escape_html


_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r'%[^%\s]*[A-Za-zА-Яа-я_][^%\s]*%')
_URL_ESCAPE_RE = re.compile(r'%[0-9A-Fa-f]{2}')
PagePlaceholderMode = Literal['html', 'button_label', 'url']


CANONICAL_PAGE_PLACEHOLDERS = frozenset({
    '%telegram_id%',
    '%bot_username%',
    '%page_key%',
    '%тарифы%',
    '%без_тарифов%',
    '%реферальная_ссылка%',
    '%реферальная_ссылка_url%',
    '%реферальная_статистика%',
    '%профиль%',
    '%баланс%',
    '%пользователь_имя%',
    '%пользователь_username%',
    '%пользователь_дата_регистрации%',
    '%ключи_сводка%',
    '%ключи_всего%',
    '%ключи_активных%',
    '%ключи_истекших%',
    '%ключ_для_копирования%',
    '%ключ_ссылка%',
    '%ключ_ссылка_url%',
    '%ключ_имя%',
    '%ключ_информация%',
    '%ключ_история_операций%',
    '%ключ_статус%',
    '%ключ_трафик%',
    '%ключ_дата_окончания%',
    '%ключ_сервер%',
    '%ключ_инбаунд%',
    '%ключ_протокол%',
    '%ключ_id%',
    '%список_ключей%',
    '%экран_данные%',
    '%замена_ключа_данные%',
    '%ключ_переименование_данные%',
    '%платеж_провайдер%',
    '%платеж_ключ_строка%',
    '%платеж_тариф%',
    '%платеж_сумма%',
    '%платеж_срок_тип%',
    '%платеж_срок%',
    '%платеж_ссылка%',
    '%платеж_ссылка_url%',
    '%платеж_инструкция%',
    '%платеж_подсказка%',
    '%платеж_скидка_строка%',
    '%платеж_баланс%',
    '%платеж_списание_баланса%',
    '%платеж_остаток_к_оплате%',
    '%платеж_доплата_подсказка%',
    '%поддержка_заголовок%',
    '%поддержка_инструкция%',
    '%поддержка_статус_заголовок%',
    '%поддержка_статус_текст%',
    '%промо_статус_заголовок%',
    '%промо_статус_текст%',
    '%ключ_статус_заголовок%',
    '%ключ_статус_текст%',
})
_CANONICAL_PAGE_PLACEHOLDER_KEYS = {
    item.casefold() for item in CANONICAL_PAGE_PLACEHOLDERS
}


KEY_DELIVERY_RAW_CONTEXT_KEY = 'key_delivery_raw_value'


class _HtmlToTextParser(HTMLParser):
    """Извлекает видимый текст из HTML для подписей кнопок."""

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
    Подставляет значения плейсхолдеров без учёта регистра.

    Сравнение выполняется через Unicode-aware casefold(), поэтому русские буквы
    в плейсхолдерах работают в любом регистре. Неизвестные плейсхолдеры остаются
    в тексте без изменений, а вставленные значения повторно не обрабатываются.
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
    """Проверяет, остались ли в строке плейсхолдеры вида `%...%`."""
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


def _resolve_registered_placeholder(
    placeholder: str,
    context: Mapping[str, Any],
    mode: PagePlaceholderMode,
) -> str:
    normalized = placeholder.casefold()
    if normalized not in _CANONICAL_PAGE_PLACEHOLDER_KEYS:
        return placeholder

    if normalized == '%telegram_id%':
        return _format_value(_context_value(context, 'telegram_id'), mode)
    if normalized == '%bot_username%':
        return _format_value(_context_value(context, 'bot_username'), mode)
    if normalized == '%page_key%':
        return _format_value(_context_value(context, 'page_key'), mode)
    if normalized == '%тарифы%':
        return _format_value(_context_value(context, 'tariffs_html'), mode, html_ready=True)
    if normalized == '%без_тарифов%':
        return ''
    if normalized == '%реферальная_ссылка%':
        return _format_value(_context_value(context, 'referral_link'), mode)
    if normalized == '%реферальная_ссылка_url%':
        return _format_value(
            _context_value(context, 'referral_link'),
            mode,
            url_encode=(mode == 'url'),
        )
    if normalized == '%реферальная_статистика%':
        return _format_value(_context_value(context, 'referral_stats_html'), mode, html_ready=True)
    if normalized == '%профиль%':
        return _format_value(_context_value(context, 'user_profile_html'), mode, html_ready=True)
    if normalized == '%баланс%':
        return _format_value(_context_value(context, 'user_balance_text'), mode)
    if normalized == '%пользователь_имя%':
        return _format_value(_context_value(context, 'user_display_name'), mode)
    if normalized == '%пользователь_username%':
        return _format_value(_context_value(context, 'user_username'), mode)
    if normalized == '%пользователь_дата_регистрации%':
        return _format_value(_context_value(context, 'user_registered_at'), mode)
    if normalized == '%ключи_сводка%':
        return _format_value(_context_value(context, 'keys_summary_html'), mode, html_ready=True)
    if normalized == '%ключи_всего%':
        return _format_value(_context_value(context, 'keys_total_count'), mode)
    if normalized == '%ключи_активных%':
        return _format_value(_context_value(context, 'keys_active_count'), mode)
    if normalized == '%ключи_истекших%':
        return _format_value(_context_value(context, 'keys_expired_count'), mode)
    if normalized == '%ключ_имя%':
        return _format_value(_context_value(context, 'key_name', 'key_display_name', 'display_name'), mode)
    if normalized == '%ключ_статус%':
        return _format_value(_context_value(context, 'key_status', 'key_status_text'), mode)
    if normalized == '%ключ_трафик%':
        return _format_value(_context_value(context, 'key_traffic_text', 'key_traffic', 'traffic_info'), mode)
    if normalized == '%ключ_дата_окончания%':
        return _format_value(
            _context_value(
                context,
                'key_expires_text',
                'key_expires_at',
                'key_expiration_date',
                'expires_at',
            ),
            mode,
        )
    if normalized == '%ключ_сервер%':
        return _format_value(_context_value(context, 'key_server_name', 'key_server', 'server_name'), mode)
    if normalized == '%ключ_инбаунд%':
        return _format_value(_context_value(context, 'key_inbound_name', 'key_inbound', 'inbound_name'), mode)
    if normalized == '%ключ_протокол%':
        return _format_value(_context_value(context, 'key_protocol', 'protocol'), mode)
    if normalized == '%ключ_id%':
        return _format_value(_context_value(context, 'key_id'), mode)
    if normalized == '%список_ключей%':
        return _format_value(_context_value(context, 'keys_list_html'), mode, html_ready=True)
    if normalized == '%ключ_информация%':
        return _format_value(_context_value(context, 'key_info_html'), mode, html_ready=True)
    if normalized == '%ключ_история_операций%':
        return _format_value(_context_value(context, 'key_history_html'), mode, html_ready=True)
    if normalized == '%экран_данные%':
        return _format_value(_context_value(context, 'screen_data_html'), mode, html_ready=True)
    if normalized == '%замена_ключа_данные%':
        return _format_value(_context_value(context, 'key_replace_data_html'), mode, html_ready=True)
    if normalized == '%ключ_переименование_данные%':
        return _format_value(_context_value(context, 'key_rename_data_html'), mode, html_ready=True)

    if normalized == '%платеж_провайдер%':
        value = _context_value(context, 'payment_provider_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_provider_title'), mode)
    if normalized == '%платеж_ключ_строка%':
        return _format_value(_context_value(context, 'payment_key_line_html'), mode, html_ready=True)
    if normalized == '%платеж_тариф%':
        value = _context_value(context, 'payment_tariff_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_tariff_name', 'tariff_name'), mode)
    if normalized == '%платеж_сумма%':
        return _format_value(_context_value(context, 'payment_amount_text'), mode)
    if normalized == '%платеж_срок_тип%':
        return _format_value(_context_value(context, 'payment_term_label'), mode)
    if normalized == '%платеж_срок%':
        return _format_value(_context_value(context, 'payment_term_text'), mode)
    if normalized == '%платеж_ссылка%':
        if mode == 'html':
            link_html = _context_value(context, 'payment_link_html')
            if link_html is not None:
                return _format_value(link_html, mode, html_ready=True)
        return _format_value(_context_value(context, 'payment_url'), mode)
    if normalized == '%платеж_ссылка_url%':
        return _format_value(
            _context_value(context, 'payment_url'),
            mode,
            url_encode=(mode == 'url'),
        )
    if normalized == '%платеж_инструкция%':
        return _format_value(_context_value(context, 'payment_instruction_html'), mode, html_ready=True)
    if normalized == '%платеж_подсказка%':
        return _format_value(_context_value(context, 'payment_hint_text'), mode)
    if normalized == '%платеж_скидка_строка%':
        return _format_value(_context_value(context, 'payment_discount_line_html'), mode, html_ready=True)
    if normalized == '%платеж_баланс%':
        return _format_value(_context_value(context, 'payment_balance_text'), mode)
    if normalized == '%платеж_списание_баланса%':
        return _format_value(_context_value(context, 'payment_balance_deduct_text'), mode)
    if normalized == '%платеж_остаток_к_оплате%':
        return _format_value(_context_value(context, 'payment_remaining_text'), mode)
    if normalized == '%платеж_доплата_подсказка%':
        return _format_value(_context_value(context, 'payment_topup_hint_html'), mode, html_ready=True)
    if normalized == '%поддержка_заголовок%':
        value = _context_value(context, 'support_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_title'), mode)
    if normalized == '%поддержка_инструкция%':
        value = _context_value(context, 'support_instruction_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_instruction'), mode)
    if normalized == '%поддержка_статус_заголовок%':
        value = _context_value(context, 'support_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_status_title'), mode)
    if normalized == '%поддержка_статус_текст%':
        value = _context_value(context, 'support_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'support_status_body'), mode)
    if normalized == '%промо_статус_заголовок%':
        value = _context_value(context, 'promo_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'promo_status_title'), mode)
    if normalized == '%промо_статус_текст%':
        value = _context_value(context, 'promo_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'promo_status_body'), mode)
    if normalized == '%ключ_статус_заголовок%':
        value = _context_value(context, 'key_status_title_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'key_status_title'), mode)
    if normalized == '%ключ_статус_текст%':
        value = _context_value(context, 'key_status_body_html')
        if value is not None:
            return _format_value(value, mode, html_ready=True)
        return _format_value(_context_value(context, 'key_status_body'), mode)

    raw_key = _context_value(context, KEY_DELIVERY_RAW_CONTEXT_KEY, 'key_raw_value')
    if normalized == '%ключ_для_копирования%':
        if raw_key is None:
            return ''
        if mode == 'html':
            return f"<code>{escape_html(str(raw_key))}</code>"
        return _format_value(raw_key, mode)
    if normalized == '%ключ_ссылка%':
        return _format_value(raw_key, mode)
    if normalized == '%ключ_ссылка_url%':
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
    Подставляет canonical-плейсхолдеры конструктора страниц.

    Неизвестные плейсхолдеры остаются видимыми. Известные, но недоступные в
    текущем контексте значения заменяются пустой строкой.
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
