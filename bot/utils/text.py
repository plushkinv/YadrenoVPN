from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation, LinkPreviewOptions
from aiogram.exceptions import TelegramBadRequest
from typing import Literal, Optional, Union
from html import escape as escape_attr
from html.parser import HTMLParser
import logging

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def escape_html(text: str) -> str:
    """Escaping special characters for HTML parse_mode."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _TelegramHtmlTruncator(HTMLParser):
    """Trims HTML at visible characters and closes open tags."""

    def __init__(self, limit: int):
        super().__init__(convert_charrefs=False)
        self.limit = max(0, limit)
        self.remaining = self.limit
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.truncated = False

    def _can_append(self) -> bool:
        return not self.truncated and self.remaining > 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self._can_append():
            return
        self.parts.append(self.get_starttag_text() or self._build_start_tag(tag, attrs))
        self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self._can_append():
            return
        self.parts.append(self.get_starttag_text() or self._build_start_tag(tag, attrs, close=True))

    def handle_endtag(self, tag: str) -> None:
        if self.truncated:
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not data or self.truncated:
            return
        if len(data) <= self.remaining:
            self.parts.append(escape_html(data))
            self.remaining -= len(data)
            return
        self.parts.append(escape_html(data[:self.remaining]))
        self.remaining = 0
        self.truncated = True

    def handle_entityref(self, name: str) -> None:
        self._append_charref(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append_charref(f"&#{name};")

    def _append_charref(self, value: str) -> None:
        if not self._can_append():
            self.truncated = True
            return
        self.parts.append(value)
        self.remaining -= 1

    def _build_start_tag(self, tag: str, attrs, close: bool = False) -> str:
        attr_text = ''.join(f' {name}="{escape_attr(str(value), quote=True)}"' for name, value in attrs)
        suffix = " /" if close else ""
        return f"<{tag}{attr_text}{suffix}>"

    def get_html(self) -> str:
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return ''.join(self.parts)


def truncate_html_for_telegram(text: Optional[str], limit: int) -> str:
    """
    Trims HTML message according to Telegram limit.

    The limit is calculated based on visible characters after HTML parsing. Tags and HTML Entities
    are saved, open tags are closed automatically. No extras. messages,
    no suffixes or ellipses are added.
    """
    if not text:
        return ""

    truncator = _TelegramHtmlTruncator(limit)
    truncator.feed(str(text))
    return truncator.get_html()


def prepare_telegram_text(text: Optional[str], *, has_media: bool = False) -> str:
    """Returns text trimmed to fit the limit of a regular message or caption."""
    limit = TELEGRAM_CAPTION_LIMIT if has_media else TELEGRAM_TEXT_LIMIT
    return truncate_html_for_telegram(text, limit)


def prepare_telegram_method(method):
    """
    Trims the text/caption fields of the aiogram method before sending to Telegram.

    This is insurance for direct calls bot.send_message/send_photo/send_video/
    send_animation, which do not go through safe_edit_or_send().
    """
    updates = {}

    text = getattr(method, 'text', None)
    if isinstance(text, str):
        updates['text'] = prepare_telegram_text(text, has_media=False)

    caption = getattr(method, 'caption', None)
    if isinstance(caption, str):
        updates['caption'] = prepare_telegram_text(caption, has_media=True)

    media = getattr(method, 'media', None)
    media_caption = getattr(media, 'caption', None)
    if isinstance(media_caption, str):
        updates['media'] = media.model_copy(update={
            'caption': prepare_telegram_text(media_caption, has_media=True)
        })

    if not updates:
        return method
    return method.model_copy(update=updates)


def get_message_text_for_storage(
    message: Message,
    text_type: Literal['html', 'plain'] = 'html'
) -> str:
    """Extracts text from a message to be stored in the database.
    
    Supports both regular text messages (html_text/text),
    and media messages (html_caption/caption).
    
    Args:
        text_type: 'html' - texts with formatting (uses html_text/html_caption),
                   'plain' - technical values (URL, secrets, numbers).
    """
    if text_type == 'html':
        # html_text preserves user formatting in HTML tags
        if message.html_text:
            return message.html_text.strip()
        if message.text:
            return message.text.strip()
        if hasattr(message, 'html_caption') and message.html_caption:
            return message.html_caption.strip()
        if message.caption:
            return message.caption.strip()
        return ""
    else:  # plain
        if message.text:
            return message.text.strip()
        if message.caption:
            return message.caption.strip()
        return ""


SUPPORTED_MEDIA_TYPES = {'photo', 'video', 'animation'}


def normalize_media_type(media_type: Optional[str], *, media: object = None) -> Optional[str]:
    """Returns the supported Telegram media type."""
    if media is None:
        return None
    return media_type if media_type in SUPPORTED_MEDIA_TYPES else 'photo'


def _input_media_for_type(media: object, media_type: str, caption: str):
    if media_type == 'video':
        return InputMediaVideo(media=media, caption=caption, parse_mode='HTML')
    if media_type == 'animation':
        return InputMediaAnimation(media=media, caption=caption, parse_mode='HTML')
    return InputMediaPhoto(media=media, caption=caption, parse_mode='HTML')


async def _answer_media(message: Message, media: object, media_type: str, text: str, reply_markup=None) -> Message:
    if media_type == 'video':
        return await message.answer_video(
            video=media,
            caption=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
    if media_type == 'animation':
        return await message.answer_animation(
            animation=media,
            caption=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
    return await message.answer_photo(
        photo=media,
        caption=text,
        reply_markup=reply_markup,
        parse_mode='HTML',
    )


async def send_media_or_text(
    bot,
    *,
    chat_id: int,
    text: str,
    reply_markup=None,
    media: Optional[Union[str, object]] = None,
    media_type: Optional[str] = None,
) -> Message:
    """Sends a regular message or media with an HTML caption."""
    normalized_media_type = normalize_media_type(media_type, media=media)
    text = prepare_telegram_text(text, has_media=media is not None)

    if media is None:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
    if normalized_media_type == 'video':
        return await bot.send_video(
            chat_id=chat_id,
            video=media,
            caption=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
    if normalized_media_type == 'animation':
        return await bot.send_animation(
            chat_id=chat_id,
            animation=media,
            caption=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
    return await bot.send_photo(
        chat_id=chat_id,
        photo=media,
        caption=text,
        reply_markup=reply_markup,
        parse_mode='HTML',
    )


async def safe_edit_or_send(
    message: Message,
    text: str = None,
    reply_markup=None,
    photo: Optional[Union[str, object]] = None,
    media: Optional[Union[str, object]] = None,
    media_type: Optional[str] = None,
    show_web_page_preview: bool = False,
    force_new: bool = False,
) -> Message:
    """Universal function for editing/sending messages.
    
    parse_mode='HTML' is hardcoded internally—the calling code cannot pass a different mode.
    
    Automatically detects the current message type and target format,
    choosing the optimal strategy:
    
    - text → text: edit_text
    - media → text: delete + answer (text)
    - text → media: delete + send desired media type
    - media → media: edit_media + edit_caption
    
    Handles Telegram API errors:
    - 'there is no text in the message to edit'
    - 'message is not modified'
    
    Args:
        message: Message for editing
        text: Message text (or caption for media)
        reply_markup: Keyboard
        photo: Photo (file_id, URL or InputFile). Old compatible alias for media
        media: Media (file_id, URL or InputFile)
        media_type: Media type: photo, video or animation
    """
    if media is None and photo is not None:
        media = photo
        media_type = media_type or 'photo'

    normalized_media_type = normalize_media_type(media_type, media=media)
    is_current_media = bool(message.photo or message.video or message.document or message.animation)
    want_media = media is not None
    text = prepare_telegram_text(text, has_media=want_media)
    
    # Disable link previews by default. Enable only if show_web_page_preview=True
    link_preview = LinkPreviewOptions(is_disabled=not show_web_page_preview)
    
    # If requested force_new, we simply send a new message without deleting the old one
    if force_new:
        if want_media:
            return await _answer_media(message, media, normalized_media_type, text, reply_markup)
        else:
            return await message.answer(
                text=text, reply_markup=reply_markup, parse_mode='HTML',
                link_preview_options=link_preview
            )
            
    try:
        if want_media and is_current_media:
            # Media → Media: edit media + caption
            input_media = _input_media_for_type(media, normalized_media_type, text)
            result = await message.edit_media(media=input_media, reply_markup=reply_markup)
            return result
            
        elif want_media and not is_current_media:
            # Text → Media: remove text, send desired media type
            try:
                await message.delete()
            except Exception:
                pass
            return await _answer_media(message, media, normalized_media_type, text, reply_markup)
            
        elif not want_media and not is_current_media:
            # Text → Text: normal editing
            return await message.edit_text(
                text=text, reply_markup=reply_markup, parse_mode='HTML',
                link_preview_options=link_preview
            )
            
        else:
            # Media → Text: remove media, send text
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer(
                text=text, reply_markup=reply_markup, parse_mode='HTML',
                link_preview_options=link_preview
            )
            
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        
        if 'message is not modified' in error_msg:
            # The content has not changed - ignore it
            logger.debug('Сообщение не изменено, пропускаем')
            return message
            
        if 'there is no text in the message' in error_msg or \
           'message can\'t be edited' in error_msg or \
           'there is no media in the message' in error_msg:
            # Fallback: delete and send again
            try:
                await message.delete()
            except Exception:
                pass
            if want_media:
                return await _answer_media(message, media, normalized_media_type, text, reply_markup)
            else:
                return await message.answer(
                    text=text, reply_markup=reply_markup, parse_mode='HTML',
                    link_preview_options=link_preview
                )
        raise
