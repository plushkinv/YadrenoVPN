"""Universal output of custom custom pages."""
from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.utils.custom_pages import CUSTOM_PAGE_CALLBACK_PREFIX, extract_page_key
from bot.utils.page_renderer import render_page
from database.requests import is_user_banned


router = Router()


@router.callback_query(F.data.startswith(CUSTOM_PAGE_CALLBACK_PREFIX))
async def custom_page_handler(callback: CallbackQuery):
    """Opens a custom page from the pages table."""
    if is_user_banned(callback.from_user.id):
        from bot.utils.user_pages import render_access_blocked_page

        await render_access_blocked_page(callback)
        await callback.answer()
        return

    page_key = extract_page_key(callback.data)
    if not page_key:
        rendered = await render_page(callback, 'screen_unavailable')
        if rendered is not None:
            await callback.answer()
        return

    rendered = await render_page(
        callback,
        page_key=page_key,
        context={'telegram_id': callback.from_user.id},
    )
    if rendered is not None:
        await callback.answer()
