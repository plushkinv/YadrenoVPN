"""Validated process cache and renderer for non-page core user UI fragments."""
from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from bot.utils.text import escape_html
from database.requests import get_all_user_ui_texts
from database.user_ui_text_catalog import USER_UI_TEXT_CATALOG


_PLACEHOLDER_RE = re.compile(r"%([a-z][a-z0-9_]*)%")
_CACHE_LOCK = RLock()
_CACHE: Mapping[str, "CachedUserUIText"] = MappingProxyType({})


@dataclass(frozen=True)
class SafeHtml:
    """Marks a value that was already escaped or intentionally assembled as safe HTML."""

    value: str


@dataclass(frozen=True)
class CachedUserUIText:
    """Validated effective runtime value loaded from the database."""

    text_key: str
    text: str
    text_format: str
    description: str


def _template_placeholders(value: str) -> frozenset[str]:
    return frozenset(_PLACEHOLDER_RE.findall(value))


def _validate_row(row: Mapping[str, Any]) -> CachedUserUIText:
    text_key = str(row.get("text_key") or "")
    definition = USER_UI_TEXT_CATALOG.get(text_key)
    if definition is None:
        raise RuntimeError(f"Unknown core user UI text key in database: {text_key!r}")

    text_format = str(row.get("text_format") or "")
    if text_format != definition.text_format:
        raise RuntimeError(
            f"Invalid format for {text_key!r}: expected {definition.text_format!r}, "
            f"got {text_format!r}"
        )

    text = row.get("text_effective")
    if not isinstance(text, str):
        raise RuntimeError(f"Effective value for {text_key!r} must be a string")
    if text_format == "button" and (not text.strip() or "\n" in text or "\r" in text):
        raise RuntimeError(f"Button template {text_key!r} must be a non-empty single line")

    placeholders = _template_placeholders(text)
    if placeholders != definition.placeholders:
        raise RuntimeError(
            f"Invalid placeholders for {text_key!r}: expected "
            f"{sorted(definition.placeholders)}, got {sorted(placeholders)}"
        )

    return CachedUserUIText(
        text_key=text_key,
        text=text,
        text_format=text_format,
        description=str(row.get("description") or definition.description),
    )


def _build_cache(rows: list[dict[str, Any]]) -> Mapping[str, CachedUserUIText]:
    row_map = {str(row.get("text_key") or ""): row for row in rows}
    missing = sorted(set(USER_UI_TEXT_CATALOG) - set(row_map))
    extra = sorted(set(row_map) - set(USER_UI_TEXT_CATALOG))
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing=" + ", ".join(missing))
        if extra:
            parts.append("unexpected=" + ", ".join(extra))
        raise RuntimeError("Invalid user_ui_texts catalog: " + "; ".join(parts))
    return MappingProxyType({key: _validate_row(row_map[key]) for key in sorted(row_map)})


def load_user_ui_text_cache() -> int:
    """Loads and validates the complete catalog in one database query."""
    global _CACHE
    candidate = _build_cache(get_all_user_ui_texts())
    with _CACHE_LOCK:
        _CACHE = candidate
    return len(candidate)


def reload_user_ui_text_cache() -> int:
    """Atomically replaces the runtime catalog after a customization change."""
    return load_user_ui_text_cache()


def get_cached_user_ui_texts() -> Mapping[str, CachedUserUIText]:
    """Returns the immutable current cache for read-only runtime introspection."""
    with _CACHE_LOCK:
        return _CACHE


def get_ui_text(text_key: str) -> str:
    """Returns one effective template without touching the database."""
    with _CACHE_LOCK:
        entry = _CACHE.get(text_key)
    if entry is None:
        raise RuntimeError(
            f"User UI text cache is not loaded or key {text_key!r} is not registered"
        )
    return entry.text


def render_ui_text(text_key: str, /, **values: Any) -> str:
    """Renders one cached template with format-aware escaping and validation."""
    definition = USER_UI_TEXT_CATALOG.get(text_key)
    if definition is None:
        raise KeyError(f"Unknown user UI text key: {text_key}")
    provided = set(values)
    if provided != set(definition.placeholders):
        raise ValueError(
            f"Invalid values for {text_key!r}: expected {sorted(definition.placeholders)}, "
            f"got {sorted(provided)}"
        )

    with _CACHE_LOCK:
        entry = _CACHE.get(text_key)
    if entry is None:
        raise RuntimeError("User UI text cache has not been loaded")

    rendered_values: dict[str, str] = {}
    for name, value in values.items():
        if isinstance(value, SafeHtml):
            if entry.text_format != "html":
                raise ValueError(f"SafeHtml is not allowed for {entry.text_format} template {text_key!r}")
            rendered_values[name] = value.value
        else:
            raw = str(value)
            rendered_values[name] = escape_html(raw) if entry.text_format == "html" else raw

    rendered = _PLACEHOLDER_RE.sub(lambda match: rendered_values[match.group(1)], entry.text)
    if entry.text_format == "button":
        if not rendered.strip() or "\n" in rendered or "\r" in rendered:
            raise ValueError(f"Rendered button {text_key!r} must be a non-empty single line")
        if len(rendered) > 64:
            raise ValueError(f"Rendered button {text_key!r} exceeds Telegram's 64-character limit")
    return rendered


__all__ = [
    "CachedUserUIText",
    "SafeHtml",
    "get_cached_user_ui_texts",
    "get_ui_text",
    "load_user_ui_text_cache",
    "reload_user_ui_text_cache",
    "render_ui_text",
]
