"""
Main router of the admin panel.

Processes login to the admin panel and the main menu.
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from bot.services.admin_monitoring import (
    build_admin_summary_text,
    AdminMonitoringRead,
)
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import admin_main_menu_kb, author_support_kb, marketing_menu_kb
from bot.utils.admin import is_admin
from bot.utils.telegram_links import build_telegram_link
from bot.utils.text import safe_edit_or_send
from bot.utils.admin_monitoring_view import monitoring_turn, show_monitoring_screen

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ADMINISTRATOR CHECK
# ============================================================================




# ============================================================================
# ADMIN MAIN MENU
# ============================================================================

from aiogram.exceptions import TelegramBadRequest

@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery, state: FSMContext):
    """Shows the main menu of the admin panel."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    turn = await monitoring_turn(state)
    await callback.answer()
    # Complete the keyboard-removal message before showing the menu. Only the
    # Telegram calls hold the delivery lock; the one-second pause does not.
    try:
        async with turn.delivery_lock:
            if not turn.current():
                return
            temp_msg = await callback.message.answer("⏳", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(1.0)
        async with turn.delivery_lock:
            await temp_msg.delete()
    except Exception:
        logger.warning('Не удалось очистить обычную клавиатуру перед админкой', exc_info=True)

    if not turn.current():
        return
    await state.set_state(AdminStates.admin_menu)
    from bot.services.page_context import clear_page_context
    clear_page_context(callback.from_user.id)
    try:
        await show_monitoring_screen(
            callback.message, turn, AdminMonitoringRead(),
            lambda snapshot: (build_admin_summary_text(snapshot), admin_main_menu_kb()),
            deliver=safe_edit_or_send,
        )
    except TelegramBadRequest as e:
        if "is not modified" not in str(e):
            logger.error(f"Ошибка при обновлении меню: {e}")


# ============================================================================
# MARKETING SECTION
# ============================================================================

@router.callback_query(F.data == "admin_marketing")
async def show_marketing_menu(callback: CallbackQuery, state: FSMContext):
    """Shows a menu of marketing tools."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.admin_menu)

    text = (
        "📣 <b>Маркетинг</b>\n\n"
        "Выберите инструмент:"
    )

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=marketing_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_placeholder")
async def admin_placeholder(callback: CallbackQuery):
    """A placeholder for a temporarily empty button in the main menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    await callback.answer("Скоро будет больше полезных функций ;)")


# ============================================================================
# SUPPORT SECTION
# ============================================================================

@router.callback_query(F.data == "admin_author_support")
async def show_author_support(callback: CallbackQuery):
    """Shows the author support screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    await callback.answer()

    developer_link = build_telegram_link('plushkin_blog')
    seller_link = build_telegram_link('Ya_SellerBot', 'item-40')
    
    text = (
        "👤 <b>Автор и поддержка</b>\n\n"
        f"<b>Разработчик</b>: <a href=\"{developer_link}\">Plushkin Blog</a>\n\n"
        "Я собираю деньги на разработку игры в жанре MMORTS с честной экономикой и никакого pay2win. Т.е. нельзя будет ничего купить у автора игры, никаких эксклюзивных вещей или бесконечных ресурсов для богатых.\n\n"
        "Очень нужна ваша поддержка, даже 100р уже вперед. как говорится с мира по нитке ;)\n"
        "💳 <b>Карты РФ</b>: https://yoomoney.ru/fundraise/1GJ73GGRJBC.260318\n"
        f"💰 <b>USDT (TON/BSC/ARBITRUM)</b>: {seller_link}\n\n"
        "‼️Другие полезные для тебя боты\n\n"
        "@Ya_FooterBot - <i>сделай автоматическую подпись ко всем постам в своем канале, добавь туда ссылку на свой VPN</i>"
    )
    
    try:
        await safe_edit_or_send(
            callback.message, 
            text,
            reply_markup=author_support_kb()
        )
    except TelegramBadRequest as e:
        if "is not modified" not in str(e):
            logger.error(f"Ошибка при показе поддержки автора: {e}")

# ============================================================================
# READDRESSING TO SUBROOUTERS
# ============================================================================

# The Users section is implemented in users.py
# The “Bot Settings” section is implemented in system.py

