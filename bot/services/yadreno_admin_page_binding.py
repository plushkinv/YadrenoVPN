"""Process-local page binding and fresh runtime snapshots for the /yaa lane."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, Message

from bot.services.page_context import PageContext
from bot.utils.page_flow import parse_registry_names
from bot.utils.page_renderer import (
    build_visible_keyboard_snapshot,
    get_page_stored_data,
    serialize_inline_button_rows,
)
from database.requests import get_page

YAA_REDACTED_USER_KEY = "[redacted_user_key]"
YAA_KEY_DELIVERY_PAGE = "key_delivery"
YAA_KEY_DELIVERY_CONTEXT_KEYS = frozenset({
    "key_delivery_raw_value",
    "key_raw_value",
})
YAA_KEY_DELIVERY_PLACEHOLDERS = frozenset({
    "%key_copy%".casefold(),
    "%key_link%".casefold(),
    "%key_link_url%".casefold(),
    "%ключ_для_копирования%".casefold(),
    "%ключ_ссылка%".casefold(),
    "%ключ_ссылка_url%".casefold(),
})


class YaaPageBindingContextError(RuntimeError):
    """A complete fresh snapshot could not be built for an active binding."""


@dataclass(frozen=True)
class YaaPageBinding:
    """Page/render inputs pinned when an administrator invokes /yaa."""

    page_key: str
    message: Message
    visibility: dict[str, bool] | None
    context: dict[str, Any] | None
    text_replacements: dict[str, str] | None
    prepend_buttons: list[list[InlineKeyboardButton]] | None
    append_buttons: list[list[InlineKeyboardButton]] | None
    backup_path: str
    attachment: dict[str, str] | None

    def page_context(self) -> PageContext:
        """Return an isolated PageContext suitable for the existing rerenderers."""
        return PageContext(
            page_key=self.page_key,
            message=self.message,
            visibility=copy.deepcopy(self.visibility),
            context=copy.deepcopy(self.context),
            text_replacements=copy.deepcopy(self.text_replacements),
            prepend_buttons=_copy_button_rows(self.prepend_buttons),
            append_buttons=_copy_button_rows(self.append_buttons),
        )


_bindings: dict[tuple[int, int], YaaPageBinding] = {}


def _copy_button_rows(
    rows: list[list[InlineKeyboardButton]] | None,
) -> list[list[InlineKeyboardButton]] | None:
    if rows is None:
        return None
    return [list(row) for row in rows]


def remember_yaa_page_binding(
    telegram_id: int,
    topic_id: int,
    page_context: PageContext,
    *,
    backup_path: str,
    attachment: dict[str, str] | None = None,
) -> YaaPageBinding:
    """Replace the process-local binding for one administrator/lane."""
    binding = YaaPageBinding(
        page_key=page_context.page_key,
        message=page_context.message,
        visibility=copy.deepcopy(page_context.visibility),
        context=copy.deepcopy(page_context.context),
        text_replacements=copy.deepcopy(page_context.text_replacements),
        prepend_buttons=_copy_button_rows(page_context.prepend_buttons),
        append_buttons=_copy_button_rows(page_context.append_buttons),
        backup_path=str(backup_path),
        attachment=copy.deepcopy(attachment),
    )
    _bindings[(int(telegram_id), int(topic_id))] = binding
    return binding


def get_yaa_page_binding(
    telegram_id: int,
    topic_id: int,
) -> YaaPageBinding | None:
    """Return the active /yaa binding without consulting current navigation."""
    return _bindings.get((int(telegram_id), int(topic_id)))


def clear_yaa_page_binding(telegram_id: int, topic_id: int) -> None:
    """Clear one process-local binding after a successful New Chat."""
    _bindings.pop((int(telegram_id), int(topic_id)), None)


def _redact_context(
    page_key: str,
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(runtime_context or {})
    if page_key == YAA_KEY_DELIVERY_PAGE:
        for key in YAA_KEY_DELIVERY_CONTEXT_KEYS:
            if key in result:
                result[key] = YAA_REDACTED_USER_KEY
    return result


def _redact_text_replacements(
    page_key: str,
    text_replacements: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not text_replacements:
        return None
    result = dict(text_replacements)
    if page_key == YAA_KEY_DELIVERY_PAGE:
        for placeholder in list(result):
            if str(placeholder).casefold() in YAA_KEY_DELIVERY_PLACEHOLDERS:
                result[placeholder] = YAA_REDACTED_USER_KEY
    return result


def _redact_visible_keyboard_urls(
    page_key: str,
    rows: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    if page_key != YAA_KEY_DELIVERY_PAGE or not rows:
        return rows
    encoded_redacted = quote(YAA_REDACTED_USER_KEY, safe="")
    return [
        [
            {
                **button,
                **(
                    {
                        "url": button["url"].replace(
                            encoded_redacted,
                            YAA_REDACTED_USER_KEY,
                        )
                    }
                    if isinstance(button.get("url"), str)
                    else {}
                ),
            }
            for button in row
        ]
        for row in rows
    ]


def _build_runtime(binding: YaaPageBinding) -> dict[str, Any]:
    runtime: dict[str, Any] = {}
    visibility = dict(binding.visibility or {})
    context = _redact_context(binding.page_key, binding.context)
    prepend_buttons = serialize_inline_button_rows(binding.prepend_buttons)
    append_buttons = serialize_inline_button_rows(binding.append_buttons)
    if visibility:
        runtime["visibility"] = visibility
    if context:
        runtime["context"] = context
    if prepend_buttons:
        runtime["prepend_buttons"] = prepend_buttons
    if append_buttons:
        runtime["append_buttons"] = append_buttons
    return runtime


def _build_page_flow(page_key: str) -> dict[str, list[str]]:
    page = get_page(page_key)
    if not page:
        raise YaaPageBindingContextError(
            f"Pinned page {page_key!r} is no longer present in the database"
        )
    return {
        "guard_names": parse_registry_names(page.get("guard_names")),
        "hook_names": parse_registry_names(page.get("hook_names")),
    }


def build_yaa_binding_runtime_context(binding: YaaPageBinding) -> dict[str, Any]:
    """Read effective page state and return one complete invocation snapshot."""
    try:
        stored_page = get_page_stored_data(binding.page_key)
        if stored_page is None:
            raise YaaPageBindingContextError(
                f"Pinned page {binding.page_key!r} has no stored state"
            )
        runtime_context = _redact_context(binding.page_key, binding.context)
        visible_keyboard = build_visible_keyboard_snapshot(
            buttons=stored_page.get("buttons") or [],
            visibility=binding.visibility,
            context=runtime_context,
            text_replacements=_redact_text_replacements(
                binding.page_key,
                binding.text_replacements,
            ),
            prepend_buttons=binding.prepend_buttons,
            append_buttons=binding.append_buttons,
        )
        visible_keyboard = _redact_visible_keyboard_urls(
            binding.page_key,
            visible_keyboard,
        )
        invocation: dict[str, Any] = {
            "source": "yaa",
            "page_key": binding.page_key,
            "page_flow": _build_page_flow(binding.page_key),
            "database_path": "database/vpn_bot.db",
            "backup": {"created": True, "path": binding.backup_path},
            "stored_page": stored_page,
            "visible_keyboard": visible_keyboard,
            "runtime": _build_runtime(binding),
            "task_format": "telegram_html",
        }
        if binding.attachment:
            invocation["attachment"] = copy.deepcopy(binding.attachment)
        return {
            "invocation": json.loads(
                json.dumps(
                    invocation,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
        }
    except YaaPageBindingContextError:
        raise
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        raise YaaPageBindingContextError(
            f"Failed to rebuild pinned page {binding.page_key!r}: {exc}"
        ) from exc


def build_bound_yaa_runtime_context(
    telegram_id: int,
    topic_id: int,
) -> dict[str, Any] | None:
    """Build a fresh full snapshot, or return None when no binding is active."""
    binding = get_yaa_page_binding(telegram_id, topic_id)
    if binding is None:
        return None
    return build_yaa_binding_runtime_context(binding)
