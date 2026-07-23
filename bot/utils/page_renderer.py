"""
Rendering of user pages.

A single point for generating and sending pages from the pages table.
Implements a three-layer button visibility system:
  1. buttons_default.is_hidden — developer default
  2. buttons_custom (merge by id) - admin customization
  3. runtime - visibility dict by button id, system handlers and page/route transitions
"""
import json
import logging
import re
from collections.abc import Mapping
from typing import Optional, Dict, List, Any
from urllib.parse import urlparse

from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.placeholders import (
    apply_page_placeholders,
    apply_placeholder_replacements,
    contains_placeholder,
)
from bot.utils.page_placeholder_context import (
    enrich_page_placeholder_context,
    enrich_page_placeholder_context_sync,
)

logger = logging.getLogger(__name__)

# Maximum number of buttons in one row
MAX_BUTTONS_PER_ROW = 2
PAGE_MEDIA_TYPES = {'photo', 'video', 'animation'}
_COLLECTION_ITEM_KEY_RE = re.compile(r'item_[A-Za-z0-9_]+\Z', re.IGNORECASE)


def validate_required_user_pages() -> int:
    """Fail startup when any stock user screen or keyboard template is absent."""
    from bot.utils.message_editor import REQUIRED_USER_PAGE_KEYS
    from database.requests import get_page_keys

    stored = get_page_keys()
    missing = sorted(REQUIRED_USER_PAGE_KEYS - stored)
    if missing:
        raise RuntimeError("Required user pages are missing: " + ", ".join(missing))
    return len(REQUIRED_USER_PAGE_KEYS)


def _normalize_page_media_type(media_type: Optional[str], media_file_id: Optional[str]) -> Optional[str]:
    if not media_file_id:
        return None
    return media_type if media_type in PAGE_MEDIA_TYPES else 'photo'


def _page_image_value(row: Dict[str, Any]) -> Optional[str]:
    """
    Returns the final file_id of the media page.

    Pages.image_custom uses three states:
    - NULL: use image_default;
    - empty line: the admin has explicitly disabled the media;
    - file_id: use custom media.
    """
    custom_image = row.get('image_custom')
    if custom_image is not None:
        return custom_image or None
    return row.get('image_default')


