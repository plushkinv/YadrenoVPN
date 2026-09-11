"""Literal bulk replacement over allowlisted effective customization content."""
from __future__ import annotations

import re
import unicodedata
from html import unescape
from typing import Any

from bot.services.yadreno_admin_page_patch import PagePatch, require_emoji_id, require_label
from bot.utils.placeholders import _iter_placeholder_matches
from bot.utils.text import TELEGRAM_TEXT_LIMIT, escape_html, html_to_plain_text
from database.requests import CustomizationChanges


REPLACE_SCOPES = ("all", "pages", "ui_texts", "message_templates")
REPLACE_TYPES = ("text.replace", "button.label.replace", "button.emoji.replace")
MESSAGE_TEMPLATE_KEYS = (
    "my_keys_item_template", "notification_text", "traffic_notification_text",
    "referral_new_ref_notification_text", "referral_purchase_notification_text",
)
_MARKUP = re.compile(r'''<!--.*?-->|<(?:[^>"']|"[^"]*"|'[^']*')*>''', re.DOTALL)
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
_MAX_SAMPLES = 10
_SAMPLE_TEXT_LIMIT = 250


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"{name} must be {'a' if empty else 'a non-empty'} string")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be a non-empty array of strings")
    return list(dict.fromkeys(value))


def validate_replace_args(args: dict[str, Any]) -> tuple[str, list[str] | None, list[dict[str, Any]]]:
    """Validate every rule before reading or mutating any content."""
    if set(args) - {"dry_run", "scope", "page_keys", "rules"}:
        raise ValueError("unknown replacement arguments")
    if type(args.get("dry_run")) is not bool:
        raise ValueError("dry_run is required and must be boolean")
    scope = args.get("scope", "all")
    if not isinstance(scope, str) or scope not in REPLACE_SCOPES:
        raise ValueError("unsupported replacement scope")
    page_keys = None
    if "page_keys" in args:
        page_keys = _string_list(args["page_keys"], "page_keys")
        if scope not in {"all", "pages"}:
            raise ValueError("page_keys requires scope=all or pages")
        scope = "pages"
    rules = args.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules must be a non-empty array")
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") not in REPLACE_TYPES:
            raise ValueError("unsupported replacement rule type")
        kind = rule["type"]
        required = {"type", "from", "to"} if kind == "button.emoji.replace" else {"type", "find", "replace"}
        allowed = required | ({"match"} if kind.startswith("button.") else set())
        if set(rule) - allowed or required - set(rule):
            raise ValueError(f"invalid fields for {kind}")
        if kind == "button.emoji.replace":
            for field in ("from", "to"):
                value = rule[field]
                if not isinstance(value, dict) or set(value) not in ({"unicode"}, {"custom_emoji_id"}):
                    raise ValueError(f"{field} requires exactly unicode or custom_emoji_id")
                if "unicode" in value:
                    icon = _text(value["unicode"], "unicode")
                    if icon.strip() != icon or any(char.isspace() for char in icon):
                        raise ValueError("unicode must be one non-whitespace icon prefix")
                    if list(_iter_placeholder_matches(icon)):
                        raise ValueError("an emoji prefix cannot be a placeholder")
                else:
                    _text(value["custom_emoji_id"], "custom_emoji_id")
                    require_emoji_id(value["custom_emoji_id"])
        else:
            _text(rule["find"], "find")
            _text(rule["replace"], "replace", empty=True)
        if "match" in rule:
            match = rule["match"]
            if not isinstance(match, dict) or not match or set(match) - {"ids", "labels", "action_type", "action_value"}:
                raise ValueError("match must contain supported exact button filters")
            for field, value in match.items():
                if field in {"ids", "labels"}:
                    _string_list(value, field)
                elif field == "action_value":
                    if value is not None:
                        _text(value, field, empty=True)
                else:
                    _text(value, field)
    return scope, page_keys, rules


