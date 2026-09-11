"""Validation for typed page creation and address-based editing."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bot.utils.action_registry import ACTION_REGISTRY, SYSTEM_BUTTONS, SYSTEM_COLLECTIONS, normalize_callback_data
from bot.utils.custom_pages import build_page_callback
from bot.utils.page_flow import PAGE_GUARDS, PAGE_HOOKS
from bot.utils.page_routes import build_page_route_callback, page_route_exists
from bot.utils.text import TELEGRAM_CAPTION_LIMIT, TELEGRAM_TEXT_LIMIT, html_to_plain_text
from database.requests import TRIAL_OFFER_ACTION_PREFIX, get_page_classification, get_trial_offer_by_id
from database.page_button_styles import BUTTON_COLORS, validate_page_item_colors

_PAGE_CREATE_FIELDS = frozenset({
    'text',
    'image',
    'media_type',
    'buttons',
    'guard_names',
    'hook_names',
})
_PAGE_BUTTON_FIELDS = frozenset({
    'id',
    'label',
    'color',
    'item_colors',
    'icon_custom_emoji_id',
    'row',
    'col',
    'is_hidden',
    'action_type',
    'action_value',
})
_PAGE_BUTTON_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_PAGE_BUTTON_COLORS = BUTTON_COLORS
_PAGE_BUTTON_ACTION_TYPES = frozenset({
    'internal',
    'system',
    'system_collection',
    'url',
    'page',
    'route',
})
_PAGE_CREATE_MAX_BUTTONS = 50
_PAGE_CREATE_MAX_BUTTONS_PER_ROW = 2


def _normalize_page_flow_names(
    value: Any,
    field: str,
    registry: dict[str, Any],
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f'page.{field} must be an array')
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f'page.{field} items must be non-empty strings')
        name = item.strip().casefold()
        if name not in registry:
            raise ValueError(f'page.{field} references unregistered name: {name}')
        if name not in normalized:
            normalized.append(name)
    return normalized


def _require_button_text(value: Any, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'page.buttons[{index}].{field} must be a non-empty string')
    return value.strip()


def _validate_page_button_action(
    button: dict[str, Any],
    *,
    index: int,
    page_key: str,
) -> None:
    button_id = str(button['id'])
    action_type = str(button['action_type'])
    action_value = button.get('action_value')
    if action_type in {'system', 'system_collection'}:
        registry = SYSTEM_BUTTONS if action_type == 'system' else SYSTEM_COLLECTIONS
        if button_id not in registry:
            raise ValueError(
                f'page.buttons[{index}] references unregistered {action_type} id: {button_id}'
            )
        if action_value not in {None, ''}:
            raise ValueError(
                f'page.buttons[{index}].action_value must be empty for {action_type}'
            )
        return

    value = _require_button_text(action_value, 'action_value', index)
    if action_type == 'internal':
        callback_data = ACTION_REGISTRY.get(value)
        if callback_data is not None:
            normalize_callback_data(callback_data)
            return
        if value.startswith(TRIAL_OFFER_ACTION_PREFIX):
            raw_offer_id = value[len(TRIAL_OFFER_ACTION_PREFIX):]
            if raw_offer_id.isdecimal() and int(raw_offer_id) > 0:
                offer = get_trial_offer_by_id(int(raw_offer_id))
                if offer is not None:
                    normalize_callback_data(f'trial_offer:{int(raw_offer_id)}')
                    return
        raise ValueError(
            f'page.buttons[{index}] references unregistered internal action: {value}'
        )
    if action_type == 'url':
        parsed = urlparse(value)
        if parsed.scheme.lower() not in {'http', 'https', 'tg'}:
            raise ValueError(
                f'page.buttons[{index}].action_value must use http, https, or tg'
            )
        return
    if action_type == 'page':
        classification = get_page_classification(value)
        if value != page_key and not classification.render_allowed:
            raise ValueError(
                f'page.buttons[{index}] references unavailable page: {value}'
            )
        if build_page_callback(value) is None:
            raise ValueError(
                f'page.buttons[{index}] page callback exceeds Telegram limits'
            )
        return
    if action_type == 'route':
        if not page_route_exists(value):
            raise ValueError(
                f'page.buttons[{index}] references unavailable route: {value}'
            )
        if build_page_route_callback(value) is None:
            raise ValueError(
                f'page.buttons[{index}] route callback exceeds Telegram limits'
            )
        return
    raise ValueError(f'page.buttons[{index}] has unsupported action_type: {action_type}')


def _normalize_page_create_buttons(value: Any, *, page_key: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError('page.buttons must be an array')
    if len(value) > _PAGE_CREATE_MAX_BUTTONS:
        raise ValueError(
            f'page.buttons exceeds {_PAGE_CREATE_MAX_BUTTONS} buttons'
        )

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    positions: set[tuple[int, int]] = set()
    row_counts: dict[int, int] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise TypeError(f'page.buttons[{index}] must be an object')
        unknown = sorted(set(raw) - _PAGE_BUTTON_FIELDS)
        if unknown:
            raise ValueError(f'page.buttons[{index}] has unknown fields: {unknown}')
        button_id = _require_button_text(raw.get('id'), 'id', index)
        if not _PAGE_BUTTON_ID_RE.fullmatch(button_id):
            raise ValueError(
                f'page.buttons[{index}].id must be lowercase snake_case'
            )
        if button_id in ids:
            raise ValueError(f'duplicate page button id: {button_id}')
        ids.add(button_id)

        label = _require_button_text(raw.get('label'), 'label', index)
        if '<' in label or '>' in label:
            raise ValueError(f'page.buttons[{index}].label must not contain HTML')
        if len(label) > 64:
            raise ValueError(f'page.buttons[{index}].label exceeds 64 characters')

        action_type = _require_button_text(
            raw.get('action_type'),
            'action_type',
            index,
        )
        if action_type not in _PAGE_BUTTON_ACTION_TYPES:
            raise ValueError(
                f'page.buttons[{index}].action_type is unsupported: {action_type}'
            )
        row = raw.get('row')
        col = raw.get('col')
        if (
            isinstance(row, bool)
            or not isinstance(row, int)
            or row < 0
            or isinstance(col, bool)
            or not isinstance(col, int)
            or col < 0
        ):
            raise ValueError(
                f'page.buttons[{index}].row and col must be non-negative integers'
            )
        position = (row, col)
        if position in positions:
            raise ValueError(f'duplicate page button position: row={row}, col={col}')
        positions.add(position)
        row_counts[row] = row_counts.get(row, 0) + 1
        if row_counts[row] > _PAGE_CREATE_MAX_BUTTONS_PER_ROW:
            raise ValueError(
                f'page button row {row} exceeds {_PAGE_CREATE_MAX_BUTTONS_PER_ROW} buttons'
            )

        is_hidden = raw.get('is_hidden', False)
        if not isinstance(is_hidden, bool):
            raise TypeError(f'page.buttons[{index}].is_hidden must be bool')
        color = raw.get('color', 'secondary')
        if color not in _PAGE_BUTTON_COLORS:
            raise ValueError(f'page.buttons[{index}].color is unsupported')
        emoji_id = raw.get('icon_custom_emoji_id')
        if emoji_id is not None and (
            not isinstance(emoji_id, str) or not emoji_id.isdecimal()
        ):
            raise ValueError(
                f'page.buttons[{index}].icon_custom_emoji_id must be a numeric string or null'
            )

        button = {
            'id': button_id,
            'label': label,
            'color': color,
            'row': row,
            'col': col,
            'is_hidden': is_hidden,
            'action_type': action_type,
            'action_value': raw.get('action_value'),
        }
        if emoji_id is not None:
            button['icon_custom_emoji_id'] = emoji_id
        if 'item_colors' in raw:
            button['item_colors'] = raw['item_colors']
        validate_page_item_colors([button])
        _validate_page_button_action(button, index=index, page_key=page_key)
        normalized.append(button)
    return normalized


def _normalize_page_create_payload(page_key: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError('page is required and must be an object')
    unknown = sorted(set(value) - _PAGE_CREATE_FIELDS)
    if unknown:
        raise ValueError(f'page has unknown fields: {unknown}')
    text = value.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('page.text must be a non-empty string')
    image = value.get('image')
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise ValueError('page.image must be a non-empty string or null')
    media_type = value.get('media_type')
    if image is None:
        if media_type is not None:
            raise ValueError('page.media_type must be null when page.image is null')
    elif media_type not in {'photo', 'video', 'animation'}:
        raise ValueError(
            'page.media_type must be photo, video, or animation when media is set'
        )
    visible_text = html_to_plain_text(text)
    if not visible_text:
        raise ValueError('page.text must contain visible text')
    text_limit = TELEGRAM_CAPTION_LIMIT if image is not None else TELEGRAM_TEXT_LIMIT
    if len(visible_text) > text_limit:
        raise ValueError(
            f'page.text exceeds Telegram limit: {len(visible_text)} of {text_limit}'
        )
    return {
        'text': text,
        'image': image,
        'media_type': media_type,
        'buttons': _normalize_page_create_buttons(
            value.get('buttons', []),
            page_key=page_key,
        ),
        'guard_names': _normalize_page_flow_names(
            value.get('guard_names', []),
            'guard_names',
            PAGE_GUARDS,
        ),
        'hook_names': _normalize_page_flow_names(
            value.get('hook_names', []),
            'hook_names',
            PAGE_HOOKS,
        ),
    }


