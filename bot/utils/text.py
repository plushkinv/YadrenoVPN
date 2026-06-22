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
    """Экранирование спецсимволов для HTML parse_mode."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _TelegramHtmlTruncator(HTMLParser):
    """Обрезает HTML по видимым символам и закрывает открытые теги."""

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
    Обрезает HTML-сообщение по лимиту Telegram.

    Лимит считается по видимым символам после разбора HTML. Теги и HTML-сущности
    сохраняются, открытые теги закрываются автоматически. Никаких доп. сообщений,
    суффиксов или троеточий не добавляется.
    """
    if not text:
        return ""

    truncator = _TelegramHtmlTruncator(limit)
    truncator.feed(str(text))
    return truncator.get_html()


def prepare_telegram_text(text: Optional[str], *, has_media: bool = False) -> str:
    """Возвращает текст, подрезанный под лимит обычного сообщения или caption."""
    limit = TELEGRAM_CAPTION_LIMIT if has_media else TELEGRAM_TEXT_LIMIT
    return truncate_html_for_telegram(text, limit)


def prepare_telegram_method(method):
    """
    Подрезает поля text/caption у aiogram-метода перед отправкой в Telegram.

    Это страховка для прямых вызовов bot.send_message/send_photo/send_video/
    send_animation, которые не идут через safe_edit_or_send().
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
    """Извлекает текст из сообщения для сохранения в БД.
    
    Поддерживает как обычные текстовые сообщения (html_text/text),
    так и медиа-сообщения (html_caption/caption).
    
    Args:
        text_type: 'html' — тексты с форматированием (использует html_text/html_caption),
                   'plain' — технические значения (URL, секреты, числа).
    """
    if text_type == 'html':
        # html_text сохраняет форматирование пользователя в HTML-тегах
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
    """Возвращает поддерживаемый тип Telegram-медиа."""
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
    """Отправляет обычное сообщение или медиа с HTML caption."""
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
    """Универсальная функция редактирования/отправки сообщения.
    
    parse_mode='HTML' зашит внутри — вызывающий код не может передать другой режим.
    
    Автоматически определяет тип текущего сообщения и целевой формат,
    выбирая оптимальную стратегию:
    
    - текст → текст: edit_text
    - медиа → текст: удалить + answer (текст)
    - текст → медиа: удалить + отправить нужный тип медиа
    - медиа → медиа: edit_media + edit_caption
    
    Обрабатывает ошибки Telegram API:
    - 'there is no text in the message to edit'
    - 'message is not modified'
    
    Args:
        message: Сообщение для редактирования
        text: Текст сообщения (или caption для медиа)
        reply_markup: Клавиатура
        photo: Фото (file_id, URL или InputFile). Старый совместимый alias для media
        media: Медиа (file_id, URL или InputFile)
        media_type: Тип медиа: photo, video или animation
    """
    if media is None and photo is not None:
        media = photo
        media_type = media_type or 'photo'

    normalized_media_type = normalize_media_type(media_type, media=media)
    is_current_media = bool(message.photo or message.video or message.document or message.animation)
    want_media = media is not None
    text = prepare_telegram_text(text, has_media=want_media)
    
    # Отключаем превью ссылок по умолчанию. Включаем только если show_web_page_preview=True
    link_preview = LinkPreviewOptions(is_disabled=not show_web_page_preview)
    
    # Если requested force_new, просто отправляем новое сообщение без удаления старого
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
            # Медиа → Медиа: редактируем media + caption
            input_media = _input_media_for_type(media, normalized_media_type, text)
            result = await message.edit_media(media=input_media, reply_markup=reply_markup)
            return result
            
        elif want_media and not is_current_media:
            # Текст → Медиа: удаляем текст, отправляем нужный тип медиа
            try:
                await message.delete()
            except Exception:
                pass
            return await _answer_media(message, media, normalized_media_type, text, reply_markup)
            
        elif not want_media and not is_current_media:
            # Текст → Текст: обычное редактирование
            return await message.edit_text(
                text=text, reply_markup=reply_markup, parse_mode='HTML',
                link_preview_options=link_preview
            )
            
        else:
            # Медиа → Текст: удаляем медиа, отправляем текст
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
            # Содержимое не изменилось — игнорируем
            logger.debug('Сообщение не изменено, пропускаем')
            return message
            
        if 'there is no text in the message' in error_msg or \
           'message can\'t be edited' in error_msg or \
           'there is no media in the message' in error_msg:
            # Фоллбэк: удаляем и отправляем заново
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
