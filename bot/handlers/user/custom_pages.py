"""Universal output of custom custom pages."""
from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.utils.custom_pages import CUSTOM_PAGE_CALLBACK_PREFIX, extract_custom_page_key
from bot.utils.page_flow import (
    build_page_flow_context,
    parse_registry_names,
    run_page_guards,
    run_page_hooks,
)
from bot.utils.page_renderer import render_page
from database.requests import get_page, is_user_banned


router = Router()


@router.callback_query(F.data.startswith(CUSTOM_PAGE_CALLBACK_PREFIX))
async def custom_page_handler(callback: CallbackQuery):
    """Opens a custom page from the pages table."""
    if is_user_banned(callback.from_user.id):
        await callback.answer("⛔ Доступ заблокирован", show_alert=True)
        return

    page_key = extract_custom_page_key(callback.data)
    page = get_page(page_key) if page_key else None
    if not page_key or not page:
        await callback.answer("⚠️ Страница недоступна", show_alert=True)
        return

    context = build_page_flow_context(
        callback,
        telegram_id=callback.from_user.id,
        page_key=page_key,
    )

    guard_result = await run_page_guards(
        parse_registry_names(page.get('guard_names')),
        callback,
        context,
    )
    if not guard_result.allowed:
        await callback.answer(
            guard_result.message or "⚠️ Страница недоступна",
            show_alert=guard_result.show_alert,
        )
        return

    hook_result = await run_page_hooks(
        parse_registry_names(page.get('hook_names')),
        callback,
        context,
    )
    context.update(hook_result.context)

    await render_page(
        callback,
        page_key=page_key,
        visibility=hook_result.visibility or None,
        context=context,
        text_replacements=hook_result.text_replacements or None,
        prepend_buttons=hook_result.prepend_buttons,
        append_buttons=hook_result.append_buttons,
    )
    await callback.answer()
