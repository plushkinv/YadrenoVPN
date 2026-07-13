"""Assembling an editable “My Keys” screen."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from bot.utils.datetime_format import format_date_for_display
from bot.utils.placeholders import apply_placeholder_replacements
from bot.utils.text import escape_html

MY_KEYS_ITEM_TEMPLATE_SETTING = 'my_keys_item_template'
DEFAULT_MY_KEYS_ITEM_TEMPLATE = (
    "%ключ_статус%<b>%ключ_имя%</b> - %ключ_трафик% - до %ключ_дата_окончания%\n"
    "     📍%ключ_сервер% - %ключ_инбаунд% (%ключ_протокол%)"
)


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
    expires = format_date_for_display(key.get('expires_at'))
    server = key.get('server_name') or 'Не выбран'
    display_name = key.get('display_name') or f"Ключ #{key.get('id', '')}"

    replacements = {
        '%key_status%': status,
        '%key_name%': escape_html(str(display_name)),
        '%key_traffic%': escape_html(str(traffic_text)),
        '%key_expires_at%': escape_html(str(expires)),
        '%key_server%': escape_html(str(server)),
        '%key_inbound%': escape_html(str(inbound_name)),
        '%key_protocol%': escape_html(str(protocol)),
        '%key_id%': escape_html(str(key.get('id', ''))),
        '%ключ_статус%': status,
        '%ключ_имя%': escape_html(str(display_name)),
        '%ключ_трафик%': escape_html(str(traffic_text)),
        '%ключ_дата_окончания%': escape_html(str(expires)),
        '%ключ_сервер%': escape_html(str(server)),
        '%ключ_инбаунд%': escape_html(str(inbound_name)),
        '%ключ_протокол%': escape_html(str(protocol)),
        '%ключ_id%': escape_html(str(key.get('id', ''))),
    }

    return apply_placeholder_replacements(template, replacements)


def build_my_keys_list_text(items: Iterable[str]) -> str:
    """Collects the elements of a list of keys with an empty string between them."""
    return '\n\n'.join(item.rstrip() for item in items if item is not None)
