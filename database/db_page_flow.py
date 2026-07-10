"""Общие DB-хелперы для имён page hooks/guards."""
from __future__ import annotations

import json


def normalize_registry_names(value: list[str] | tuple[str, ...] | str | None) -> str:
    """Сохраняет список имён hooks/guards как JSON-массив строк."""
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
            raise ValueError('registry name должен быть строкой')
        normalized = name.strip()
        if normalized:
            clean.append(normalized)
    return json.dumps(clean, ensure_ascii=False)