def _replace_node(raw: str, find: str, replacement: str, *, html: bool) -> tuple[str, int]:
    """Map visible matches back to source spans, preserving untouched entities."""
    decoded = []
    starts: list[int] = []
    ends: list[int] = []
    offset = 0
    entities = list(_ENTITY.finditer(raw)) if html else []
    for entity in entities:
        for index in range(offset, entity.start()):
            decoded.append(raw[index]); starts.append(index); ends.append(index + 1)
        value = unescape(entity.group())
        for character in value:
            decoded.append(character); starts.append(entity.start()); ends.append(entity.end())
        offset = entity.end()
    for index in range(offset, len(raw)):
        decoded.append(raw[index]); starts.append(index); ends.append(index + 1)
    text = "".join(decoded)
    protected = [(match.start(), match.end()) for match in _iter_placeholder_matches(text)]
    spans = []
    offset = 0
    while (position := text.find(find, offset)) >= 0:
        end = position + len(find)
        offset = end
        if any(position < right and end > left for left, right in protected):
            continue
        # A match must consume complete entities, including multi-codepoint ones.
        if (position and starts[position] == starts[position - 1]) or (end < len(text) and ends[end - 1] == ends[end]):
            continue
        spans.append((starts[position], ends[end - 1]))
    if not spans or find == replacement:
        return raw, len(spans)
    pieces = []
    offset = 0
    for start, end in spans:
        pieces.extend((raw[offset:start], escape_html(replacement) if html else replacement))
        offset = end
    pieces.append(raw[offset:])
    return "".join(pieces), len(spans)


def replace_text(text: str, find: str, replacement: str, *, html: bool) -> tuple[str, int]:
    """Replace literal text within nodes; markup and source placeholders survive."""
    parts = []
    count = 0
    offset = 0
    for markup in list(_MARKUP.finditer(text)) if html else []:
        value, matches = _replace_node(text[offset:markup.start()], find, replacement, html=html)
        parts.extend((value, markup.group()))
        count += matches
        offset = markup.end()
    value, matches = _replace_node(text[offset:], find, replacement, html=html)
    parts.append(value)
    updated = "".join(parts)
    if [item.group() for item in _iter_placeholder_matches(updated)] != [item.group() for item in _iter_placeholder_matches(text)]:
        raise ValueError("replacement must preserve placeholders")
    return updated, count + matches


def matches_button(button: dict[str, Any], match: dict[str, Any], *, page: bool = True) -> bool:
    for field, expected in match.items():
        if field in {"ids", "labels"}:
            if button.get("id" if field == "ids" else "label") not in expected:
                return False
        elif not page or button.get(field) != expected:
            return False
    return True


def _whole_prefix(label: str, prefix: str) -> bool:
    if not label.startswith(prefix):
        return False
    rest = label[len(prefix):]
    if prefix.endswith("\u200d"):
        return False
    if rest and (unicodedata.category(rest[0]).startswith("M") or rest[0] in "\u200d\ufe0f\ufe0e" or 0x1F3FB <= ord(rest[0]) <= 0x1F3FF):
        return False
    if rest and 0x1F1E6 <= ord(prefix[-1]) <= 0x1F1FF and 0x1F1E6 <= ord(rest[0]) <= 0x1F1FF:
        return False
    if rest and 0xE0020 <= ord(rest[0]) <= 0xE007F:
        return False
    return True


def replace_emoji(button: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any] | None:
    source, target = rule["from"], rule["to"]
    label = button.get("label")
    if not isinstance(label, str):
        return None
    if "unicode" in source:
        if not _whole_prefix(label, source["unicode"]):
            return None
        suffix = label[len(source["unicode"]):]
        if suffix.startswith(" "):
            suffix = suffix[1:]
    else:
        if button.get("icon_custom_emoji_id") != source["custom_emoji_id"]:
            return None
        suffix = label
    if source == target:
        return {}
    if "custom_emoji_id" in target:
        return {"label": suffix, "icon_custom_emoji_id": target["custom_emoji_id"]}
    return {"label": target["unicode"] + (" " + suffix if suffix else ""), "icon_custom_emoji_id": None}


