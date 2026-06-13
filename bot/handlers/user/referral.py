"""
Роутер раздела «Реферальная система» для пользователей.

Отображение реферальной ссылки и статистики по уровням.
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database.requests import (
    is_referral_enabled,
    get_referral_reward_type,
    get_referral_levels,
    get_referral_stats,
    get_user_internal_id,
    get_user_balance,
    ensure_user_referral_code,
)
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)

router = Router()


def format_price_compact(cents: int) -> str:
    """Форматирует копейки в компактную строку рублей."""
    if cents >= 10000:
        return f"{cents // 100} ₽"
    else:
        return f"{cents / 100:.2f} ₽".replace(".", ",")


def _build_stats_text(user_internal_id: int) -> str:
    """Формирует блок статистики для плейсхолдера %статистика%.
    
    Показывает только включённые уровни и (при reward_type='balance') баланс.
    
    Args:
        user_internal_id: Внутренний ID пользователя
    
    Returns:
        HTML-текст блока статистики
    """
    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    stats = get_referral_stats(user_internal_id)
    balance = get_user_balance(user_internal_id)

    stats_by_level = {s['level']: s for s in stats} if stats else {}

    lines = []
    lines.append("📊 <b>Ваша статистика:</b>")
    lines.append("")

    visible_levels = [
        level for level in levels
        if bool(level.get('enabled')) and level.get('level_number') in (1, 2, 3)
    ]

    if not visible_levels:
        lines.append("Пока нет активных уровней реферальной программы.")
    for level in visible_levels:
        level_num = level['level_number']
        percent = level['percent']
        level_stat = stats_by_level.get(level_num)
        count = level_stat['count'] if level_stat else 0

        if reward_type == 'days':
            total_reward = level_stat['total_reward_days'] if level_stat else 0
            reward_display = escape_html(f"{total_reward} дн.")
        else:
            total_reward = level_stat['total_reward_cents'] if level_stat else 0
            reward_display = escape_html(format_price_compact(total_reward))

        lines.append(
            f"✅ Уровень {escape_html(str(level_num))} "
            f"({escape_html(str(percent))}%): "
            f"{escape_html(str(count))} чел. — {reward_display}"
        )
    lines.append("")

    if reward_type == 'balance':
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>Ваш баланс:</b> {escape_html(format_price_compact(balance))}")
        lines.append("")

    return "\n".join(lines)


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

    # Формируем реферальную ссылку
    referral_code = ensure_user_referral_code(user_internal_id)
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else callback.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"

    # Формируем блок статистики
    stats_text = _build_stats_text(user_internal_id)

    # Плейсхолдеры
    text_replacements = {
        '%ссылка%': escape_html(referral_link),
        '%статистика%': stats_text,
    }

    await render_page(
        callback,
        page_key='referral',
        text_replacements=text_replacements,
    )
    await callback.answer()
