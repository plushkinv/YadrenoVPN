"""Shared parsing and comparison helpers for official 3X-UI versions."""

from __future__ import annotations

import re
from typing import Any, Sequence


MINIMUM_SUPPORTED_3X_UI_VERSION = (3, 3, 0)
_VERSION_PART_RE = re.compile(r"(\d+)")


def parse_panel_version(value: Any) -> tuple[int, ...]:
    """Return numeric version parts accepted from the official panel API."""
    if value is None:
        return ()

    text = str(value).strip().lstrip("vV")
    if not text:
        return ()

    parts: list[int] = []
    for part in text.split("."):
        match = _VERSION_PART_RE.match(part)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def panel_version_at_least(
    value: Any,
    minimum: Sequence[int] = MINIMUM_SUPPORTED_3X_UI_VERSION,
) -> bool:
    """Return whether a detected panel version satisfies the minimum."""
    parts = parse_panel_version(value)
    required = tuple(int(part) for part in minimum)
    if not parts or not required:
        return False
    padded = parts + (0,) * max(0, len(required) - len(parts))
    return padded[:len(required)] >= required
