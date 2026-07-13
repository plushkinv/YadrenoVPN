"""General page-backed user area screens."""

ACCESS_BLOCKED_PAGE_KEY = 'access_blocked'


async def render_access_blocked_page(target, *, force_new: bool = False) -> None:
    """Renders an editable locked screen."""
    from bot.utils.page_renderer import render_page

    await render_page(target, page_key=ACCESS_BLOCKED_PAGE_KEY, force_new=force_new)
