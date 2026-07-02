"""
Рендер страниц пользователя.

Единая точка формирования и отправки страниц из таблицы pages.
Реализует трёхслойную систему видимости кнопок:
  1. buttons_default.is_hidden — дефолт разработчика
  2. buttons_custom (мёрж по id) — кастомизация админа
  3. runtime — visibility dict (для internal), system handlers и page-переходы
"""
import json
import logging
from typing import Optional, Dict, List, Any

from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.placeholders import apply_placeholder_replacements

logger = logging.getLogger(__name__)

# Максимальное количество кнопок в одном ряду
MAX_BUTTONS_PER_ROW = 2
PAGE_MEDIA_TYPES = {'photo', 'video', 'animation'}


def _normalize_page_media_type(media_type: Optional[str], media_file_id: Optional[str]) -> Optional[str]:
    if not media_file_id:
        return None
    return media_type if media_type in PAGE_MEDIA_TYPES else 'photo'


def _page_image_value(row: Dict[str, Any]) -> Optional[str]:
    """
    Возвращает итоговый file_id медиа страницы.

    Для pages.image_custom используется три состояния:
    - NULL: использовать image_default;
    - пустая строка: админ явно отключил медиа;
    - file_id: использовать кастомное медиа.
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
    Возвращает итоговые данные страницы с учётом кастомизации.

    Текст: custom если есть, иначе default.
    Медиа: image_custom если не NULL, иначе image_default; пустой image_custom отключает медиа.
    Кнопки: мёрж buttons_default + buttons_custom по id.

    Args:
        page_key: Ключ страницы в таблице pages

    Returns:
        {"text": str, "image": str|None, "media_type": str|None, "buttons": list[dict]}
        или None если страница не найдена
    """
    from database.requests import get_page

    row = get_page(page_key)
    if not row:
        return None

    # Текст: custom → default
    text = row.get('text_custom') or row.get('text_default') or ''
    image = _page_image_value(row)
    media_type = _page_media_type_value(row, image)

    # Кнопки: мёрж по id
    buttons = _merge_buttons_by_id(
        buttons_default_json=row.get('buttons_default', '[]'),
        buttons_custom_json=row.get('buttons_custom'),
    )

    return {
        "text": text,
        "image": image,
        "media_type": media_type,
        "buttons": buttons,
    }


def _parse_buttons_json(raw: Optional[str]) -> List[Dict]:
    """Безопасный парсинг JSON массива кнопок."""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _merge_buttons_by_id(
    buttons_default_json: str,
    buttons_custom_json: Optional[str],
) -> List[Dict]:
    """
    Мержит два массива кнопок по полю id.

    Алгоритм:
    1. Парсим buttons_default и buttons_custom.
    2. Если buttons_custom пуст (NULL) — возвращаем buttons_default as-is.
    3. Для каждой кнопки из default: если в custom есть кнопка с тем же id →
       берём custom-версию (приоритет кастомных).
    4. Кнопки из custom, которых нет в default → добавленные админом, дописываем.
    5. Сортируем по (row, col).
    """
    defaults = _parse_buttons_json(buttons_default_json)
    customs = _parse_buttons_json(buttons_custom_json)

    if not customs:
        return defaults

    # Индексируем custom-кнопки по id
    custom_map = {btn.get('id'): btn for btn in customs if btn.get('id')}
    used_custom_ids = set()

    merged = []
    for btn in defaults:
        btn_id = btn.get('id')
        if btn_id and btn_id in custom_map:
            # Кастомная версия — приоритет
            merged.append(custom_map[btn_id])
            used_custom_ids.add(btn_id)
        else:
            # Нет кастомной — берём дефолтную
            merged.append(btn)

    # Добавленные админом кнопки (нет в default)
    for btn in customs:
        btn_id = btn.get('id')
        if btn_id and btn_id not in used_custom_ids:
            merged.append(btn)

    # Сортировка по (row, col)
    merged.sort(key=lambda b: (b.get('row', 0), b.get('col', 0)))

    return merged


