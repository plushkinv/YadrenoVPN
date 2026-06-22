"""Сборка редактируемого экрана «Мои ключи»."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from bot.utils.datetime_format import format_date_for_display
from bot.utils.placeholders import apply_placeholder_replacements
from bot.utils.text import escape_html

MY_KEYS_ITEM_TEMPLATE_SETTING = 'my_keys_item_template'
DEFAULT_MY_KEYS_ITEM_TEMPLATE = (
    "%статус%<b>%имяключа%</b> - %трафик% - до %датаокончания%\n"
    "     📍%сервер% - %инбаунд% (%протокол%)"
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
    """Подставляет данные одного ключа в скрытый шаблон строки списка."""
    expires = format_date_for_display(key.get('expires_at'))
    server = key.get('server_name') or 'Не выбран'
    display_name = key.get('display_name') or f"Ключ #{key.get('id', '')}"

    replacements = {
        '%статус%': status,
        '%имяключа%': escape_html(str(display_name)),
        '%трафик%': escape_html(str(traffic_text)),
        '%датаокончания%': escape_html(str(expires)),
        '%сервер%': escape_html(str(server)),
        '%инбаунд%': escape_html(str(inbound_name)),
        '%протокол%': escape_html(str(protocol)),
        '%id%': escape_html(str(key.get('id', ''))),
    }

    return apply_placeholder_replacements(template, replacements)


def build_my_keys_list_text(items: Iterable[str]) -> str:
    """Собирает элементы списка ключей с пустой строкой между ними."""
    return '\n\n'.join(item.rstrip() for item in items if item is not None)
