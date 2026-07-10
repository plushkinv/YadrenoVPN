"""Page-backed обёртка экранов выбора тарифа для способов оплаты."""
from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

PAYMENT_TARIFF_SELECT_PAGE_KEY = 'payment_tariff_select'


def default_payment_tariff_select_page_text() -> str:
    """Дефолтный текст экрана выбора тарифа оплаты."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


def _callback_bot_username(callback: CallbackQuery) -> str:
    bot = getattr(callback, 'bot', None)
    return (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )


def _runtime_rows(markup: Optional[InlineKeyboardMarkup]) -> Optional[list[list[InlineKeyboardButton]]]:
    return getattr(markup, 'inline_keyboard', None) if markup is not None else None


def build_payment_tariff_select_page_context(
    *,
    provider_title_html: str,
    instruction_html: str = 'Выберите тариф:',
    key_name: Optional[str] = None,
    hint_text: str = '',
    telegram_id: Optional[int] = None,
    bot_username: str = '',
) -> dict[str, Any]:
    """Собирает общий context для page-backed выбора тарифа."""
    context: dict[str, Any] = {
        'payment_provider_title_html': provider_title_html,
        'payment_key_line_html': (
            f"🔑 Ключ: <b>{escape_html(str(key_name))}</b>\n\n" if key_name else ''
        ),
        'payment_instruction_html': instruction_html,
        'payment_hint_text': hint_text,
    }
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


def render_payment_tariff_select_page_text(context: dict[str, Any]) -> str:
    try:
        from bot.utils.page_renderer import render_page_text

        text = render_page_text(PAYMENT_TARIFF_SELECT_PAGE_KEY, context=context)
        if text is not None:
            return text
    except Exception as e:
        logger.warning("Не удалось отрендерить страницу %s: %s", PAYMENT_TARIFF_SELECT_PAGE_KEY, e)

    from bot.utils.placeholders import apply_page_placeholders

    fallback_context = {'page_key': PAYMENT_TARIFF_SELECT_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_payment_tariff_select_page_text(),
        context=fallback_context,
        mode='html',
    ) or '(пусто)'


def build_payment_tariff_select_reply_markup(
    context: dict[str, Any],
    runtime_rows: Optional[list[list[InlineKeyboardButton]]],
) -> Optional[InlineKeyboardMarkup]:
    """Собирает клавиатуру страницы + runtime-кнопки тарифов."""
    try:
        from bot.utils.page_renderer import build_page_keyboard

        markup = build_page_keyboard(
            PAYMENT_TARIFF_SELECT_PAGE_KEY,
            context=context,
            append_buttons=runtime_rows,
        )
        if markup is not None:
            return markup
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру %s: %s", PAYMENT_TARIFF_SELECT_PAGE_KEY, e)

    if runtime_rows:
        return InlineKeyboardMarkup(inline_keyboard=runtime_rows)
    return None


def remember_payment_tariff_select_page_context(
    telegram_id: int,
    message,
    context: dict[str, Any],
    runtime_rows: Optional[list[list[InlineKeyboardButton]]],
) -> None:
    """Сохраняет экран выбора тарифа оплаты для контекстной команды /yaa."""
    if telegram_id not in ADMIN_IDS or message is None:
        return
    try:
        from bot.services.page_context import remember_page_context

        render_context = {'page_key': PAYMENT_TARIFF_SELECT_PAGE_KEY}
        render_context.update(context)
        remember_page_context(
            telegram_id,
            page_key=PAYMENT_TARIFF_SELECT_PAGE_KEY,
            message=message,
            context=render_context,
            append_buttons=runtime_rows,
        )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст payment_tariff_select для /yaa: %s", e)


async def show_payment_tariff_select_page(
    callback: CallbackQuery,
    *,
    context: dict[str, Any],
    runtime_markup: Optional[InlineKeyboardMarkup],
) -> None:
    """Показывает page-backed экран выбора тарифа с переданной runtime-клавиатурой."""
    render_context = dict(context)
    render_context.setdefault('telegram_id', callback.from_user.id)
    bot_username = _callback_bot_username(callback)
    if bot_username:
        render_context.setdefault('bot_username', bot_username)

    runtime_rows = _runtime_rows(runtime_markup)
    rendered_message = await safe_edit_or_send(
        callback.message,
        render_payment_tariff_select_page_text(render_context),
        reply_markup=build_payment_tariff_select_reply_markup(render_context, runtime_rows),
    )
    remember_payment_tariff_select_page_context(
        callback.from_user.id,
        rendered_message,
        render_context,
        runtime_rows,
    )


async def show_payment_no_tariffs_page(
    callback: CallbackQuery,
    *,
    provider_title_html: str,
    instruction_html: str,
    key_name: Optional[str] = None,
    back_callback: Optional[str] = None,
) -> None:
    """Показывает page-backed экран выбора тарифа без доступных тарифов."""
    from bot.keyboards.admin import back_and_home_kb, home_only_kb

    runtime_markup = back_and_home_kb(back_callback) if back_callback else home_only_kb()
    await show_payment_tariff_select_page(
        callback,
        context=build_payment_tariff_select_page_context(
            provider_title_html=provider_title_html,
            instruction_html=instruction_html,
            key_name=key_name,
        ),
        runtime_markup=runtime_markup,
    )


async def rerender_payment_tariff_select_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённый экран выбора тарифа оплаты после изменения через /yaa."""
    context = dict(page_context.context or {})
    if not context:
        return False

    rendered_message = await safe_edit_or_send(
        page_context.message,
        render_payment_tariff_select_page_text(context),
        reply_markup=build_payment_tariff_select_reply_markup(context, page_context.append_buttons),
    )
    remember_payment_tariff_select_page_context(
        viewer_id,
        rendered_message,
        context,
        page_context.append_buttons,
    )
    return True
