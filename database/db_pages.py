"""
Модуль работы со страницами пользователя.

Таблица pages хранит текст, медиа и кнопки для каждого экрана.
Кнопки хранятся в двух JSON-полях:
  - buttons_default — дефолты разработчика (обновляются только миграциями)
  - buttons_custom — кастомизация админа (обновляется через админ-панель)
Функции *_default вызываются ТОЛЬКО из миграций.
"""
import json
import logging
from typing import Optional, List, Dict, Any
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_page',
    'update_page_custom',
    'upsert_page_defaults',
]


def get_page(page_key: str) -> Optional[Dict[str, Any]]:
    """
    Возвращает данные страницы из таблицы pages.

    Args:
        page_key: Ключ страницы

    Returns:
        Словарь с полями таблицы или None если страница не найдена
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM pages WHERE page_key = ?",
            (page_key,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_page_custom(
    page_key: str,
    text: Optional[str] = None,
    image: Optional[str] = None,
    media_type: Optional[str] = None,
    buttons: Optional[str] = None,
) -> None:
    """
    Обновляет кастомные поля страницы.
    НЕ трогает *_default поля.

    Args:
        page_key: Ключ страницы
        text: Кастомный текст (None = не менять)
        image: Кастомный file_id изображения (None = не менять)
        buttons: Кастомный JSON кнопок (None = не менять)
    """
    # Собираем только переданные поля
    updates = []
    params = []
    if text is not None:
        updates.append("text_custom = ?")
        params.append(text)
    if image is not None:
        updates.append("image_custom = ?")
        params.append(image)
        if image:
            updates.append("media_type_custom = ?")
            params.append(media_type if media_type in {'photo', 'video', 'animation'} else 'photo')
        else:
            updates.append("media_type_custom = NULL")
    if buttons is not None:
        updates.append("buttons_custom = ?")
        params.append(buttons)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(page_key)

    with get_db() as conn:
        conn.execute(
            f"UPDATE pages SET {', '.join(updates)} WHERE page_key = ?",
            params
        )
    logger.info(f"Кастомные данные страницы обновлены: {page_key}")


def upsert_page_defaults(
    page_key: str,
    text: str,
    image: Optional[str],
    buttons: str,
    media_type: Optional[str] = None,
) -> None:
    """
    Вставляет или обновляет ТОЛЬКО дефолтные поля страницы.
    Вызывается ИСКЛЮЧИТЕЛЬНО из миграций!
    НИКОГДА не трогает *_custom поля.

    Args:
        page_key: Ключ страницы
        text: Дефолтный текст (HTML)
        image: Дефолтный file_id медиа (или None)
        media_type: Тип дефолтного медиа: photo, video или animation
        buttons: JSON-строка массива кнопок
    """
    normalized_media_type = media_type if media_type in {'photo', 'video', 'animation'} else None
    if image and normalized_media_type is None:
        normalized_media_type = 'photo'

    with get_db() as conn:
        # Пробуем вставить новую запись
        conn.execute(
            """
            INSERT OR IGNORE INTO pages (page_key, text_default, image_default, media_type_default, buttons_default)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_key, text, image, normalized_media_type, buttons)
        )
        # Обновляем *_default поля (для уже существующих записей)
        conn.execute(
            """
            UPDATE pages
            SET text_default    = ?,
                image_default   = ?,
                media_type_default = ?,
                buttons_default = ?
            WHERE page_key = ?
            """,
            (text, image, normalized_media_type, buttons, page_key)
        )
    logger.info(f"Дефолты страницы обновлены: {page_key}")
