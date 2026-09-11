"""Atomic typed customization writes using the existing storage formats."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .connection import get_db
from .db_pages import PAGE_CUSTOM_FIELDS


__all__ = ["CustomizationChanges", "mutate_customization_data"]


@dataclass
class CustomizationChanges:
    """Internal prepared writes; never accepted directly from tool arguments."""

    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    ui_texts: dict[str, str | None] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


def mutate_customization_data(
    transform: Callable[[dict[str, Any]], CustomizationChanges],
    *,
    page_keys: list[str] | None,
    include_pages: bool,
    include_ui_texts: bool,
    setting_keys: tuple[str, ...],
    dry_run: bool,
    backup: Callable[[], str],
    prepare_cache: Callable[[list[dict[str, Any]]], Any],
    publish_cache: Callable[[Any], None],
) -> dict[str, Any]:
    """Read, transform, validate and write one snapshot in a single transaction.

    The writer lock precedes the snapshot. The backup uses a separate reader
    before any write, so it captures the same committed state in WAL mode.
    Cache preparation can fail before commit; publication only assigns a
    previously validated immutable value. Callers own the cache publication lock.
    """
    candidate_cache = None
    with get_db() as conn:
        conn.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
        pages: dict[str, Any] = {}
        if include_pages:
            rows = conn.execute("SELECT * FROM pages ORDER BY page_key").fetchall()
            pages = {row["page_key"]: dict(row) for row in rows}
            if page_keys is not None:
                missing = sorted(set(page_keys) - pages.keys())
                if missing:
                    raise ValueError(f"unknown page_keys: {missing}")
                pages = {key: pages[key] for key in sorted(set(page_keys))}
        ui_rows = (
            [dict(row) for row in conn.execute("SELECT * FROM user_ui_texts ORDER BY text_key")]
            if include_ui_texts else []
        )
        settings = {}
        for key in setting_keys:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is not None:
                settings[key] = row["value"]
        before = {"pages": pages, "ui_texts": ui_rows, "settings": settings}
        changes = transform(before)
        normalized_pages = {}
        for key, patch in changes.pages.items():
            if key not in pages or not patch or set(patch) - PAGE_CUSTOM_FIELDS:
                raise ValueError("invalid internal page customization change")
            normalized_pages[key] = {
                name: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if name == "buttons_custom" and value is not None else value
                for name, value in patch.items()
            }
        ui_by_key = {row["text_key"]: row for row in ui_rows}
        if changes.ui_texts.keys() - ui_by_key.keys() or changes.settings.keys() - settings.keys():
            raise ValueError("customization writes outside the inspected snapshot")
        if changes.ui_texts:
            future_rows = []
            for row in ui_rows:
                future = dict(row)
                if row["text_key"] in changes.ui_texts:
                    future["text_custom"] = changes.ui_texts[row["text_key"]]
                custom = future["text_custom"]
                future["text_effective"] = custom if custom is not None else future["text_default"]
                future_rows.append(future)
            candidate_cache = prepare_cache(future_rows)
        count = len(normalized_pages) + len(changes.ui_texts) + len(changes.settings)
        result = dict(changes.result)
        result.update(status="ok", changed=bool(count) and not dry_run,
                      changed_records=count, dry_run=dry_run, backup_path=None)
        if count and not dry_run:
            result["backup_path"] = backup()
            for key, patch in normalized_pages.items():
                assignments = ", ".join(f"{name} = ?" for name in patch)
                conn.execute(
                    f"UPDATE pages SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE page_key = ?",
                    (*patch.values(), key),
                )
                after = conn.execute("SELECT * FROM pages WHERE page_key = ?", (key,)).fetchone()
                if after is None or any(after[name] != value for name, value in patch.items()):
                    raise RuntimeError(f"page read-back mismatch: {key}")
            for key, value in changes.ui_texts.items():
                conn.execute(
                    "UPDATE user_ui_texts SET text_custom = ?, updated_at = CURRENT_TIMESTAMP WHERE text_key = ?",
                    (value, key),
                )
                after = conn.execute("SELECT text_custom FROM user_ui_texts WHERE text_key = ?", (key,)).fetchone()
                if after is None or after[0] != value:
                    raise RuntimeError(f"UI text read-back mismatch: {key}")
            for key, value in changes.settings.items():
                conn.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
                after = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                if after is None or after[0] != value:
                    raise RuntimeError(f"setting read-back mismatch: {key}")
    if candidate_cache is not None and not dry_run:
        publish_cache(candidate_cache)
    return result
