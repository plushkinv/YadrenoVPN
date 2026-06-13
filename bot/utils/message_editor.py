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
    Возвращает итоговую картинку страницы.

    Для pages.image_custom используется три состояния:
    - NULL: использовать image_default;
    - пустая строка: админ явно отключил картинку;
    - file_id: использовать кастомную картинку.
    """
    custom_image = row.get('image_custom')
    if custom_image is not None:
        return custom_image or None
    return row.get('image_default')


def _normalize_message_data(data: dict, default_text: str = '') -> dict:
    """Дополняет данные сообщения ожидаемыми ключами для обратной совместимости."""
    return {
        'text': data.get('text', default_text),
        'photo_file_id': data.get('photo_file_id'),
        'video_file_id': data.get('video_file_id'),
        'animation_file_id': data.get('animation_file_id'),
    }


def get_message_data(key: str, default_text: str = '') -> dict:
    """
    Загружает данные сообщения.
    
    Для ключей из PAGE_KEY_MAP — читает из таблицы pages.
    Для остальных — из settings (обратная совместимость).
    
    Args:
        key: Ключ настройки
        default_text: Текст по умолчанию если ключ не найден
        
    Returns:
        Словарь с ключами: text, photo_file_id, video_file_id, animation_file_id
    """
    if key in PAGE_KEYS:
        # Читаем из таблицы pages
        from database.requests import get_page
        row = get_page(key)
        if row:
            text = row.get('text_custom') or row.get('text_default') or default_text
            image = _page_image_value(row)
            return {
                'text': text,
                'photo_file_id': image,
                'video_file_id': None,
                'animation_file_id': None,
            }
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
    
    Для ключей из PAGE_KEY_MAP — сохраняет в таблицу pages (text_custom, image_custom).
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
    data = {
        'text': current_data.get('text', ''),
        'photo_file_id': current_data.get('photo_file_id'),
        'video_file_id': current_data.get('video_file_id'),
        'animation_file_id': current_data.get('animation_file_id'),
    }
    
    # Определяем тип сообщения и извлекаем медиа
    if message.animation:
        data['photo_file_id'] = None
        data['video_file_id'] = None
        data['animation_file_id'] = message.animation.file_id
        # Для медиа используем caption
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.video:
        data['photo_file_id'] = None
        data['video_file_id'] = message.video.file_id
        data['animation_file_id'] = None
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.photo:
        data['photo_file_id'] = message.photo[-1].file_id
        data['video_file_id'] = None
        data['animation_file_id'] = None
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.text:
        data['text'] = get_message_text_for_storage(message, 'html')
    
    # Проверяем, относится ли ключ к таблице pages
    if key in PAGE_KEYS:
        # Сохраняем в таблицу pages
        from database.requests import update_page_custom
        if message.photo:
            update_page_custom(key, text=data['text'] or None, image=data['photo_file_id'])
        else:
            update_page_custom(key, text=data['text'] or None)
        logger.info(f"Сообщение сохранено в pages: {key}")
    else:
        # Старая логика: settings
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Сообщение сохранено в settings: {key}")
    
    return data


def delete_message_photo(key: str) -> dict:
    """
    Явно удаляет картинку редактируемого сообщения, не меняя текст.

    Для settings очищает photo_file_id в JSON. Для pages записывает пустую строку
    в image_custom, чтобы не подтягивалась image_default.
    """
    data = get_message_data(key)
    data['photo_file_id'] = None

    if key in PAGE_KEYS:
        from database.requests import update_page_custom
        update_page_custom(key, image='')
        logger.info(f"Картинка удалена из pages: {key}")
    else:
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Картинка удалена из settings: {key}")

    return data


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
) -> InlineKeyboardMarkup:
    """
    Клавиатура редактора сообщений.
    
    Раскладка:
    [⬅️ Назад]  [🈴 На главную]
    [🗑 Удалить картинку]  # если картинка есть
    [📝 Отправьте новое сообщение ⬇️]
    
    Args:
        back_callback: callback_data для кнопки «Назад»
        has_help: Есть ли текст справки (меняет поведение кнопки)
        can_delete_photo: Показывать ли кнопку удаления текущей картинки
    """
    builder = InlineKeyboardBuilder()
    
    # Верхний ряд: Назад + На главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start"),
    )

    if can_delete_photo:
        builder.row(
            InlineKeyboardButton(
                text="🗑 Удалить картинку",
                callback_data="msg_editor_delete_photo"
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
    
    # Определяем медиа (приоритет: animation > video > photo)
    # safe_edit_or_send поддерживает только photo, для video/animation — фоллбэк на текст
    photo = data.get('photo_file_id')
    animation = data.get('animation_file_id')
    video = data.get('video_file_id')
    
    media_file_id = None
    if animation:
        text = f"{text}\n\n🎞 <i>(к сообщению прикреплена GIF)</i>"
    elif video:
        text = f"{text}\n\n🎬 <i>(к сообщению прикреплено видео)</i>"
    elif photo:
        media_file_id = photo
    
    return await safe_edit_or_send(
        message, text,
        reply_markup=reply_markup,
        photo=media_file_id,
    )
