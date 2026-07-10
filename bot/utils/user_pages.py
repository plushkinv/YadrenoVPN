"""Общие page-backed экраны пользовательской зоны."""

ACCESS_BLOCKED_PAGE_KEY = 'access_blocked'


async def render_access_blocked_page(target, *, force_new: bool = False) -> None:
    """Рендерит редактируемый экран заблокированного доступа."""
    from bot.utils.page_renderer import render_page

    await render_page(target, page_key=ACCESS_BLOCKED_PAGE_KEY, force_new=force_new)
