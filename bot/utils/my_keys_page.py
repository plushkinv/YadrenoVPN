"""Assembling an editable “My Keys” screen."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from bot.utils.key_pages import build_key_page_context
from bot.utils.placeholders import apply_page_placeholders

MY_KEYS_ITEM_TEMPLATE_SETTING = 'my_keys_item_template'


def build_my_keys_item_text(
    key: Dict[str, Any],
    *,
    template: str,
    status: str,
    traffic_text: str,
    inbound_name: str,
    protocol: str,
) -> str:
    """Substitutes the data of one key into a hidden list string template."""
    context = build_key_page_context(
        key,
        status=status,
        traffic=traffic_text,
        inbound=inbound_name,
        protocol=protocol,
    )
    return apply_page_placeholders(template, context=context)


def build_my_keys_list_text(items: Iterable[str]) -> str:
    """Collects the elements of a list of keys with an empty string between them."""
    return '\n\n'.join(item.rstrip() for item in items if item is not None)
