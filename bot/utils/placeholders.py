"""Утилиты для подстановки плейсхолдеров в редактируемые тексты."""
from __future__ import annotations

import re
from typing import Any, Mapping


_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')


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
