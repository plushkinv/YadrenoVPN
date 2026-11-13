"""Common DB helpers for page hooks/guards names."""
from __future__ import annotations

from localization import text as _tr

import json


def normalize_registry_names(value: list[str] | tuple[str, ...] | str | None) -> str:
    """Saves a list of hooks/guards names as a JSON array of strings."""
    if value is None:
        return '[]'
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            names = parsed if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            names = [value]
    else:
        names = list(value)
    clean = []
    for name in names:
        if not isinstance(name, str):
            raise ValueError(_tr('page_flow.normalize_registered_name.1'))
        normalized = name.strip()
        if normalized:
            clean.append(normalized)
    return json.dumps(clean, ensure_ascii=False)
