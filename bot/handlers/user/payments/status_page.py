"""Page-backed экраны статуса проверки платежа."""
from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.text import safe_edit_or_send
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

PAYMENT_STATUS_PAGE_KEY = 'payment_status'


def default_payment_status_page_text() -> str:
    """Дефолтный текст экрана статуса платежа."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


def build_payment_status_page_context(
    *,
    title_html: str,
    body_html: str,
    hint_text: str = '',
    payment_provider_title: str = '',
) -> dict[str, Any]:
    """Собирает context для page-backed статуса платежа."""
    context: dict[str, Any] = {
        'payment_provider_title_html': title_html,
        'payment_instruction_html': body_html,
        'payment_hint_text': hint_text,
    }
    if payment_provider_title:
        context['payment_provider_title'] = payment_provider_title
    return context


def render_payment_status_page_text(context: dict[str, Any]) -> str:
    try:
        from bot.utils.page_renderer import render_page_text

        text = render_page_text(PAYMENT_STATUS_PAGE_KEY, context=context)
        if text is not None:
            return text
    except Exception as e:
        logger.warning("Не удалось отрендерить страницу %s: %s", PAYMENT_STATUS_PAGE_KEY, e)

    from bot.utils.placeholders import apply_page_placeholders

    fallback_context = {'page_key': PAYMENT_STATUS_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_payment_status_page_text(),
        context=fallback_context,
        mode='html',
    ) or '(пусто)'


def _runtime_rows(markup: Optional[InlineKeyboardMarkup]) -> Optional[list[list[InlineKeyboardButton]]]:
    return getattr(markup, 'inline_keyboard', None) if markup else None


def _message_viewer_id(message) -> Optional[int]:
    user = getattr(message, 'from_user', None)
    if user and not getattr(user, 'is_bot', False):
        return user.id
    chat = getattr(message, 'chat', None)
    if chat and getattr(chat, 'type', None) == 'private':
        return chat.id
    return None


def _message_bot_username(message) -> str:
    bot = getattr(message, 'bot', None)
    return (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )


def build_payment_status_reply_markup(
    context: dict[str, Any],
    runtime_rows: Optional[list[list[InlineKeyboardButton]]],
) -> Optional[InlineKeyboardMarkup]:
    """Собирает клавиатуру страницы статуса платежа + runtime-кнопки."""
    try:
        from bot.utils.page_renderer import build_page_keyboard

        markup = build_page_keyboard(
            PAYMENT_STATUS_PAGE_KEY,
            context=context,
            append_buttons=runtime_rows,
        )
        if markup is not None:
            return markup
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру %s: %s", PAYMENT_STATUS_PAGE_KEY, e)

    if runtime_rows:
        return InlineKeyboardMarkup(inline_keyboard=runtime_rows)
    return None


def remember_payment_status_page_context(
    telegram_id: Optional[int],
    message,
    context: dict[str, Any],
    runtime_rows: Optional[list[list[InlineKeyboardButton]]],
) -> None:
    """Сохраняет экран статуса платежа для контекстной команды /yaa."""
    if telegram_id not in ADMIN_IDS or message is None:
        return
    try:
        from bot.services.page_context import remember_page_context

        render_context = {'page_key': PAYMENT_STATUS_PAGE_KEY}
        render_context.update(context)
        remember_page_context(
            telegram_id,
            page_key=PAYMENT_STATUS_PAGE_KEY,
            message=message,
            context=render_context,
            append_buttons=runtime_rows,
        )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст payment_status для /yaa: %s", e)


async def show_payment_status_page(
    message,
    *,
    context: dict[str, Any],
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    force_new: bool = False,
    send_func=None,
):
    """Показывает page-backed статус платежа."""
    render_context = dict(context)
    viewer_id = _message_viewer_id(message)
    if viewer_id:
        render_context.setdefault('telegram_id', viewer_id)
    bot_username = _message_bot_username(message)
    if bot_username:
        render_context.setdefault('bot_username', bot_username)

    runtime_rows = _runtime_rows(reply_markup)
    sender = send_func or safe_edit_or_send
    rendered_message = await sender(
        message,
        render_payment_status_page_text(render_context),
        reply_markup=build_payment_status_reply_markup(render_context, runtime_rows),
        force_new=force_new,
    )
    remember_payment_status_page_context(
        viewer_id,
        rendered_message,
        render_context,
        runtime_rows,
    )
    return rendered_message


async def show_payment_status_message(
    message,
    *,
    title_html: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    hint_text: str = '',
    payment_provider_title: str = '',
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    force_new: bool = False,
    send_func=None,
):
    """Показывает типовой page-backed статус платежа по заголовку и тексту."""
    if body_html is None:
        from bot.utils.text import escape_html

        body_html = escape_html('' if body_text is None else str(body_text))

    return await show_payment_status_page(
        message,
        context=build_payment_status_page_context(
            title_html=title_html,
            body_html=body_html,
            hint_text=hint_text,
            payment_provider_title=payment_provider_title,
        ),
        reply_markup=reply_markup,
        force_new=force_new,
        send_func=send_func,
    )


async def show_payment_unavailable_status(
    message,
    reason: str,
    *,
    payment_provider_title: str = '',
    send_func=None,
):
    """Показывает типовой статус недоступного способа оплаты."""
    from bot.keyboards.admin import home_only_kb

    return await show_payment_status_message(
        message,
        title_html='⚠️ <b>Способ оплаты недоступен</b>',
        body_text=reason,
        payment_provider_title=payment_provider_title,
        reply_markup=home_only_kb(),
        send_func=send_func,
    )


async def show_payment_configuration_status(
    message,
    *,
    title_html: str = '❌ <b>Ошибка настройки платежей</b>',
    body_html: str | None = None,
    body_text: str | None = None,
    payment_provider_title: str = '',
    send_func=None,
):
    """Показывает типовой статус ошибки настройки платёжного способа."""
    from bot.keyboards.admin import home_only_kb

    return await show_payment_status_message(
        message,
        title_html=title_html,
        body_html=body_html,
        body_text=body_text,
        payment_provider_title=payment_provider_title,
        reply_markup=home_only_kb(),
        send_func=send_func,
    )


async def rerender_payment_status_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённый экран статуса платежа после изменения через /yaa."""
    context = dict(page_context.context or {})
    if not context:
        return False

    rendered_message = await safe_edit_or_send(
        page_context.message,
        render_payment_status_page_text(context),
        reply_markup=build_payment_status_reply_markup(context, page_context.append_buttons),
    )
    remember_payment_status_page_context(
        viewer_id,
        rendered_message,
        context,
        page_context.append_buttons,
    )
    return True
