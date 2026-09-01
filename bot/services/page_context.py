"""
Memory of the last user page seen by the administrator.

Needed for the /yaa command: the administrator can call it directly from the user
pages, and the agent receives the exact context and after changing the screen you can
redraw without any questions asked.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, Message

@dataclass
class PageContext:
    """Latest render of an editable custom page."""

    page_key: str
    message: Message
    visibility: Optional[Dict[str, bool]] = None
    context: Optional[Dict[str, Any]] = None
    text_replacements: Optional[Dict[str, Any]] = None
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None
    route_key: Optional[str] = None
    base_visibility: Optional[Dict[str, bool]] = None
    base_context: Optional[Dict[str, Any]] = None
    base_text_replacements: Optional[Dict[str, Any]] = None
    base_prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None
    base_append_buttons: Optional[List[List[InlineKeyboardButton]]] = None

    @property
    def effective_visibility(self) -> Optional[Dict[str, bool]]:
        """Compatibility-safe explicit name for the rendered visibility snapshot."""
        return self.visibility

    @property
    def effective_context(self) -> Optional[Dict[str, Any]]:
        """Compatibility-safe explicit name for the rendered context snapshot."""
        return self.context

    @property
    def effective_text_replacements(self) -> Optional[Dict[str, Any]]:
        """Compatibility-safe explicit name for rendered text replacements."""
        return self.text_replacements

    @property
    def effective_prepend_buttons(self) -> Optional[List[List[InlineKeyboardButton]]]:
        """Compatibility-safe explicit name for rendered prepend rows."""
        return self.prepend_buttons

    @property
    def effective_append_buttons(self) -> Optional[List[List[InlineKeyboardButton]]]:
        """Compatibility-safe explicit name for rendered append rows."""
        return self.append_buttons


_contexts: dict[int, PageContext] = {}


def _copy_optional_mapping(value: Optional[Mapping[str, Any]], field_name: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} должен быть mapping или None")
    return dict(value)


def _copy_visibility(value: Optional[Mapping[str, bool]]) -> Optional[Dict[str, bool]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("visibility должен быть mapping или None")
    visibility: Dict[str, bool] = {}
    for button_id, visible in value.items():
        if not isinstance(button_id, str):
            raise ValueError("visibility button_id должен быть строкой")
        if not isinstance(visible, bool):
            raise ValueError("visibility values должны быть bool")
        visibility[button_id] = visible
    return visibility


def _copy_button_rows(
    rows: Optional[List[List[InlineKeyboardButton]]],
    field_name: str,
) -> Optional[List[List[InlineKeyboardButton]]]:
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise ValueError(f"{field_name} должен быть списком рядов кнопок или None")
    copied_rows: List[List[InlineKeyboardButton]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError(f"{field_name} должен содержать только ряды кнопок")
        copied_row: List[InlineKeyboardButton] = []
        for button in row:
            if not isinstance(button, InlineKeyboardButton):
                raise ValueError(f"{field_name} должен содержать только InlineKeyboardButton")
            copied_row.append(button)
        copied_rows.append(copied_row)
    return copied_rows


def is_supported_yaa_page_key(page_key: str) -> bool:
    """Checks whether the page for the /yaa context command can be remembered."""
    if not isinstance(page_key, str):
        raise ValueError("page_key должен быть строкой")
    from database.db_pages import resolve_page_row
    from database.requests import get_page

    return resolve_page_row(
        page_key,
        get_page(page_key),
        warn_unknown=False,
    ) is not None


def remember_page_context(
    telegram_id: int,
    page_key: str,
    message: Message,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict[str, Any]] = None,
    text_replacements: Optional[Dict[str, Any]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    route_key: Optional[str] = None,
    base_visibility: Optional[Dict[str, bool]] = None,
    base_context: Optional[Dict[str, Any]] = None,
    base_text_replacements: Optional[Dict[str, Any]] = None,
    base_prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    base_append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    effective_visibility: Optional[Dict[str, bool]] = None,
    effective_context: Optional[Dict[str, Any]] = None,
    effective_text_replacements: Optional[Dict[str, Any]] = None,
    effective_prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    effective_append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    stored_page: bool = True,
) -> None:
    """Remember a successfully delivered stored page without a local whitelist."""
    if not isinstance(page_key, str) or not page_key.strip():
        raise ValueError('page_key must be a non-empty string')
    page_key = page_key.strip()
    if not isinstance(stored_page, bool):
        raise ValueError('stored_page must be bool')
    if not stored_page:
        return
    _contexts[telegram_id] = PageContext(
        page_key=page_key,
        message=message,
        visibility=_copy_visibility(
            effective_visibility if effective_visibility is not None else visibility
        ),
        context=_copy_optional_mapping(
            effective_context if effective_context is not None else context,
            'effective_context',
        ),
        text_replacements=_copy_optional_mapping(
            effective_text_replacements
            if effective_text_replacements is not None
            else text_replacements,
            'effective_text_replacements',
        ),
        prepend_buttons=_copy_button_rows(
            effective_prepend_buttons
            if effective_prepend_buttons is not None
            else prepend_buttons,
            'effective_prepend_buttons',
        ),
        append_buttons=_copy_button_rows(
            effective_append_buttons
            if effective_append_buttons is not None
            else append_buttons,
            'effective_append_buttons',
        ),
        route_key=route_key,
        base_visibility=_copy_visibility(
            base_visibility if base_visibility is not None else visibility
        ),
        base_context=_copy_optional_mapping(
            base_context if base_context is not None else context,
            'base_context',
        ),
        base_text_replacements=_copy_optional_mapping(
            base_text_replacements
            if base_text_replacements is not None
            else text_replacements,
            'base_text_replacements',
        ),
        base_prepend_buttons=_copy_button_rows(
            base_prepend_buttons
            if base_prepend_buttons is not None
            else prepend_buttons,
            'base_prepend_buttons',
        ),
        base_append_buttons=_copy_button_rows(
            base_append_buttons
            if base_append_buttons is not None
            else append_buttons,
            'base_append_buttons',
        ),
    )


def get_page_context(telegram_id: int) -> Optional[PageContext]:
    """Returns the last admin page for /yaa."""
    return _contexts.get(telegram_id)


def clear_page_context(telegram_id: int) -> None:
    """Clears the saved admin page context."""
    _contexts.pop(telegram_id, None)
