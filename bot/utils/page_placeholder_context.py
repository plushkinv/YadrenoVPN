"""Before-render context filling for canonical placeholders of pages."""
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

TARIFF_PLACEHOLDERS = {'%tariffs%', '%тарифы%', '%no_tariffs%', '%без_тарифов%'}
REFERRAL_PLACEHOLDERS = {
    '%referral_link%',
    '%referral_link_url%',
    '%referral_stats%',
    '%реферальная_ссылка%',
    '%реферальная_ссылка_url%',
    '%реферальная_статистика%',
}
SUPPORT_PLACEHOLDERS = {
    '%support_title%',
    '%support_instruction%',
    '%поддержка_заголовок%',
    '%поддержка_инструкция%',
}
MY_KEYS_PLACEHOLDERS = {'%keys_list%', '%список_ключей%'}
USER_PROFILE_PLACEHOLDERS = {
    '%profile%',
    '%user_balance%',
    '%user_name%',
    '%user_display_name%',
    '%user_username%',
    '%user_registered_at%',
    '%keys_summary%',
    '%keys_total%',
    '%keys_active%',
    '%keys_expired%',
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
    """Collects placeholders from page text and templated parts of buttons."""
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


def _render_legacy_fragment(template: str, context: Mapping[str, Any]) -> str:
    """Renders a compatibility fragment whose wording still comes from pages."""
    from bot.utils.placeholders import apply_page_placeholders

    return apply_page_placeholders(template, context=context, mode='html')


def _without_separator_line(value: str) -> str:
    lines = value.splitlines()
    if lines and lines[0] and set(lines[0]) <= {'━', '─', '-', ' '}:
        lines = lines[1:]
    return '\n'.join(lines).lstrip('\n')


def _add_legacy_composite_page_context(
    page_key: str,
    page_data: Mapping[str, Any],
    context: dict[str, Any],
) -> None:
    """Keeps pre-v81 custom placeholders working without changing stored custom text."""
    custom = page_data.get('_text_custom')
    default = page_data.get('_text_default')
    if not isinstance(custom, str) or not isinstance(default, str):
        return

    custom_folded = custom.casefold()
    blocks = default.split('\n\n')
    if page_key == 'main' and '%тарифы%' in custom_folded:
        tariff_lines = default.splitlines()
        for index, line in enumerate(tariff_lines):
            if '%tariffs%' not in line.casefold():
                continue
            heading = tariff_lines[index - 1] if index > 0 else ''
            rows = build_tariff_text()
            context['tariffs_html'] = '\n'.join(
                part for part in (heading, rows) if part
            )
            break
        return

    if page_key == 'custom_profile':
        if '%profile%' in custom_folded or '%профиль%' in custom_folded:
            profile_template = blocks[1] if len(blocks) > 1 else default
            context['user_profile_html'] = _render_legacy_fragment(profile_template, context)
        if '%keys_summary%' in custom_folded or '%ключи_сводка%' in custom_folded:
            summary_template = '\n\n'.join(blocks[2:]) if len(blocks) > 2 else ''
            context['keys_summary_html'] = _render_legacy_fragment(
                _without_separator_line(summary_template),
                context,
            )
        return

    if page_key == 'key_details' and (
        '%key_info%' in custom_folded or '%ключ_информация%' in custom_folded
    ):
        history_rows = context.get('key_history_html', '')
        context['key_info_html'] = _render_legacy_fragment(
            blocks[0] if blocks else default,
            context,
        )
        context['key_history_html'] = history_rows
        context['key_history_html'] = _render_legacy_fragment(
            '\n\n'.join(blocks[1:]),
            context,
        )
        return

    fragment_specs = {
        'key_replace_server_select': ('screen_data_html', 1, -1),
        'key_replace_inbound_select': ('screen_data_html', 1, -1),
        'new_key_server_select': ('screen_data_html', 1, None),
        'new_key_inbound_select': ('screen_data_html', 1, -1),
        'key_replace_confirm': ('key_replace_data_html', 1, -1),
        'key_rename_prompt': ('key_rename_data_html', 1, 2),
    }
    spec = fragment_specs.get(page_key)
    if not spec:
        return
    context_key, start, stop = spec
    marker_names = {
        'screen_data_html': ('%screen_data%', '%экран_данные%'),
        'key_replace_data_html': ('%key_replace_data%', '%замена_ключа_данные%'),
        'key_rename_data_html': ('%key_rename_data%', '%ключ_переименование_данные%'),
    }[context_key]
    if not any(marker in custom_folded for marker in marker_names):
        return
    template = '\n\n'.join(blocks[start:stop])
    context[context_key] = _render_legacy_fragment(template, context)


async def enrich_page_placeholder_context(
    page_key: str,
    page_data: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    text_replacements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Adds data for general placeholders to the context if the page needs them.

    Values from explicit `text_replacements` are considered more accurate and not
    are recalculated automatically.
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
    Sync-safe part of before-render enrichment for text page adapters.

    Does not fill the key-list placeholder because this block may require
    async requests to the VPN panel. For regular `render_page()` it adds
    async wrapper `enrich_page_placeholder_context()`.
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

    _add_legacy_composite_page_context(page_key, page_data, enriched)

    return enriched