def _merge_buttons_by_id_with_source(
    buttons_default_json: str,
    buttons_custom_json: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Мержит кнопки для /yaa-снимка и помечает источник каждой effective-кнопки.

    Если у кнопки есть custom-версия, default не дублируется: агенту для правки
    нужен текущий редактируемый вариант и понимание, откуда он взят.
    """
    defaults = _parse_buttons_json(buttons_default_json)
    customs = _parse_buttons_json(buttons_custom_json)

    custom_map = {btn.get('id'): btn for btn in customs if btn.get('id')}
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
        if btn_id and btn_id not in used_custom_ids:
            item = dict(btn)
            item['source'] = 'custom'
            merged.append(item)

    merged.sort(key=lambda b: (b.get('row', 0), b.get('col', 0)))
    return merged


def _stored_text_value(row: Dict[str, Any]) -> Dict[str, Any]:
    """Возвращает компактное состояние текста страницы для /yaa."""
    text_custom = row.get('text_custom')
    if text_custom:
        return {'source': 'custom', 'value': text_custom}
    return {
        'source': 'default',
        'value': row.get('text_default') or '',
        'custom': None,
    }


def _stored_image_value(row: Dict[str, Any]) -> Dict[str, Any]:
    """Возвращает компактное состояние медиа страницы для /yaa."""
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
    Возвращает DB-first состояние страницы для /yaa без дубля default/custom.

    Значения с custom показываются только как custom. Значения без custom
    показываются как default с явным custom=None.
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
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]],
    append_buttons: Optional[List[List[InlineKeyboardButton]]],
) -> InlineKeyboardMarkup:
    """
    Собирает InlineKeyboardMarkup из списка кнопок.

    Применяет слой 3 (runtime): visibility dict и system handlers.
    Правила размещения: по row, max 2 кнопки в ряд, фолбэк при коллизиях.
    """
    from bot.utils.action_registry import ACTION_REGISTRY, SYSTEM_BUTTONS

    if visibility is None:
        visibility = {}
    if context is None:
        context = {}

    # Обрабатываем каждую кнопку: определяем action, label, hidden
    resolved_buttons: List[Dict] = []

    for btn in buttons:
        btn_id = btn.get('id', '')
        action_type = btn.get('action_type', 'internal')
        action_value = btn.get('action_value')
        label = btn.get('label', '')
        icon_custom_emoji_id = btn.get('icon_custom_emoji_id')
        is_hidden = btn.get('is_hidden', False)
        color = btn.get('color')
        row = btn.get('row', 0)
        col = btn.get('col', 0)

        # Слой 3: visibility dict (для internal-кнопок)
        if btn_id in visibility:
            is_hidden = not visibility[btn_id]

        # Обработка по типу
        callback_data = None
        url = None

        if action_type == 'system':
            handler = SYSTEM_BUTTONS.get(btn_id)
            if handler is None:
                logger.warning(f"System handler не найден для кнопки '{btn_id}' — пропускаем")
                continue

            try:
                result = handler(context)
            except Exception as e:
                logger.error(f"Ошибка system handler '{btn_id}': {e}")
                continue

            if result is None:
                # System handler решил скрыть кнопку
                continue

            callback_data = result.get('callback_data')
            url = result.get('url')
            # System handler может переопределить label
            if result.get('label'):
                label = result['label']
            # System handler может скрыть кнопку
            if result.get('hidden', False):
                continue

        elif action_type == 'internal':
            if not action_value:
                logger.warning(f"Пустой action_value для internal-кнопки '{btn_id}' — пропускаем")
                continue

            cb = ACTION_REGISTRY.get(action_value)
            if cb is None:
                logger.warning(f"action_value '{action_value}' не найден в ACTION_REGISTRY — пропускаем")
                continue
            callback_data = cb

        elif action_type == 'url':
            if not action_value:
                logger.warning(f"Пустой action_value для url-кнопки '{btn_id}' — пропускаем")
                continue
            url = action_value

        elif action_type == 'page':
            from bot.utils.custom_pages import build_custom_page_callback, custom_page_exists

            if not action_value:
                logger.warning(f"Пустой action_value для page-кнопки '{btn_id}' — пропускаем")
                continue
            if not custom_page_exists(action_value):
                logger.warning(f"custom-страница '{action_value}' для кнопки '{btn_id}' не найдена или имеет неверный ключ — пропускаем")
                continue

            callback_data = build_custom_page_callback(action_value)
            if not callback_data:
                logger.warning(f"callback custom-страницы '{action_value}' для кнопки '{btn_id}' не помещается в лимит Telegram — пропускаем")
                continue

        else:
            logger.warning(f"Неизвестный action_type '{action_type}' для кнопки '{btn_id}' — пропускаем")
            continue

        # Пропускаем скрытые кнопки (после всех 3 слоёв)
        if is_hidden:
            continue

        resolved_buttons.append({
            'label': label,
            'icon_custom_emoji_id': icon_custom_emoji_id,
            'callback_data': callback_data,
            'url': url,
            'style': _resolve_button_style(color),
            'row': row,
            'col': col,
        })

    # Группируем по row и строим клавиатуру
    builder = InlineKeyboardBuilder()

    # Добавляем prepend_buttons перед кнопками страницы.
    if prepend_buttons:
        for row_btns in prepend_buttons:
            builder.row(*row_btns)

    if resolved_buttons:
        # Группируем кнопки по row
        rows_map: Dict[int, List[Dict]] = {}
        for btn in resolved_buttons:
            r = btn['row']
            if r not in rows_map:
                rows_map[r] = []
            rows_map[r].append(btn)

        # Сортируем ряды по номеру
        for row_num in sorted(rows_map.keys()):
            row_buttons = rows_map[row_num]
            # Формируем InlineKeyboardButton объекты
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

            # Фолбэк: по MAX_BUTTONS_PER_ROW в ряд
            for i in range(0, len(kb_buttons), MAX_BUTTONS_PER_ROW):
                chunk = kb_buttons[i:i + MAX_BUTTONS_PER_ROW]
                builder.row(*chunk)

    # Добавляем append_buttons (кнопки вне БД, например «Админ-панель»)
    if append_buttons:
        for row_btns in append_buttons:
            builder.row(*row_btns)

    return builder.as_markup()


def _apply_text_replacements(
    text: str,
    text_replacements: Optional[Dict[str, str]],
) -> str:
    """Применяет HTML-подстановки страницы так же, как render_page()."""
    rendered_text = text or ''
    if text_replacements:
        rendered_text = apply_placeholder_replacements(rendered_text, text_replacements)
    return rendered_text or '(пусто)'


def serialize_inline_button_rows(
    rows: Optional[List[List[InlineKeyboardButton]]],
) -> List[List[Dict[str, Any]]]:
    """Сериализует inline-кнопки в компактный JSON-friendly формат."""
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
    """Сериализует InlineKeyboardMarkup в компактные ряды кнопок."""
    if markup is None:
        return []
    return serialize_inline_button_rows(getattr(markup, 'inline_keyboard', None))


def build_visible_keyboard_snapshot(
    buttons: Optional[List[Dict[str, Any]]],
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> List[List[Dict[str, Any]]]:
    """Собирает компактный read-only снимок видимой inline-клавиатуры."""
    keyboard = _build_keyboard(
        buttons=buttons or [],
        visibility=visibility,
        context=context,
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
    """Собирает фактически отрендеренный вид страницы для контекста /yaa."""
    page_data = page_data or {}
    return {
        'text': _apply_text_replacements(
            page_data.get('text') or '',
            text_replacements,
        ),
        'image': page_data.get('image') or '',
        'media_type': page_data.get('media_type') or '',
        'keyboard': build_visible_keyboard_snapshot(
            buttons=page_data.get('buttons') or [],
            visibility=visibility,
            context=context,
            prepend_buttons=prepend_buttons,
            append_buttons=append_buttons,
        ),
    }


def _resolve_button_style(color: Optional[str]) -> Optional[str]:
    """
    Преобразует цвет из JSON кнопки в поддерживаемый Telegram style.

    secondary — это обычный стиль клиента Telegram, его не передаём явно.
    """
    if color in {'primary', 'success', 'danger'}:
        return color
    return None


def build_page_keyboard(
    page_key: str,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
) -> Optional[InlineKeyboardMarkup]:
    """Собирает клавиатуру страницы из таблицы pages без отправки сообщения."""
    page_data = get_page_data(page_key)
    if page_data is None:
        return None

    return _build_keyboard(
        buttons=page_data["buttons"],
        visibility=visibility,
        context=context,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )


async def render_page(
    target,
    page_key: str,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
    prepend_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    append_buttons: Optional[List[List[InlineKeyboardButton]]] = None,
    force_new: bool = False,
) -> None:
    """
    Получает страницу из БД и отправляет/редактирует сообщение.

    Args:
        target: Message или CallbackQuery (определяет send vs edit)
        page_key: Ключ страницы в таблице pages
        visibility: Переопределение видимости для internal-кнопок
                    {button_id: True/False}. True = показать, False = скрыть
        context: Контекст для system-кнопок (order_id, telegram_id, ...)
        text_replacements: Словарь плейсхолдеров для подстановки в текст
                          {"%тарифы%": "<b>Тарифы:</b>...", "%ключ%": "<pre>...</pre>"}
        prepend_buttons: Доп. ряды кнопок перед кнопками страницы
                       Список списков InlineKeyboardButton
        append_buttons: Доп. ряды кнопок вне БД (например, «Админ-панель»)
                       Список списков InlineKeyboardButton
        force_new: Принудительно отправить новое сообщение (не редактировать)
    """
    from bot.utils.text import safe_edit_or_send

    # 1. Получаем данные страницы
    page_data = get_page_data(page_key)

    if page_data is None:
        logger.error(f"Страница '{page_key}' не найдена в БД")
        msg = target.message if isinstance(target, CallbackQuery) else target
        await safe_edit_or_send(msg, "⚠️ Страница не настроена")
        return

    # 2. Обработка текста
    text = _apply_text_replacements(page_data["text"], text_replacements)

    # 3. Собираем клавиатуру
    kb = _build_keyboard(
        buttons=page_data["buttons"],
        visibility=visibility,
        context=context,
        prepend_buttons=prepend_buttons,
        append_buttons=append_buttons,
    )

    # 4. Определяем медиа
    image = page_data.get("image")
    media_type = page_data.get("media_type")

    # 5. Отправляем/редактируем
    msg = target.message if isinstance(target, CallbackQuery) else target
    rendered_message = await safe_edit_or_send(
        msg,
        text,
        reply_markup=kb,
        media=image,
        media_type=media_type,
        force_new=force_new,
    )

    # 6. Запоминаем редактируемую пользовательскую страницу для /yaa.
    try:
        from config import ADMIN_IDS
        from bot.services.page_context import remember_page_context

        if isinstance(target, CallbackQuery):
            viewer_id = target.from_user.id
        elif target.from_user and not target.from_user.is_bot:
            viewer_id = target.from_user.id
        else:
            chat = getattr(target, 'chat', None)
            viewer_id = chat.id if chat and getattr(chat, 'type', None) == 'private' else None

        if viewer_id in ADMIN_IDS:
            remember_page_context(
                viewer_id,
                page_key=page_key,
                message=rendered_message,
                visibility=visibility,
                context=context,
                text_replacements=text_replacements,
                prepend_buttons=prepend_buttons,
                append_buttons=append_buttons,
            )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст страницы для /yaa: %s", e)
