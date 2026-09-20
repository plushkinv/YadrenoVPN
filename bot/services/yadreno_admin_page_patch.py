"""Addressed edits over existing full-button override objects."""
from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from bot.services.yadreno_admin_page_validation import (
    _PAGE_BUTTON_FIELDS, _PAGE_BUTTON_COLORS, _PAGE_BUTTON_ACTION_TYPES,
    _PAGE_CREATE_MAX_BUTTONS, _PAGE_CREATE_MAX_BUTTONS_PER_ROW,
    _normalize_page_create_buttons, _validate_page_button_action,
)
from bot.utils.page_renderer import _merge_buttons_by_id, _page_image_value, _page_media_type_value
from bot.utils.payment_provider_buttons import prepare_payment_provider_buttons
from bot.utils.text import TELEGRAM_CAPTION_LIMIT, TELEGRAM_TEXT_LIMIT, html_to_plain_text
from database.page_button_styles import validate_page_item_colors


PAGE_CHANGE_FIELDS = {
    "text.set": {"type", "value"}, "text.reset": {"type"},
    "media.set": {"type", "image", "media_type"},
    "media.remove": {"type"}, "media.reset": {"type"},
    "button.update": {"type", "id", "patch"},
    "button.add": {"type", "button"},
    "button.remove": {"type", "id"}, "button.reset": {"type", "id"},
}


def require_label(value: Any) -> None:
    """Validate a changed stored button label without normalizing its text."""
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("button label must contain 1 to 64 characters")
    if any(character in value for character in ("\n", "\r", "<", ">")):
        raise ValueError("button label must be plain single-line text")


def require_emoji_id(value: Any) -> None:
    """Accept only Telegram's decimal string identifier or explicit clearing."""
    if value is not None and (
        not isinstance(value, str) or not value.isascii() or not value.isdecimal()
    ):
        raise ValueError("icon_custom_emoji_id must be a numeric string or null")


