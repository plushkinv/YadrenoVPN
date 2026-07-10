"""
Роутер раздела «Реферальная система» для пользователей.

Отображение реферальной ссылки и статистики по уровням.
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database.requests import (
    is_referral_enabled,
    get_user_internal_id,
)
from bot.utils.page_dynamic_data import build_referral_stats_text

logger = logging.getLogger(__name__)

router = Router()


def format_price_compact(cents: int) -> str:
    """Форматирует копейки в компактную строку рублей."""
    from bot.utils.page_dynamic_data import format_price_compact as _format_price_compact

    return _format_price_compact(cents)


def _build_stats_text(user_internal_id: int) -> str:
    """Формирует блок статистики для плейсхолдера %реферальная_статистика%.
    
    Показывает только включённые уровни и (при reward_type='balance') баланс.
    
    Args:
        user_internal_id: Внутренний ID пользователя
    
    Returns:
        HTML-текст блока статистики
    """
    return build_referral_stats_text(user_internal_id)


@router.callback_query(F.data == "referral_system")
async def show_referral_system(callback: CallbackQuery):
    """Показывает раздел реферальной системы."""
    from bot.utils.page_renderer import render_page

    telegram_id = callback.from_user.id

    if not is_referral_enabled():
        await callback.answer("❌ Реферальная система недоступна", show_alert=True)
        return

    user_internal_id = get_user_internal_id(telegram_id)
    if not user_internal_id:
        await callback.answer("❌ Ошибка пользователя", show_alert=True)
        return

    await render_page(
        callback,
        page_key='referral',
    )
    await callback.answer()