def prepare_replacement(snapshot: dict[str, Any], scope: str, rules: list[dict[str, Any]]) -> CustomizationChanges:
    """Apply ordered rules in memory and return final changed DB rows only."""
    changes = CustomizationChanges()
    pages = {key: PagePatch(row) for key, row in snapshot["pages"].items()}
    ui_rows = {row["text_key"]: row for row in snapshot["ui_texts"]}
    ui_values = {
        key: row["text_custom"] if row["text_custom"] is not None else row["text_default"]
        for key, row in ui_rows.items()
    }
    original_ui = dict(ui_values)
    settings = dict(snapshot["settings"])
    matches = 0
    matched_records: set[tuple[str, ...]] = set()
    for rule in rules:
        kind = rule["type"]
        for key, page in pages.items():
            if kind == "text.replace":
                value, count = replace_text(page.text(), rule["find"], rule["replace"], html=True)
                if count:
                    matched_records.add(("page_text", key)); matches += count
                    page.apply([{"type": "text.set", "value": value}])
            else:
                for button in page.buttons():
                    if not matches_button(button, rule.get("match", {})):
                        continue
                    if kind == "button.label.replace":
                        label = button.get("label")
                        if not isinstance(label, str):
                            continue
                        value, count = replace_text(label, rule["find"], rule["replace"], html=False)
                        patch = {"label": value} if count else None
                    else:
                        patch = replace_emoji(button, rule)
                        count = int(patch is not None)
                    if count:
                        matched_records.add(("button", key, button["id"])); matches += count
                        if patch:
                            page.update_button(button["id"], patch)
        for key, row in ui_rows.items():
            is_button = row["text_format"] == "button"
            if (kind == "text.replace" and is_button) or (kind.startswith("button.") and not is_button):
                continue
            if is_button and not matches_button({"id": key, "label": ui_values[key]}, rule.get("match", {}), page=False):
                continue
            if kind == 'button.emoji.replace':
                if 'unicode' not in rule['from'] or 'unicode' not in rule['to']:
                    continue
                patch = replace_emoji({'label': ui_values[key]}, rule)
                count = int(patch is not None)
                value = patch.get('label', ui_values[key]) if patch else ui_values[key]
            else:
                value, count = replace_text(ui_values[key], rule["find"], rule["replace"], html=row["text_format"] == "html")
            if count:
                matched_records.add(("ui_text", key)); matches += count
                ui_values[key] = value
        if kind == "text.replace":
            for key, text in settings.items():
                value, count = replace_text(text, rule["find"], rule["replace"], html=True)
                if count:
                    matched_records.add(("message_template", key)); matches += count
                    settings[key] = value
    samples: list[dict[str, Any]] = []
    total_samples = 0
    changed_buttons = 0

    def sample(kind: str, key: str, before: Any, after: Any, button_id: str | None = None) -> None:
        nonlocal total_samples
        if before == after:
            return
        total_samples += 1
        if len(samples) < _MAX_SAMPLES:
            samples.append({"kind": kind, "key": key, "button_id": button_id,
                            "before": str(before)[:_SAMPLE_TEXT_LIMIT], "after": str(after)[:_SAMPLE_TEXT_LIMIT]})

    for key, page in pages.items():
        patch = page.patch()
        if not patch:
            continue
        changes.pages[key] = patch
        sample("page_text", key, page.before.get("text_custom") or page.before["text_default"], page.text())
        originals = {item["id"]: item for item in page.original_buttons}
        for button in page.buttons():
            old = originals[button["id"]]
            if button != old:
                changed_buttons += 1
                sample("button", key, {field: old.get(field) for field in ("label", "icon_custom_emoji_id")},
                       {field: button.get(field) for field in ("label", "icon_custom_emoji_id")}, button["id"])
    for key, value in ui_values.items():
        if value != original_ui[key]:
            if ui_rows[key]['text_format'] == 'button':
                require_label(value)
            elif len(html_to_plain_text(value) if ui_rows[key]['text_format'] == 'html' else value) > TELEGRAM_TEXT_LIMIT:
                raise ValueError(f'final UI text exceeds the Telegram limit: {key}')
            changes.ui_texts[key] = value
            sample("ui_text", key, original_ui[key], value)
    for key, value in settings.items():
        if value != snapshot["settings"][key]:
            visible = html_to_plain_text(value)
            if not visible.strip() or len(visible) > TELEGRAM_TEXT_LIMIT:
                raise ValueError(f"invalid final message template: {key}")
            changes.settings[key] = value
            sample("message_template", key, snapshot["settings"][key], value)
    changes.result = {
        "operation": "replace", "scope": scope, "scanned_pages": len(pages),
        "scanned_ui_texts": len(ui_values), "scanned_message_templates": len(settings),
        "matches": matches, "matched_records": len(matched_records),
        "changed_buttons": changed_buttons, "samples": samples,
        "samples_truncated": total_samples > len(samples),
    }
    return changes