def _buttons(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    value = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("stored buttons must be an array of objects")
    ids = [item.get("id") for item in value]
    if any(not isinstance(item, str) or not item.strip() for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("stored buttons have missing or duplicate IDs")
    return value


class PagePatch:
    """Prepare one page's edits without rewriting untouched saved fields."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.before = copy.deepcopy(row)
        self.row = copy.deepcopy(row)
        self.key = row["page_key"]
        self.defaults = prepare_payment_provider_buttons(self.key, _buttons(row["buttons_default"]))
        self.customs = _buttons(row.get("buttons_custom"))
        self.original_customs = copy.deepcopy(self.customs)
        self.original_buttons = self.buttons()
        self.touched: dict[str, set[str]] = {}
        self.layout_changed = False
        self.content_changed = False

    def buttons(self) -> list[dict[str, Any]]:
        """Resolve using the renderer's existing whole-object inheritance."""
        ordered = _merge_buttons_by_id(
            json.dumps(self.defaults, ensure_ascii=False),
            json.dumps(self.customs, ensure_ascii=False) if self.customs else None,
        )
        # Keep the tentative field types intact until final validation. JSON
        # encoding for renderer ordering must not coerce an invalid map key.
        originals = {button['id']: button for button in (*self.defaults, *self.customs)}
        return [copy.deepcopy(originals[button['id']]) for button in ordered]

    def text(self) -> str:
        return self.row.get("text_custom") or self.row.get("text_default") or ""

    def _override(self, button_id: str, value: dict[str, Any] | None) -> None:
        for index, button in enumerate(self.customs):
            if button["id"] == button_id:
                if value is None:
                    self.customs.pop(index)
                else:
                    self.customs[index] = copy.deepcopy(value)
                return
        if value is not None:
            self.customs.append(copy.deepcopy(value))

    def update_button(self, button_id: str, patch: dict[str, Any]) -> None:
        if not isinstance(patch, dict) or not patch or set(patch) - (_PAGE_BUTTON_FIELDS - {"id"}):
            raise ValueError("button.update requires a non-empty allowlisted patch without id")
        current = next((item for item in self.buttons() if item["id"] == button_id), None)
        if current is None:
            raise ValueError(f"unknown button id: {button_id}")
        updated = {**current, **copy.deepcopy(patch)}
        self.touched.setdefault(button_id, set()).update(patch)
        if updated != current:
            self._override(button_id, updated)
            self.layout_changed |= bool(set(patch) & {"row", "col", "is_hidden"})

    def apply(self, changes: Any) -> None:
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a non-empty array")
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get("type"), str):
                raise ValueError("each page change requires a type")
            kind = change["type"]
            if kind not in PAGE_CHANGE_FIELDS or set(change) != PAGE_CHANGE_FIELDS[kind]:
                raise ValueError(f"invalid fields for page change: {kind}")
            if kind.startswith("button.") and kind != "button.add":
                if not isinstance(change["id"], str) or not change["id"].strip():
                    raise ValueError("button id must be a non-empty string")
            if kind == "button.update":
                self.update_button(change["id"], change["patch"])
            elif kind == "button.add":
                button = _normalize_page_create_buttons([change["button"]], page_key=self.key)[0]
                existing = next((item for item in self.buttons() if item["id"] == button["id"]), None)
                if existing is not None and existing != button:
                    raise ValueError(f"button id already exists: {button['id']}")
                if existing is None:
                    self._override(button["id"], button)
                    self.touched[button["id"]] = set(button) - {"id"}
                    self.layout_changed = True
            elif kind == "button.remove":
                button_id = change["id"]
                if any(item["id"] == button_id for item in self.defaults):
                    self.update_button(button_id, {"is_hidden": True})
                else:
                    self._override(button_id, None)
                    self.layout_changed = True
            elif kind == "button.reset":
                if not any(item["id"] == change["id"] for item in self.defaults):
                    raise ValueError("button.reset requires a base button")
                self._override(change["id"], None)
                self.touched.pop(change["id"], None)
                self.layout_changed = True
            elif kind == "text.set":
                value = change["value"]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("text.set requires non-empty text")
                if value != self.text():
                    self.row["text_custom"] = value
                    self.content_changed = True
            elif kind == "text.reset":
                self.row["text_custom"] = None
                self.content_changed = True
            elif kind == "media.set":
                image = change["image"]
                media_type = change["media_type"]
                if not isinstance(image, str) or not image.strip() or media_type not in {"photo", "video", "animation"}:
                    raise ValueError("media.set requires image and photo/video/animation media_type")
                if image != _page_image_value(self.row) or media_type != _page_media_type_value(self.row, image):
                    self.row.update(image_custom=image, media_type_custom=media_type)
                    self.content_changed = True
            elif kind in {"media.remove", "media.reset"}:
                self.row.update(image_custom="" if kind == "media.remove" else None, media_type_custom=None)
                self.content_changed = True

    def validate(self) -> None:
        """Check affected fields and final layout while preserving legacy data."""
        buttons = self.buttons()
        for button in buttons:
            fields = self.touched.get(button["id"], set())
            for name in fields:
                value = button.get(name)
                if name == "label":
                    require_label(value)
                elif name == "icon_custom_emoji_id":
                    require_emoji_id(value)
                elif name in {"row", "col"}:
                    if type(value) is not int or value < 0:
                        raise ValueError(f"button {name} must be a non-negative integer")
                elif name == "is_hidden" and not isinstance(value, bool):
                    raise ValueError("button is_hidden must be boolean")
                elif name == "color" and (not isinstance(value, str) or value not in _PAGE_BUTTON_COLORS):
                    raise ValueError("unsupported button color")
                elif name == "item_colors":
                    validate_page_item_colors([button])
            if fields & {"action_type", "action_value"}:
                if button.get("action_type") not in _PAGE_BUTTON_ACTION_TYPES:
                    raise ValueError("unsupported action_type")
                _validate_page_button_action(button, index=0, page_key=self.key)
        if self.layout_changed:
            def positions(items: list[dict[str, Any]]) -> Counter:
                return Counter(
                    (item.get("row", 0), item.get("col", 0))
                    for item in items if not item.get("is_hidden", False)
                )
            old_positions = positions(self.original_buttons)
            new_positions = positions(buttons)
            for position, count in new_positions.items():
                if count > max(1, old_positions[position]):
                    raise ValueError(f"duplicate final button position: {position}")
            old_rows = Counter()
            new_rows = Counter()
            for (row, _), count in old_positions.items():
                old_rows[row] += count
            for (row, _), count in new_positions.items():
                new_rows[row] += count
            if any(count > max(_PAGE_CREATE_MAX_BUTTONS_PER_ROW, old_rows[row]) for row, count in new_rows.items()):
                raise ValueError("final button row exceeds the button limit")
            if len(buttons) > max(_PAGE_CREATE_MAX_BUTTONS, len(self.original_buttons)):
                raise ValueError("page exceeds the button limit")
        if self.content_changed:
            text = self.text()
            visible = html_to_plain_text(text)
            limit = TELEGRAM_CAPTION_LIMIT if _page_image_value(self.row) else TELEGRAM_TEXT_LIMIT
            if not visible.strip() or len(visible) > limit:
                raise ValueError(f"final page text must contain 1 to {limit} visible characters")

    def patch(self) -> dict[str, Any]:
        self.validate()
        originals = {button['id']: button for button in self.original_buttons}
        custom_ids = {button['id'] for button in self.original_customs}
        self.customs = [button for button in self.customs
                        if button['id'] in custom_ids or button != originals.get(button['id'])]
        if self.before.get('text_custom') is None and self.row.get('text_custom') == self.before.get('text_default'):
            self.row['text_custom'] = None
        patch = {
            field: self.row.get(field)
            for field in ("text_custom", "image_custom", "media_type_custom")
            if self.row.get(field) != self.before.get(field)
        }
        if self.customs != self.original_customs:
            patch["buttons_custom"] = self.customs or None
        return patch
