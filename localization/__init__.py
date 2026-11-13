"""Offline installation-local catalogs. User Telegram copy is seeded into SQLite."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

CATALOG_ROOT = Path(__file__).resolve().parent.parent / 'locales'
LANGUAGES = MappingProxyType(json.loads((CATALOG_ROOT / 'languages.json').read_text(encoding='utf-8')))
DEFAULT_LANGUAGE = 'ru'


def normalize_language(value: str) -> str:
    """Accept only the release's explicit language codes, including both Chinese scripts."""
    if not isinstance(value, str) or value not in LANGUAGES:
        raise ValueError('Unsupported installation language: ' + str(value))
    return value


def language() -> str:
    """Resolve the immutable installation setting without changing storage."""
    from database.db_language import get_installation_language
    return get_installation_language()


@lru_cache(maxsize=64)
def catalog(namespace: str, code: str):
    """Read a complete shipped catalog once; missing translations are release errors."""
    normalize_language(code)
    if namespace not in {'admin', 'seed', 'installer', 'legacy'}:
        raise ValueError('Unknown translation namespace')
    path = CATALOG_ROOT / code / (namespace + '.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid translation catalog: ' + str(path))
    return MappingProxyType(value)


def text(key: str, *, locale: str | None = None, **values) -> str:
    """Render core-owned copy; caller retains HTML escaping and output-format ownership."""
    code = normalize_language(locale) if locale is not None else language()
    template = catalog('admin', code)[key]
    if isinstance(template, dict):
        from babel import Locale
        category = Locale.parse(code, sep='-').plural_form(values['count'])
        template = template.get(category, template['other'])
    if not isinstance(template, str):
        raise ValueError('Invalid message: ' + key)
    return template.format_map(values) if values else template


def seed_text(key: str, code: str) -> str:
    """Return an initialization/reset default, never an end-user runtime fallback."""
    value = catalog('seed', normalize_language(code))[key]
    if not isinstance(value, str):
        raise ValueError('Invalid seed text: ' + key)
    return value


def legacy_text(key: str) -> str:
    """Immutable Russian bytes used by the already released migration history."""
    return catalog('legacy', DEFAULT_LANGUAGE)[key]
