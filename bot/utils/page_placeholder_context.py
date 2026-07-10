"""Before-render наполнение context для canonical-плейсхолдеров страниц."""
from __future__ import annotations

import re
from typing import Any, Mapping

from bot.utils.page_dynamic_data import (
    build_my_keys_context_values,
    build_referral_context_values,
    build_support_context_values,
    build_tariff_text,
    build_user_profile_context_values,
)


_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')

TARIFF_PLACEHOLDERS = {'%тарифы%', '%без_тарифов%'}
REFERRAL_PLACEHOLDERS = {
    '%реферальная_ссылка%',
    '%реферальная_ссылка_url%',
    '%реферальная_статистика%',
}
SUPPORT_PLACEHOLDERS = {'%поддержка_заголовок%', '%поддержка_инструкция%'}
MY_KEYS_PLACEHOLDERS = {'%список_ключей%'}
USER_PROFILE_PLACEHOLDERS = {
    '%профиль%',
    '%баланс%',
    '%пользователь_имя%',
    '%пользователь_username%',
    '%пользователь_дата_регистрации%',
    '%ключи_сводка%',
    '%ключи_всего%',
    '%ключи_активных%',
    '%ключи_истекших%',
}


def _normalize_placeholders(values: Mapping[str, Any] | None) -> set[str]:
    if not values:
        return set()
    return {key.casefold() for key in values if isinstance(key, str)}


def _collect_page_placeholder_names(page_data: Mapping[str, Any]) -> set[str]:
    """Собирает плейсхолдеры из текста страницы и шаблонизируемых частей кнопок."""
    sources: list[str] = []
    text = page_data.get('text')
    if isinstance(text, str):
        sources.append(text)
    for button in page_data.get('buttons') or []:
        if not isinstance(button, Mapping):
            continue
        label = button.get('label')
        if isinstance(label, str):
            sources.append(label)
        if button.get('action_type') == 'url':
            action_value = button.get('action_value')
            if isinstance(action_value, str):
                sources.append(action_value)

    result: set[str] = set()
    for source in sources:
        result.update(match.group(0).casefold() for match in _PLACEHOLDER_RE.finditer(source))
    return result


def _context_int(context: Mapping[str, Any], key: str) -> int | None:
    value = context.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _context_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    return value if isinstance(value, str) else ''


def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise ValueError("context должен быть mapping")
    return dict(context)


async def enrich_page_placeholder_context(
    page_key: str,
    page_data: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    text_replacements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Добавляет в context данные для общих плейсхолдеров, если они нужны странице.

    Значения из явных `text_replacements` считаются более точными и не
    пересчитываются автоматически.
    """
    enriched = enrich_page_placeholder_context_sync(
        page_key,
        page_data,
        context,
        text_replacements,
    )
    placeholders = _collect_page_placeholder_names(page_data)
    explicit = _normalize_placeholders(text_replacements)

    if (MY_KEYS_PLACEHOLDERS & placeholders) - explicit and 'keys_list_html' not in enriched:
        for key, value in (await build_my_keys_context_values(_context_int(enriched, 'telegram_id'))).items():
            enriched.setdefault(key, value)

    return enriched


def enrich_page_placeholder_context_sync(
    page_key: str,
    page_data: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    text_replacements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Sync-safe часть before-render enrichment для текстовых адаптеров страниц.

    Не наполняет `%список_ключей%`, потому что этот блок может требовать
    async-запросов к VPN-панели. Для обычного `render_page()` его добавляет
    async-обёртка `enrich_page_placeholder_context()`.
    """
    enriched: dict[str, Any] = _normalize_context(context)
    placeholders = _collect_page_placeholder_names(page_data)
    explicit = _normalize_placeholders(text_replacements)

    if (TARIFF_PLACEHOLDERS & placeholders) - explicit and 'tariffs_html' not in enriched:
        enriched['tariffs_html'] = build_tariff_text()

    if (REFERRAL_PLACEHOLDERS & placeholders) - explicit:
        for key, value in build_referral_context_values(
            _context_int(enriched, 'telegram_id'),
            _context_text(enriched, 'bot_username'),
        ).items():
            enriched.setdefault(key, value)

    if (SUPPORT_PLACEHOLDERS & placeholders) - explicit:
        thread_id = _context_int(enriched, 'support_thread_id') or _context_int(enriched, 'thread_id')
        for key, value in build_support_context_values(thread_id=thread_id).items():
            enriched.setdefault(key, value)

    if (USER_PROFILE_PLACEHOLDERS & placeholders) - explicit:
        for key, value in build_user_profile_context_values(_context_int(enriched, 'telegram_id')).items():
            enriched.setdefault(key, value)

    return enriched
