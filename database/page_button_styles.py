"""Shared data contract for page-owned collection button colors."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


BUTTON_COLORS = frozenset({'secondary', 'primary', 'success', 'danger'})
ITEM_COLOR_COLLECTIONS = frozenset({'btn_tariff_items', 'btn_server_items'})
_PERSONAL_COLLECTIONS = frozenset({
    'btn_key_items', 'btn_key_device_items', 'btn_subscription_host_items',
})


def is_collection_item_id(value: Any) -> bool:
    """Accept the canonical string form of a positive SQLite entity id."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 19
        and value.isascii()
        and value.isdecimal()
        and not value.startswith('0')
        and int(value) <= 9223372036854775807
    )


def supports_item_colors(button: Mapping[str, Any]) -> bool:
    """Only installation-owned tariff/server collections have item colors."""
    return (
        button.get('action_type') == 'system_collection'
        and isinstance(button.get('id'), str)
        and button['id'] in ITEM_COLOR_COLLECTIONS
    )


def validate_page_item_colors(buttons: Any) -> None:
    """Validate only the new field; preserve unrelated legacy button shapes."""
    if not isinstance(buttons, list):
        return
    for button in buttons:
        if not isinstance(button, Mapping) or 'item_colors' not in button:
            continue
        if not supports_item_colors(button):
            raise ValueError(
                'item_colors is supported only for system_collection '
                'btn_tariff_items and btn_server_items; personal key, device '
                'and subscription-host lists use the common color only'
            )
        colors = button['item_colors']
        if not isinstance(colors, dict):
            raise TypeError('item_colors must be an object; use {} to inherit the common color')
        for item_id, color in colors.items():
            if not is_collection_item_id(item_id):
                raise ValueError('item_colors keys must be canonical positive SQLite id strings')
            if not isinstance(color, str) or color not in BUTTON_COLORS:
                raise ValueError('item_colors values must be secondary, primary, success or danger')


def collection_item_color_override(button: Mapping[str, Any], item_id: Any) -> str | None:
    """Ignore unsupported or malformed stored overrides without hiding items."""
    if not supports_item_colors(button) or not is_collection_item_id(item_id):
        return None
    colors = button.get('item_colors')
    if not isinstance(colors, dict):
        return None
    color = colors.get(item_id)
    return color if isinstance(color, str) and color in BUTTON_COLORS else None


def resolve_collection_item_color(button: Mapping[str, Any], item_id: Any) -> str:
    """Resolve item override, template color, then ordinary Telegram style."""
    override = collection_item_color_override(button, item_id)
    if override is not None:
        return override
    color = button.get('color')
    return color if isinstance(color, str) and color in BUTTON_COLORS else 'secondary'


def describe_collection_colors(buttons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Publish support facts without enumerating personal runtime collections."""
    result: dict[str, dict[str, Any]] = {}
    for button in buttons:
        button_id = button.get('id')
        if button.get('action_type') != 'system_collection' or not isinstance(button_id, str):
            continue
        supported = supports_item_colors(button)
        info: dict[str, Any] = {'item_colors_supported': supported}
        if not supported:
            info['reason'] = (
                'user_specific_items' if button_id in _PERSONAL_COLLECTIONS
                else 'unsupported_collection'
            )
        result[button_id] = info
    return result
