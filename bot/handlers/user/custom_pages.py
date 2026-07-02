"""Универсальный вывод пользовательских custom-страниц."""
from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.utils.custom_pages import CUSTOM_PAGE_CALLBACK_PREFIX, extract_custom_page_key
from bot.utils.page_renderer import render_page
from database.requests import get_page, is_user_banned


router = Router()


@router.callback_query(F.data.startswith(CUSTOM_PAGE_CALLBACK_PREFIX))
async def custom_page_handler(callback: CallbackQuery):
    """Открывает custom-страницу из таблицы pages."""
    if is_user_banned(callback.from_user.id):
        await callback.answer("⛔ Доступ заблокирован", show_alert=True)
        return

    page_key = extract_custom_page_key(callback.data)
    if not page_key or not get_page(page_key):
        await callback.answer("⚠️ Страница недоступна", show_alert=True)
        return

    await render_page(callback, page_key=page_key)
    await callback.answer()
