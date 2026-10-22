"""Stored button merging shared by page rendering and read-only projections."""
import json
from typing import Any, Dict, List, Optional

from bot.utils.payment_provider_buttons import prepare_payment_provider_buttons


def _parse_buttons_json(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Secure parsing of JSON array of buttons."""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError):
        return []


def _valid_button_id(raw_button_id: Any) -> bool:
    return isinstance(raw_button_id, str) and bool(raw_button_id.strip())


def _button_position_value(raw_value: Any) -> Optional[int]:
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        return None
    return raw_value


def _button_sort_key(button: Dict[str, Any]) -> tuple[int, int]:
    row = _button_position_value(button.get('row', 0))
    col = _button_position_value(button.get('col', 0))
    return (
        row if row is not None else 10_000,
        col if col is not None else 10_000,
    )


def _merge_buttons_by_id(
    buttons_default_json: str,
    buttons_custom_json: Optional[str],
    *,
    page_key: Optional[str] = None,
) -> List[Dict]:
    """
    Merges two arrays of buttons based on the id field.

    Algorithm:
    1. Parse stored buttons and prepare registry-derived defaults for the page.
    2. If buttons_custom is empty (NULL) - return the prepared defaults.
    3. For each button from default: if in custom there is a button with the same id →
       take the custom version (custom priority).
    4. We add buttons from custom that are not in default → added by the admin.
    5. Sort by (row, col).
    """
    defaults = prepare_payment_provider_buttons(page_key, _parse_buttons_json(buttons_default_json))
    customs = _parse_buttons_json(buttons_custom_json)

    if not customs:
        return defaults

    # Indexing custom buttons by id
    custom_map = {btn.get('id'): btn for btn in customs if _valid_button_id(btn.get('id'))}
    used_custom_ids = set()

    merged = []
    for btn in defaults:
        btn_id = btn.get('id')
        if btn_id and btn_id in custom_map:
            # Custom version is priority
            merged.append(custom_map[btn_id])
            used_custom_ids.add(btn_id)
        else:
            # There is no custom one - we take the default one
            merged.append(btn)

    # Buttons added by the admin (not in default)
    for btn in customs:
        btn_id = btn.get('id')
        if _valid_button_id(btn_id) and btn_id not in used_custom_ids:
            merged.append(btn)

    # Sort by (row, col)
    merged.sort(key=_button_sort_key)

    return merged


def _merge_buttons_by_id_with_source(
    buttons_default_json: str,
    buttons_custom_json: Optional[str],
    *,
    page_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Merges buttons for /yaa snapshot and marks the source of each effective button.

    If a button has a custom version, default is not duplicated: to the agent for editing
    you need the current edited version and an understanding of where it came from.
    """
    defaults = prepare_payment_provider_buttons(page_key, _parse_buttons_json(buttons_default_json))
    customs = _parse_buttons_json(buttons_custom_json)

    custom_map = {btn.get('id'): btn for btn in customs if _valid_button_id(btn.get('id'))}
    used_custom_ids = set()
    merged: List[Dict[str, Any]] = []

    for btn in defaults:
        btn_id = btn.get('id')
        if btn_id and btn_id in custom_map:
            item = dict(custom_map[btn_id])
            item['source'] = 'custom'
            merged.append(item)
            used_custom_ids.add(btn_id)
            continue

        item = dict(btn)
        item['source'] = 'default'
        merged.append(item)

    for btn in customs:
        btn_id = btn.get('id')
        if _valid_button_id(btn_id) and btn_id not in used_custom_ids:
            item = dict(btn)
            item['source'] = 'custom'
            merged.append(item)

    merged.sort(key=_button_sort_key)
    return merged