def _page_media_type_value(row: Dict[str, Any], image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    if row.get('image_custom') is not None:
        return _normalize_page_media_type(row.get('media_type_custom'), image)
    return _normalize_page_media_type(row.get('media_type_default'), image)


def get_page_data(page_key: str) -> Optional[Dict[str, Any]]:
    """
    Returns the final page data taking into account customization.

    Text: custom if available, otherwise default.
    Media: image_custom if not NULL, otherwise image_default; empty image_custom disables media.
    Buttons: merge buttons_default + buttons_custom by id.

    Args:
        page_key: Key of the page in the pages table

    Returns:
        {"text": str, "image": str|None, "media_type": str|None, "buttons": list[dict]}
        or None if page not found
    """
    from database.requests import get_page

    row = get_page(page_key)
    if not row:
        return None

    # Text: custom → default
    text = row.get('text_custom') or row.get('text_default') or ''
    image = _page_image_value(row)
    media_type = _page_media_type_value(row, image)

    # Buttons: merge by id
    buttons = _merge_buttons_by_id(
        buttons_default_json=row.get('buttons_default', '[]'),
        buttons_custom_json=row.get('buttons_custom'),
    )

    return {
        "text": text,
        "image": image,
        "media_type": media_type,
        "buttons": buttons,
        "_text_default": row.get('text_default') or '',
        "_text_custom": row.get('text_custom'),
    }


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


def _collection_item_replacements(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Builds the local placeholder map for one repeatable button item."""
    return {
        f'%{key}%': value
        for key, value in data.items()
        if isinstance(key, str) and _COLLECTION_ITEM_KEY_RE.fullmatch(key)
    }


def _merge_buttons_by_id(
    buttons_default_json: str,
    buttons_custom_json: Optional[str],
) -> List[Dict]:
    """
    Merges two arrays of buttons based on the id field.

    Algorithm:
    1. Parse buttons_default and buttons_custom.
    2. If buttons_custom is empty (NULL) - return buttons_default as-is.
    3. For each button from default: if in custom there is a button with the same id →
       take the custom version (custom priority).
    4. We add buttons from custom that are not in default → added by the admin.
    5. Sort by (row, col).
    """
    defaults = _parse_buttons_json(buttons_default_json)
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
) -> List[Dict[str, Any]]:
    """
    Merges buttons for /yaa snapshot and marks the source of each effective button.

    If a button has a custom version, default is not duplicated: to the agent for editing
    you need the current edited version and an understanding of where it came from.
    """
    defaults = _parse_buttons_json(buttons_default_json)
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


def _stored_text_value(row: Dict[str, Any]) -> Dict[str, Any]:
    """Returns the compact state of the page text for /yaa."""
    text_custom = row.get('text_custom')
    if text_custom:
        return {'source': 'custom', 'value': text_custom}
    return {
        'source': 'default',
        'value': row.get('text_default') or '',
        'custom': None,
    }


def _stored_image_value(row: Dict[str, Any]) -> Dict[str, Any]:
    """Returns the compact state of the media page for /yaa."""
    image_custom = row.get('image_custom')
    if image_custom is not None:
        return {
            'source': 'custom',
            'value': image_custom,
            'media_type': _normalize_page_media_type(row.get('media_type_custom'), image_custom),
        }
    image_default = row.get('image_default') or ''
    return {
        'source': 'default',
        'value': image_default,
        'media_type': _normalize_page_media_type(row.get('media_type_default'), image_default),
        'custom': None,
    }


def get_page_stored_data(page_key: str) -> Optional[Dict[str, Any]]:
    """
    Returns the DB-first page state for /yaa without the default/custom duplicate.

    Values with custom are shown only as custom. Values without custom
    are shown as default with explicit custom=None.
    """
    from database.requests import get_page

    row = get_page(page_key)
    if not row:
        return None

    return {
        'text': _stored_text_value(row),
        'image': _stored_image_value(row),
        'buttons': _merge_buttons_by_id_with_source(
            buttons_default_json=row.get('buttons_default', '[]'),
            buttons_custom_json=row.get('buttons_custom'),
        ),
    }


def _build_keyboard(
    buttons: List[Dict],
    visibility: Optional[Dict[str, bool]],
    context: Optional[Dict],
    text_replacements: Optional[Dict[str, str]],
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]],
    append_buttons: Optional[List[List[InlineKeyboardButton]]],
) -> InlineKeyboardMarkup:
    """
    Collects InlineKeyboardMarkup from a list of buttons.

    Applies layer 3 (runtime): visibility dict and system handlers.
    Placement rules: by row, max 2 buttons in a row, fallback in case of collisions.
    """
    from bot.utils.action_registry import (
        ACTION_REGISTRY,
        SYSTEM_BUTTONS,
        normalize_callback_data,
        resolve_system_collection,
        resolve_system_button,
    )

    visibility, invalid_visibility_ids = _normalize_runtime_visibility(visibility)
    context = _normalize_context_mapping(context)

    def render_label(
        raw_label: Any,
        btn_id: str,
        label_context: Optional[Dict[str, Any]] = None,
        item_replacements: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not isinstance(raw_label, str):
            logger.warning("Label кнопки '%s' должен быть строкой — пропускаем", btn_id)
            return None
        rendered = apply_page_placeholders(
            raw_label,
            text_replacements,
            label_context or context,
            mode='button_label',
        )
        rendered = apply_placeholder_replacements(rendered, item_replacements).strip()
        if not rendered:
            logger.warning("Пустой label после подстановки для кнопки '%s' — пропускаем", btn_id)
            return None
        return rendered

    def require_string_value(raw_value: Any, field_name: str, btn_id: str) -> Optional[str]:
        if not isinstance(raw_value, str) or not raw_value.strip():
            logger.warning("Поле %s кнопки '%s' должно быть непустой строкой — пропускаем", field_name, btn_id)
            return None
        return raw_value

    def render_url(
        raw_url: Any,
        btn_id: str,
        url_context: Optional[Dict[str, Any]] = None,
        item_replacements: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        rendered = apply_page_placeholders(
            raw_url,
            text_replacements,
            url_context or context,
            mode='url',
        )
        rendered = apply_placeholder_replacements(rendered, item_replacements).strip()
        if not rendered:
            logger.warning("Пустой URL после подстановки для кнопки '%s' — пропускаем", btn_id)
            return None
        if contains_placeholder(rendered):
            logger.warning("URL кнопки '%s' содержит неизвестный плейсхолдер — пропускаем", btn_id)
            return None
        if not _is_allowed_button_url(rendered):
            logger.warning("URL кнопки '%s' не прошёл проверку схемы — пропускаем", btn_id)
            return None
        return rendered

    # We process each button: define action, label, hidden
    resolved_buttons: List[Dict] = []

    for btn in buttons:
        if not isinstance(btn, Mapping):
            logger.warning("Кнопка страницы должна быть JSON-объектом — пропускаем")
            continue
        btn_id = btn.get('id', '')
        if not _valid_button_id(btn_id):
            logger.warning("id кнопки страницы должен быть непустой строкой — пропускаем")
            continue
        action_type = btn.get('action_type', 'internal')
        if not isinstance(action_type, str):
            logger.warning("action_type кнопки '%s' должен быть строкой — пропускаем", btn_id)
            continue
        action_value = btn.get('action_value')
        label = btn.get('label', '')
        icon_custom_emoji_id = btn.get('icon_custom_emoji_id')
        if icon_custom_emoji_id is not None and not isinstance(icon_custom_emoji_id, str):
            logger.warning("icon_custom_emoji_id кнопки '%s' должен быть строкой — игнорируем", btn_id)
            icon_custom_emoji_id = None
        is_hidden = btn.get('is_hidden', False)
        if not isinstance(is_hidden, bool):
            logger.warning("is_hidden кнопки '%s' должен быть bool — скрываем кнопку", btn_id)
            is_hidden = True
        color = btn.get('color')
        row = _button_position_value(btn.get('row', 0))
        col = _button_position_value(btn.get('col', 0))
        if row is None or col is None:
            logger.warning("row/col кнопки '%s' должны быть int — пропускаем", btn_id)
            continue

        # Layer 3: visibility dict by button id for all action_types.
        if btn_id in invalid_visibility_ids:
            is_hidden = True
        elif btn_id in visibility:
            is_hidden = not visibility[btn_id]

        # Processing by type
        callback_data = None
        url = None

        if action_type == 'system_collection':
            if is_hidden:
                continue
            try:
                collection_items = resolve_system_collection(btn_id, context)
            except Exception as e:
                logger.error("System collection '%s' failed: %s", btn_id, e)
                continue
            for item_index, item in enumerate(collection_items):
                if item.get('hidden') is True:
                    continue
                item_context = dict(context)
                item_data = item.get('data') or {}
                item_context.update(item_data)
                item_replacements = _collection_item_replacements(item_data)
                rendered_label = render_label(
                    label,
                    btn_id,
                    item_context,
                    item_replacements,
                )
                if rendered_label is None:
                    continue
                item_callback = item.get('callback_data')
                item_url = item.get('url')
                if item_url:
                    item_url = render_url(
                        item_url,
                        btn_id,
                        item_context,
                        item_replacements,
                    )
                    if not item_url:
                        continue
                if not item_callback and not item_url:
                    logger.warning(
                        "System collection '%s' item %s has no action — skipped",
                        btn_id,
                        item_index,
                    )
                    continue
                resolved_buttons.append({
                    'label': rendered_label,
                    'icon_custom_emoji_id': icon_custom_emoji_id,
                    'callback_data': item_callback,
                    'url': item_url,
                    'style': _resolve_button_style(color),
                    'row': item.get('row') if item.get('row') is not None else row + item_index,
                    'col': item.get('col') if item.get('col') is not None else col,
                })
            continue

        if action_type == 'system':
            if btn_id not in SYSTEM_BUTTONS and not (
                btn_id.startswith('btn_pay_ext_') or btn_id.startswith('btn_renew_pay_ext_')
            ):
                logger.warning(f"System handler не найден для кнопки '{btn_id}' — пропускаем")
                continue

            try:
                result = resolve_system_button(btn_id, context)
            except Exception as e:
                logger.error(f"Ошибка system handler '{btn_id}': {e}")
                continue

            if result is None:
                # System handler decided to hide the button
                continue

            callback_data = result.get('callback_data')
            url = result.get('url')
            # System handler can override label
            if result.get('label'):
                label = result['label']
            # System handler can hide the button
            if result.get('hidden', False):
                continue
            if url:
                url = render_url(url, btn_id)
                if not url:
                    continue

        elif action_type == 'internal':
            action_value = require_string_value(action_value, 'action_value', btn_id)
            if action_value is None:
                continue

            cb = ACTION_REGISTRY.get(action_value)
            if cb is None:
                logger.warning(f"action_value '{action_value}' не найден в ACTION_REGISTRY — пропускаем")
                continue
            try:
                callback_data = normalize_callback_data(
                    cb,
                    f"callback_data action_value '{action_value}'",
                )
            except ValueError as e:
                logger.warning("Некорректный callback_data для action_value '%s': %s", action_value, e)
                continue

        elif action_type == 'url':
            action_value = require_string_value(action_value, 'action_value', btn_id)
            if action_value is None:
                continue
            url = render_url(action_value, btn_id)
            if not url:
                continue

        elif action_type == 'page':
            from bot.utils.custom_pages import build_custom_page_callback, custom_page_exists

            action_value = require_string_value(action_value, 'action_value', btn_id)
            if action_value is None:
                continue
            if not custom_page_exists(action_value):
                logger.warning(f"custom-страница '{action_value}' для кнопки '{btn_id}' не найдена или имеет неверный ключ — пропускаем")
                continue

            callback_data = build_custom_page_callback(action_value)
            if not callback_data:
                logger.warning(f"callback custom-страницы '{action_value}' для кнопки '{btn_id}' не помещается в лимит Telegram — пропускаем")
                continue

        elif action_type == 'route':
            from bot.utils.page_routes import build_page_route_callback, page_route_exists

            action_value = require_string_value(action_value, 'action_value', btn_id)
            if action_value is None:
                continue
            if not page_route_exists(action_value):
                logger.warning(f"route '{action_value}' для кнопки '{btn_id}' не найден или выключен — пропускаем")
                continue

            callback_data = build_page_route_callback(action_value)
            if not callback_data:
                logger.warning(f"callback route '{action_value}' для кнопки '{btn_id}' не помещается в лимит Telegram — пропускаем")
                continue

        else:
            logger.warning(f"Неизвестный action_type '{action_type}' для кнопки '{btn_id}' — пропускаем")
            continue

        # Skip hidden buttons (after all 3 layers)
        if is_hidden:
            continue

        rendered_label = render_label(label, btn_id)
        if rendered_label is None:
            continue

        resolved_buttons.append({
            'label': rendered_label,
            'icon_custom_emoji_id': icon_custom_emoji_id,
            'callback_data': callback_data,
            'url': url,
            'style': _resolve_button_style(color),
            'row': row,
            'col': col,
        })

    # Group by row and build a keyboard
    builder = InlineKeyboardBuilder()

    # Add prepend_buttons before the page buttons.
    if prepend_buttons:
        for row_btns in prepend_buttons:
            builder.row(*row_btns)

    if resolved_buttons:
        # Grouping buttons by row
        rows_map: Dict[int, List[Dict]] = {}
        for btn in resolved_buttons:
            r = btn['row']
            if r not in rows_map:
                rows_map[r] = []
            rows_map[r].append(btn)

        # Sort rows by number
        for row_num in sorted(rows_map.keys()):
            row_buttons = rows_map[row_num]
            # Forming InlineKeyboardButton objects
            kb_buttons = []
            for btn in row_buttons:
                if btn['url']:
                    kb_buttons.append(
                        InlineKeyboardButton(
                            text=btn['label'],
                            url=btn['url'],
                            **(
                                {'icon_custom_emoji_id': btn['icon_custom_emoji_id']}
                                if btn['icon_custom_emoji_id'] else {}
                            ),
                            **({'style': btn['style']} if btn['style'] else {}),
                        )
                    )
                elif btn['callback_data']:
                    kb_buttons.append(
                        InlineKeyboardButton(
                            text=btn['label'],
                            callback_data=btn['callback_data'],
                            **(
                                {'icon_custom_emoji_id': btn['icon_custom_emoji_id']}
                                if btn['icon_custom_emoji_id'] else {}
                            ),
                            **({'style': btn['style']} if btn['style'] else {}),
                        )
                    )

            # Fallback: MAX_BUTTONS_PER_ROW in a row
            for i in range(0, len(kb_buttons), MAX_BUTTONS_PER_ROW):
                chunk = kb_buttons[i:i + MAX_BUTTONS_PER_ROW]
                builder.row(*chunk)

    # Add append_buttons (buttons outside the database, for example “Admin Panel”)
    if append_buttons:
        for row_btns in append_buttons:
            builder.row(*row_btns)

    return builder.as_markup()


def _normalize_runtime_visibility(raw_visibility: Optional[Dict[str, bool]]) -> tuple[Dict[str, bool], set[str]]:
    """Normalizes runtime visibility: non-bool override safely hides the button."""
    if raw_visibility is None:
        return {}, set()
    if not isinstance(raw_visibility, Mapping):
        logger.warning("Runtime visibility должен быть mapping — игнорируем")
        return {}, set()

    visibility: Dict[str, bool] = {}
    invalid_ids: set[str] = set()
    for button_id, visible in raw_visibility.items():
        if not isinstance(button_id, str):
            logger.warning("Runtime visibility содержит нестроковый button_id: %r", button_id)
            continue
        if not isinstance(visible, bool):
            logger.warning("Runtime visibility для кнопки '%s' должен быть bool — скрываем кнопку", button_id)
            invalid_ids.add(button_id)
            continue
        visibility[button_id] = visible
    return visibility, invalid_ids


def _is_allowed_button_url(url: str) -> bool:
    """Allows only safe URL button schemes after wildcarding."""
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https', 'tg'}:
        return False
    if parsed.scheme in {'http', 'https'} and not parsed.netloc:
        return False
    if parsed.scheme == 'tg' and (not url.startswith('tg://') or not parsed.netloc):
        return False
    return True


def _normalize_context_mapping(context: Optional[Mapping[str, Any]], field_name: str = 'context') -> Dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise ValueError(f"{field_name} должен быть mapping")
    return dict(context)


def _snapshot_page_key(context: Mapping[str, Any]) -> str:
    page_key = context.get('page_key')
    if page_key is None:
        return ''
    if not isinstance(page_key, str):
        raise ValueError("context.page_key должен быть строкой")
    return page_key


def _normalize_snapshot_buttons(buttons: Optional[List[Dict[str, Any]]]) -> List[Any]:
    if buttons is None:
        return []
    if not isinstance(buttons, list):
        raise ValueError("buttons должен быть list или None")
    return buttons


def _normalize_snapshot_page_data(page_data: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if page_data is None:
        return {}
    if not isinstance(page_data, Mapping):
        raise ValueError("page_data должен быть mapping или None")
    return dict(page_data)


def _apply_text_replacements(
    text: str,
    text_replacements: Optional[Dict[str, str]],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Applies HTML page substitutions in the same way as render_page()."""
    rendered_text = apply_page_placeholders(
        text or '',
        text_replacements,
        _normalize_context_mapping(context),
        mode='html',
    )
    return rendered_text


def render_page_text(
    page_key: str,
    context: Optional[Dict[str, Any]] = None,
    text_replacements: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Renders only page text without sending a message and without the keyboard.

    Needed for technical screens, where the text is already stored in pages, but sending
    remains special: for example, payment via QR with a photo and a runtime keyboard.
    """
    page_data = get_page_data(page_key)
    if page_data is None:
        return None

    render_context: Dict[str, Any] = {'page_key': page_key}
    render_context.update(_normalize_context_mapping(context))
    render_context = enrich_page_placeholder_context_sync(
        page_key,
        page_data,
        render_context,
        text_replacements,
    )

    return _apply_text_replacements(
        page_data.get('text') or '',
        text_replacements,
        render_context,
    )


def serialize_inline_button_rows(
    rows: Optional[List[List[InlineKeyboardButton]]],
) -> List[List[Dict[str, Any]]]:
    """Serializes inline buttons into a compact JSON-friendly format."""
    if not rows:
        return []

    serialized_rows: List[List[Dict[str, Any]]] = []
    for row in rows:
        serialized_row: List[Dict[str, Any]] = []
        for button in row or []:
            item: Dict[str, Any] = {
                'label': getattr(button, 'text', '') or '',
            }
            callback_data = getattr(button, 'callback_data', None)
            url = getattr(button, 'url', None)
            style = getattr(button, 'style', None)
            icon_custom_emoji_id = getattr(button, 'icon_custom_emoji_id', None)
            if callback_data:
                item['callback_data'] = callback_data
            if url:
                item['url'] = url
            if style:
                item['style'] = style
            if icon_custom_emoji_id:
                item['icon_custom_emoji_id'] = icon_custom_emoji_id
            if item['label'] or len(item) > 1:
                serialized_row.append(item)
        if serialized_row:
            serialized_rows.append(serialized_row)
    return serialized_rows


def serialize_inline_keyboard(
    markup: Optional[InlineKeyboardMarkup],
) -> List[List[Dict[str, Any]]]:
    """Serializes InlineKeyboardMarkup into compact rows of buttons."""
    if markup is None:
        return []
    return serialize_inline_button_rows(getattr(markup, 'inline_keyboard', None))


def build_visible_keyboard_snapshot(
    buttons: Optional[List[Dict[str, Any]]],
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> List[List[Dict[str, Any]]]:
    """Collects a compact read-only snapshot of a visible inline keyboard."""
    render_context: Dict[str, Any] = _normalize_context_mapping(context)
    normalized_buttons = _normalize_snapshot_buttons(buttons)
    render_context = enrich_page_placeholder_context_sync(
        _snapshot_page_key(render_context),
        {'text': '', 'buttons': normalized_buttons},
        render_context,
        text_replacements,
    )
    keyboard = _build_keyboard(
        buttons=normalized_buttons,
        visibility=visibility,
        context=render_context,
        text_replacements=text_replacements,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )
    return serialize_inline_keyboard(keyboard)


def build_page_render_snapshot(
    page_data: Optional[Dict[str, Any]],
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> Dict[str, Any]:
    """Collects the actual rendered view of the page for the /yaa context."""
    page_data = _normalize_snapshot_page_data(page_data)
    page_buttons = _normalize_snapshot_buttons(page_data.get('buttons'))
    render_context: Dict[str, Any] = _normalize_context_mapping(context)
    render_context = enrich_page_placeholder_context_sync(
        _snapshot_page_key(render_context),
        page_data,
        render_context,
        text_replacements,
    )
    return {
        'text': _apply_text_replacements(
            page_data.get('text') or '',
            text_replacements,
            render_context,
        ),
        'image': page_data.get('image') or '',
        'media_type': page_data.get('media_type') or '',
        'keyboard': build_visible_keyboard_snapshot(
            buttons=page_buttons,
            visibility=visibility,
            context=render_context,
            text_replacements=text_replacements,
            prepend_buttons=prepend_buttons,
            append_buttons=append_buttons,
        ),
    }


def _resolve_button_style(color: Optional[str]) -> Optional[str]:
    """
    Converts the color from JSON of a button to a supported Telegram style.

    secondary is the usual style of the Telegram client, we do not transfer it explicitly.
    """
    if not isinstance(color, str):
        return None
    if color in {'primary', 'success', 'danger'}:
        return color
    return None


def build_page_keyboard(
    page_key: str,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> Optional[InlineKeyboardMarkup]:
    """Collects the page keyboard from the pages table without sending a message."""
    page_data = get_page_data(page_key)
    if page_data is None:
        return None

    render_context = {'page_key': page_key}
    render_context.update(_normalize_context_mapping(context))
    render_context = enrich_page_placeholder_context_sync(
        page_key,
        page_data,
        render_context,
        text_replacements,
    )

    return _build_keyboard(
        buttons=page_data["buttons"],
        visibility=visibility,
        context=render_context,
        text_replacements=text_replacements,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )


def _target_viewer_id(target) -> Optional[int]:
    if isinstance(target, CallbackQuery):
        return target.from_user.id
    user = getattr(target, 'from_user', None)
    if user and not getattr(user, 'is_bot', False):
        return user.id
    chat = getattr(target, 'chat', None)
    if chat and getattr(chat, 'type', None) == 'private':
        return chat.id
    return None


def _target_bot_username(target) -> str:
    bot = getattr(target, 'bot', None)
    if bot is None and isinstance(target, CallbackQuery):
        bot = getattr(target.message, 'bot', None)
    return (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )


def _build_render_context(
    target,
    page_key: str,
    context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    render_context: Dict[str, Any] = {'page_key': page_key}
    viewer_id = _target_viewer_id(target)
    if viewer_id:
        render_context['telegram_id'] = viewer_id
    bot_username = _target_bot_username(target)
    if bot_username:
        render_context['bot_username'] = bot_username
    render_context.update(_normalize_context_mapping(context))
    return render_context


def _build_fallback_page_data(fallback_text: str) -> Dict[str, Any]:
    return {
        'text': fallback_text,
        'image': None,
        'media_type': None,
        'buttons': [],
    }


def _resolve_render_media(
    page_data: Dict[str, Any],
    media_policy: str,
    runtime_media: Any,
    runtime_media_type: Optional[str],
) -> tuple[Any, Optional[str]]:
    if media_policy == 'page':
        return page_data.get('image'), page_data.get('media_type')
    if media_policy == 'runtime':
        if runtime_media is None:
            return None, None
        return runtime_media, _normalize_page_media_type(runtime_media_type, runtime_media)
    raise ValueError("media_policy must be 'page' or 'runtime'")


async def render_page(
    target,
    page_key: str,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    force_new: bool = False,
    fallback_text: Optional[str] = None,
    send_func=None,
    media_policy: str = 'page',
    runtime_media: Any = None,
    runtime_media_type: Optional[str] = None,
) -> Optional[Message]:
    """
    Retrieves a page from the database and sends/edits a message.

    Args:
        target: Message or CallbackQuery (defines send vs edit)
        page_key: Key of the page in the pages table
        visibility: Override visibility by button id
                    {button_id: True/False}. True = show, False = hide
        context: Context for system buttons (order_id, telegram_id, ...)
        text_replacements: Dictionary of canonical placeholders for substitution
                          {"<tariffs placeholder>": "<b>Tariffs:</b>...", "<key name placeholder>": "Basic"}
        prepend_buttons: Prepend. rows of buttons in front of page buttons
                       List of lists InlineKeyboardButton
        append_buttons: Add. rows of buttons outside the database (for example, “Admin panel”)
                       List of lists InlineKeyboardButton
        force_new: Force a new message to be sent (do not edit)
        fallback_text: Text rendered through the same pipeline if the page row is absent
        send_func: safe_edit_or_send-compatible sender override
        media_policy: "page" uses pages media, "runtime" uses runtime_media
        runtime_media: Technical media object/file_id used with media_policy="runtime"
        runtime_media_type: Runtime media type: photo, video or animation
    """
    from bot.utils.text import safe_edit_or_send

    sender = send_func or safe_edit_or_send

    # 1. Get page data
    rendered_page_key = page_key
    page_data = get_page_data(rendered_page_key)

    if page_data is None and fallback_text is None:
        logger.error("User page %r is missing; rendering screen_unavailable", page_key)
        rendered_page_key = 'screen_unavailable'
        page_data = get_page_data(rendered_page_key)
        if page_data is None:
            raise RuntimeError(
                f"Required fallback page 'screen_unavailable' is missing while rendering {page_key!r}"
            )
    elif page_data is None:
        page_data = _build_fallback_page_data(fallback_text or '')

    render_context = _build_render_context(target, rendered_page_key, context)
    render_context = await enrich_page_placeholder_context(
        rendered_page_key,
        page_data,
        render_context,
        text_replacements,
    )

    from bot.utils.action_dispatcher import apply_action_policy_previews

    rendered_buttons = await apply_action_policy_previews(
        page_data["buttons"],
        target,
        page_key=rendered_page_key,
        context=render_context,
    )

    # 2. Text processing
    text = _apply_text_replacements(page_data["text"], text_replacements, render_context)

    # 3. Assembling the keyboard
    kb = _build_keyboard(
        buttons=rendered_buttons,
        visibility=visibility,
        context=render_context,
        text_replacements=text_replacements,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )

    # 4. Define the media
    media, media_type = _resolve_render_media(
        page_data,
        media_policy,
        runtime_media,
        runtime_media_type,
    )

    # 5. Submit/edit
    msg = target.message if isinstance(target, CallbackQuery) else target
    rendered_message = await sender(
        msg,
        text,
        reply_markup=kb,
        media=media,
        media_type=media_type,
        force_new=force_new,
    )

    # 6. Remember the editable user page for /yaa.
    try:
        from config import ADMIN_IDS
        from bot.services.page_context import remember_page_context

        viewer_id = _target_viewer_id(target)

        if viewer_id in ADMIN_IDS:
            remember_page_context(
                viewer_id,
                page_key=rendered_page_key,
                message=rendered_message,
                visibility=visibility,
                context=render_context,
                text_replacements=text_replacements,
                prepend_buttons=prepend_buttons,
                append_buttons=append_buttons,
            )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст страницы для /yaa: %s", e)

    return rendered_message
