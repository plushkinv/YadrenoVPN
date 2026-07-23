"""
Router of the “Referral system” section for users.

Displaying referral links and statistics by level.
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database.requests import (
    is_referral_enabled,
    get_or_create_user,
)
from bot.utils.page_dynamic_data import build_referral_stats_text

logger = logging.getLogger(__name__)

router = Router()


def format_price_compact(cents: int) -> str:
    """Formats kopecks into a compact ruble string."""
    from bot.utils.page_dynamic_data import format_price_compact as _format_price_compact

    return _format_price_compact(cents)


def _build_stats_text(user_internal_id: int) -> str:
    """Generates a statistics block for the referral statistics placeholder.
    
    Shows only enabled levels and (if reward_type='balance') balance.
    
    Args:
        user_internal_id: Internal user ID
    
    Returns:
        HTML text of the statistics block
    """
    return build_referral_stats_text(user_internal_id)


@router.callback_query(F.data == "referral_system")
async def show_referral_system(callback: CallbackQuery):
    """Shows the referral system section."""
    from bot.utils.page_renderer import render_page

    telegram_id = callback.from_user.id

    if not is_referral_enabled():
        await render_page(callback, page_key='action_unavailable')
        await callback.answer()
        return

    get_or_create_user(
        telegram_id,
        callback.from_user.username,
        first_name=getattr(callback.from_user, 'first_name', None),
        last_name=getattr(callback.from_user, 'last_name', None),
    )

    await render_page(
        callback,
        page_key='referral',
    )
    await callback.answer()
