"""
Рендер страниц пользователя.

Единая точка формирования и отправки страниц из таблицы pages.
Реализует трёхслойную систему видимости кнопок:
  1. buttons_default.is_hidden — дефолт разработчика
  2. buttons_custom (мёрж по id) — кастомизация админа
  3. runtime — visibility dict (для internal) и system handlers (для system)
"""
import json
import logging
from typing import Optional, Dict, List, Any

from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Максимальное количество кнопок в одном ряду
MAX_BUTTONS_PER_ROW = 2


def get_page_data(page_key: str) -> Optional[Dict[str, Any]]:
    """
    Возвращает итоговые данные страницы с учётом кастомизации.

    Текст и фото: custom если есть, иначе default.
    Кнопки: мёрж buttons_default + buttons_custom по id.

    Args:
        page_key: Ключ страницы в таблице pages

    Returns:
        {"text": str, "image": str|None, "buttons": list[dict]}
        или None если страница не найдена
    """
    from database.requests import get_page

    row = get_page(page_key)
    if not row:
        return None

    # Текст: custom → default
    text = row.get('text_custom') or row.get('text_default') or ''
    image = row.get('image_custom') or row.get('image_default')

    # Кнопки: мёрж по id
    buttons = _merge_buttons_by_id(
        buttons_default_json=row.get('buttons_default', '[]'),
        buttons_custom_json=row.get('buttons_custom'),
    )

    return {
        "text": text,
        "image": image,
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


def _build_keyboard(
    buttons: List[Dict],
    visibility: Optional[Dict[str, bool]],
    context: Optional[Dict],
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

        else:
            logger.warning(f"Неизвестный action_type '{action_type}' для кнопки '{btn_id}' — пропускаем")
            continue

        # Пропускаем скрытые кнопки (после всех 3 слоёв)
        if is_hidden:
            continue

        resolved_buttons.append({
            'label': label,
            'callback_data': callback_data,
            'url': url,
            'style': _resolve_button_style(color),
            'row': row,
            'col': col,
        })

    # Группируем по row и строим клавиатуру
    builder = InlineKeyboardBuilder()

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
                            **({'style': btn['style']} if btn['style'] else {}),
                        )
                    )
                elif btn['callback_data']:
                    kb_buttons.append(
                        InlineKeyboardButton(
                            text=btn['label'],
                            callback_data=btn['callback_data'],
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


def _resolve_button_style(color: Optional[str]) -> Optional[str]:
    """
    Преобразует цвет из JSON кнопки в поддерживаемый Telegram style.

    secondary — это обычный стиль клиента Telegram, его не передаём явно.
    """
    if color in {'primary', 'success', 'danger'}:
        return color
    return None


async def render_page(
    target,
    page_key: str,
    visibility: Optional[Dict[str, bool]] = None,
    context: Optional[Dict] = None,
    text_replacements: Optional[Dict[str, str]] = None,
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
    text = page_data["text"]
    if text_replacements:
        for placeholder, value in text_replacements.items():
            text = text.replace(placeholder, value)

    if not text:
        text = '(пусто)'

    # 3. Собираем клавиатуру
    kb = _build_keyboard(
        buttons=page_data["buttons"],
        visibility=visibility,
        context=context,
        append_buttons=append_buttons,
    )

    # 4. Определяем медиа
    image = page_data.get("image")

    # 5. Отправляем/редактируем
    msg = target.message if isinstance(target, CallbackQuery) else target
    rendered_message = await safe_edit_or_send(
        msg,
        text,
        reply_markup=kb,
        photo=image,
        force_new=force_new,
    )

    # 6. Запоминаем редактируемую пользовательскую страницу для /yaa.
    try:
        from config import ADMIN_IDS
        from bot.services.page_context import remember_page_context

        if isinstance(target, CallbackQuery):
            viewer_id = target.from_user.id
        else:
            viewer_id = target.from_user.id if target.from_user else None

        if viewer_id in ADMIN_IDS:
            remember_page_context(
                viewer_id,
                page_key=page_key,
                message=rendered_message,
                visibility=visibility,
                context=context,
                text_replacements=text_replacements,
                append_buttons=append_buttons,
            )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст страницы для /yaa: %s", e)
