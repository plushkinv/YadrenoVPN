"""
Universal message editor for the admin panel.

Utilities for storing, displaying and editing messages.
Message = text + optional media (photo, video, gif).
The database stores JSON: {text, photo_file_id, video_file_id, animation_file_id}.
Backward compatibility: if the database contains an old string (not JSON), we return {'text': string}.
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

# Acceptable media types
ALL_MEDIA_TYPES = ['text', 'photo', 'video', 'animation']


# Keys that are stored in the new pages table
PAGE_KEYS = (
    'main',
    'help',
    'trial',
    'access_blocked',
    'prepayment',
    'prepayment_unavailable',
    'renew_payment',
    'my_keys',
    'my_keys_empty',
    'key_details',
    'key_status',
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
    'qr_payment',
    'crypto_payment',
    'balance_payment',
    'demo_payment',
    'payment_tariff_select',
    'payment_status',
    'support_start',
    'support_status',
    'promo_enter',
    'promo_status',
    'show_id',
)


def _is_page_key(key: str) -> bool:
    if key in PAGE_KEYS:
        return True

    from bot.utils.custom_pages import custom_page_exists

    return custom_page_exists(key)


def _page_image_value(row: dict) -> Optional[str]:
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
    """Supplements these messages with the expected keys for backward compatibility."""
    media_file_id, media_type = _media_from_parts(data)
    return _with_media_fields(data.get('text', default_text), media_file_id, media_type)


def get_message_data(key: str, default_text: str = '') -> dict:
    """
    Loads message data.
    
    For keys from PAGE_KEY_MAP - reads from the pages table.
    For others - from settings (backward compatibility).
    
    Args:
        key: Setting key
        default_text: Default text if key not found
        
    Returns:
        Dictionary with keys: text, photo_file_id, video_file_id, animation_file_id,
        media_file_id, media_type
    """
    if _is_page_key(key):
        # Reading from the pages table
        from database.requests import get_page
        row = get_page(key)
        if row:
            text = row.get('text_custom') or row.get('text_default') or default_text
            image = _page_image_value(row)
            media_type = _page_media_type_value(row, image)
            return _with_media_fields(text, image, media_type)
        return _normalize_message_data({'text': default_text})

    # Old logic: settings
    raw = get_setting(key)
    
    if raw is None:
        return _normalize_message_data({'text': default_text})
    
    # Trying to parse it as JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and 'text' in data:
            return _normalize_message_data(data, default_text)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # The old format is just a string
    return _normalize_message_data({'text': raw})


def save_message_data(key: str, message: Message, allowed_types: Optional[List[str]] = None) -> dict:
    """
    Extracts data from an incoming Telegram message and saves it.
    
    For keys from PAGE_KEY_MAP - saves to the pages table
    (text_custom, image_custom, media_type_custom).
    For others - in settings as JSON (backward compatibility).
    
    Uses get_message_text_for_storage() for text (TK rule).
    We don’t download media - we save only file_id.
    
    Args:
        key: Setting key
        message: Incoming message from Telegram
        allowed_types: Allowed types ['text', 'photo', 'video', 'animation']
        
    Returns:
        Saved data dictionary
    """
    from bot.utils.text import get_message_text_for_storage

    current_data = get_message_data(key)
    current_media_file_id, current_media_type = _media_from_parts(current_data)
    data = _with_media_fields(
        current_data.get('text', ''),
        current_media_file_id,
        current_media_type,
    )
    
    # Determine the message type and extract media
    if message.animation:
        data.update(_with_media_fields(data.get('text', ''), message.animation.file_id, 'animation'))
        # For media we use caption
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.video:
        data.update(_with_media_fields(data.get('text', ''), message.video.file_id, 'video'))
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.photo:
        data.update(_with_media_fields(data.get('text', ''), message.photo[-1].file_id, 'photo'))
        data['text'] = get_message_text_for_storage(message, 'html') if message.caption else ''
    elif message.text:
        data['text'] = get_message_text_for_storage(message, 'html')
    
    # Checking whether the key belongs to the pages table
    if _is_page_key(key):
        # Save to the pages table
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
        # Old logic: settings
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Сообщение сохранено в settings: {key}")
    
    return data


def delete_message_media(key: str) -> dict:
    """
    Explicitly removes the media of the edited message without changing the text.

    For settings, clears media fields in JSON. For pages writes an empty string
    in image_custom so that the default media is not pulled up.
    """
    data = get_message_data(key)
    data.update(_with_media_fields(data.get('text', ''), None, None))

    if _is_page_key(key):
        from database.requests import update_page_custom
        update_page_custom(key, image='')
        logger.info(f"Медиа удалено из pages: {key}")
    else:
        set_setting(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"Медиа удалено из settings: {key}")

    return data


def delete_message_photo(key: str) -> dict:
    """Backward compatibility for old imports."""
    return delete_message_media(key)


def detect_message_type(message: Message) -> str:
    """
    Determines the type of incoming message.
    
    Returns:
        'animation', 'video', 'photo' or 'text'
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
    Message editor keyboard.
    
    Layout:
    [⬅️ Back] [🈴 Home]
    [🗑 Remove media] # if there is media
    [📝 Send a new message ⬇️]
    
    Args:
        back_callback: callback_data for the back button
        has_help: Whether there is help text (changes the behavior of the button)
        can_delete_photo: Compatible with old media delete flag name
        can_delete_media: Whether to show a button to delete the current media
    """
    if can_delete_photo:
        can_delete_media = True

    builder = InlineKeyboardBuilder()
    
    # Top row: Back + Home
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
    
    # Bottom row: enter button
    if has_help:
        # The button shows help before entering
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="msg_editor_show_help"
            )
        )
    else:
        # Placeholder button (just a visual indicator, the editor is already waiting for input)
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="msg_editor_noop_alert"
            )
        )
    
    return builder.as_markup()


def editor_help_kb() -> InlineKeyboardMarkup:
    """Keyboard for editor help screen."""
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
    """Universal sending/editing of a message from the editor.
    
    Single contract: all texts from the editor are stored in the database in the format
    HTML and are sent ONLY through this function with parse_mode='HTML'.
    
    Internally delegates a call to safe_edit_or_send() to handle
    text↔media transitions and Telegram API errors.
    
    Args:
        message: Message to edit or reply
        key: Settings key in the settings table (loads via get_message_data)
        data: Already loaded data dictionary (priority over key)
        default_text: Default text if key not found
        reply_markup: Keyboard
        text_override: Prepared text (replaces data['text']).
            Used when editor placeholders must be substituted before rendering.
            Important: all dynamic values must be escaped via escape_html()
            
    Returns:
        Message object after sending/editing
    """
    from bot.utils.text import safe_edit_or_send
    
    # Load data from the database if not passed explicitly
    if data is None:
        if key is None:
            raise ValueError("Нужно передать key или data")
        data = get_message_data(key, default_text)
    
    # Defining the text
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
