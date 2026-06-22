"""
Универсальный редактор сообщений для админ-панели.

Утилиты для хранения, отображения и редактирования сообщений.
Сообщение = текст + опциональное медиа (фото, видео, гифка).
В БД хранится JSON: {text, photo_file_id, video_file_id, animation_file_id}.
Обратная совместимость: если в БД старая строка (не JSON) — возвращаем {'text': строка}.
"""
import json
import logging
from typing import Optional, List

from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from database.requests import get_setting, set_setting

logger = logging.getLogger(__name__)

# Допустимые типы медиа
ALL_MEDIA_TYPES = ['text', 'photo', 'video', 'animation']


# Ключи, которые хранятся в новой таблице pages
PAGE_KEYS = (
    'main',
    'help',
    'trial',
    'prepayment',
    'renew_payment',
    'my_keys',
    'my_keys_empty',
    'key_details',
    'key_show_unconfigured',
    'renew_payment_unavailable',
    'key_replace_server_select',
    'key_replace_inbound_select',
    'key_replace_confirm',
    'key_rename_prompt',
    'new_key_server_select',
    'new_key_inbound_select',
    'new_key_no_servers',
    'referral',
    'key_delivery',
)


def _page_image_value(row: dict) -> Optional[str]:
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


def _page_media_type_value(row: dict, image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    if row.get('image_custom') is not None:
        media_type = row.get('media_type_custom')
    else:
        media_type = row.get('media_type_default')
    return media_type if media_type in {'photo', 'video', 'animation'} else 'photo'


def _media_from_parts(data: dict) -> tuple[Optional[str], Optional[str]]:
    media_type = data.get('media_type')
    media_file_id = data.get('media_file_id')
    if media_file_id and media_type in {'photo', 'video', 'animation'}:
        return media_file_id, media_type
    if data.get('animation_file_id'):
        return data.get('animation_file_id'), 'animation'
    if data.get('video_file_id'):
        return data.get('video_file_id'), 'video'
    if data.get('photo_file_id'):
        return data.get('photo_file_id'), 'photo'
    return None, None


def _with_media_fields(text: str, media_file_id: Optional[str], media_type: Optional[str]) -> dict:
    media_type = media_type if media_type in {'photo', 'video', 'animation'} and media_file_id else None
    return {
        'text': text,
        'photo_file_id': media_file_id if media_type == 'photo' else None,
        'video_file_id': media_file_id if media_type == 'video' else None,
        'animation_file_id': media_file_id if media_type == 'animation' else None,
        'media_file_id': media_file_id if media_type else None,
        'media_type': media_type,
    }


def _normalize_message_data(data: dict, default_text: str = '') -> dict:
    """Дополняет данные сообщения ожидаемыми ключами для обратной совместимости."""
    media_file_id, media_type = _media_from_parts(data)
    return _with_media_fields(data.get('text', default_text), media_file_id, media_type)


def get_message_data(key: str, default_text: str = '') -> dict:
    """
    Загружает данные сообщения.
    
    Для ключей из PAGE_KEY_MAP — читает из таблицы pages.
    Для остальных — из settings (обратная совместимость).
    
    Args:
        key: Ключ настройки
        default_text: Текст по умолчанию если ключ не найден
        
    Returns:
        Словарь с ключами: text, photo_file_id, video_file_id, animation_file_id,
        media_file_id, media_type
    """
    if key in PAGE_KEYS:
        # Читаем из таблицы pages
        from database.requests import get_page
        row = get_page(key)
        if row:
            text = row.get('text_custom') or row.get('text_default') or default_text
            image = _page_image_value(row)
            media_type = _page_media_type_value(row, image)
            return _with_media_fields(text, image, media_type)
        return _normalize_message_data({'text': default_text})

    # Старая логика: settings
    raw = get_setting(key)
    
    if raw is None:
        return _normalize_message_data({'text': default_text})
    
    # Пробуем распарсить как JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and 'text' in data:
            return _normalize_message_data(data, default_text)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Старый формат — просто строка
    return _normalize_message_data({'text': raw})


def save_message_data(key: str, message: Message, allowed_types: Optional[List[str]] = None) -> dict:
    """
    Извлекает данные из входящего Telegram-сообщения и сохраняет.
    
    Для ключей из PAGE_KEY_MAP — сохраняет в таблицу pages
    (text_custom, image_custom, media_type_custom).
    Для остальных — в settings как JSON (обратная совместимость).
    
    Использует get_message_text_for_storage() для текста (правило ТЗ).
    Медиа не скачиваем — сохраняем только file_id.
    
    Args:
        key: Ключ настройки
        message: Входящее сообщение от Telegram
        allowed_types: Допустимые типы ['text', 'photo', 'video', 'animation']
        
    Returns:
        Сохранённый словарь данных
    """
    from bot.utils.text import get_message_text_for_storage

    current_data = get_message_data(key)
    current_media_file_id, current_media_type = _media_from_parts(current_data)
    data = _with_media_fields(
        current_data.get('text', ''),
        current_media_file_id,
        current_media_type,
    )
    
    # Определяем тип сообщения и извлекаем медиа
    if message.animation:
        data.update(_with_media_fields(data.get('text', ''), message.animation.file_id, 'animation'))
        # Для медиа используем caption
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.video:
        data.update(_with_media_fields(data.get('text', ''), message.video.file_id, 'video'))
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.photo:
        data.update(_with_media_fields(data.get('text', ''), message.photo[-1].file_id, 'photo'))
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.text:
        data['text'] = get_message_text_for_storage(message, 'html')
    
    # Проверяем, относится ли ключ к таблице pages
    if key in PAGE_KEYS:
        # Сохраняем в таблицу pages
        from database.requests import update_page_custom
        if message.photo or message.video or message.animation:
            update_page_custom(
                key,
                text=data['text'] or None,
                image=data['media_file_id'],
                media_type=data['media_type'],
            )
        else:
            update_page_custom(key, text=data['text'] or None)
        logger.info(f"Сообщение сохранено в pages: {key}")
    else:
        # Старая логика: settings
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Сообщение сохранено в settings: {key}")
    
    return data


def delete_message_media(key: str) -> dict:
    """
    Явно удаляет медиа редактируемого сообщения, не меняя текст.

    Для settings очищает media-поля в JSON. Для pages записывает пустую строку
    в image_custom, чтобы не подтягивалось дефолтное медиа.
    """
    data = get_message_data(key)
    data.update(_with_media_fields(data.get('text', ''), None, None))

    if key in PAGE_KEYS:
        from database.requests import update_page_custom
        update_page_custom(key, image='')
        logger.info(f"Медиа удалено из pages: {key}")
    else:
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Медиа удалено из settings: {key}")

    return data


def delete_message_photo(key: str) -> dict:
    """Обратная совместимость для старых импортов."""
    return delete_message_media(key)


def detect_message_type(message: Message) -> str:
    """
    Определяет тип входящего сообщения.
    
    Returns:
        'animation', 'video', 'photo' или 'text'
    """
    if message.animation:
        return 'animation'
    if message.video:
        return 'video'
    if message.photo:
        return 'photo'
    return 'text'


def editor_kb(
    back_callback: str,
    has_help: bool = False,
    can_delete_photo: bool = False,
    can_delete_media: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура редактора сообщений.
    
    Раскладка:
    [⬅️ Назад]  [🈴 На главную]
    [🗑 Удалить медиа]  # если медиа есть
    [📝 Отправьте новое сообщение ⬇️]
    
    Args:
        back_callback: callback_data для кнопки «Назад»
        has_help: Есть ли текст справки (меняет поведение кнопки)
        can_delete_photo: Совместимость со старым названием флага удаления медиа
        can_delete_media: Показывать ли кнопку удаления текущего медиа
    """
    if can_delete_photo:
        can_delete_media = True

    builder = InlineKeyboardBuilder()
    
    # Верхний ряд: Назад + На главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start"),
    )

    if can_delete_media:
        builder.row(
            InlineKeyboardButton(
                text="🗑 Удалить медиа",
                callback_data="msg_editor_delete_media"
            )
        )
    
    # Нижний ряд: кнопка ввода
    if has_help:
        # Кнопка показывает справку перед вводом
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="msg_editor_show_help"
            )
        )
    else:
        # Кнопка-заглушка (просто визуальный индикатор, редактор уже ждёт ввод)
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="msg_editor_noop_alert"
            )
        )
    
    return builder.as_markup()


def editor_help_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана справки редактора."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="msg_editor_back_to_preview")
    )
    return builder.as_markup()


async def send_editor_message(
    message: Message,
    key: str = None,
    data: dict = None,
    default_text: str = '',
    reply_markup=None,
    text_override: str = None,
) -> Message:
    """Универсальная отправка/редактирование сообщения из редактора.
    
    Единый контракт: все тексты из редактора хранятся в БД в формате
    HTML и отправляются ТОЛЬКО через эту функцию с parse_mode='HTML'.
    
    Внутри делегирует вызов в safe_edit_or_send() для обработки
    переходов текст↔медиа и ошибок Telegram API.
    
    Args:
        message: Сообщение для редактирования или ответа
        key: Ключ настройки в таблице settings (загружает через get_message_data)
        data: Уже загруженный словарь данных (приоритет над key)
        default_text: Текст по умолчанию если ключ не найден
        reply_markup: Клавиатура
        text_override: Подготовленный текст (заменяет data['text']).
            Используется когда нужно подставить плейсхолдеры (%тарифы%, %ключ% и т.д.)
            Важно: все динамические значения должны быть экранированы через escape_html()
            
    Returns:
        Объект Message после отправки/редактирования
    """
    from bot.utils.text import safe_edit_or_send
    
    # Загружаем данные из БД если не переданы явно
    if data is None:
        if key is None:
            raise ValueError("Нужно передать key или data")
        data = get_message_data(key, default_text)
    
    # Определяем текст
    text = text_override if text_override is not None else (data.get('text', '') or default_text)
    if not text:
        text = '(пусто)'
    
    media_file_id, media_type = _media_from_parts(data)
    
    return await safe_edit_or_send(
        message, text,
        reply_markup=reply_markup,
        media=media_file_id,
        media_type=media_type,
    )
