"""One-attempt delivery of a stored page to the selected recipient."""
from __future__ import annotations

from typing import Any


async def send_extension_user_page(
    bot: Any, *, telegram_id: int, page_key: str,
) -> dict[str, Any]:
    """Reuse page authorization/rendering; retry policy belongs to the caller."""
    from bot.utils.background_page_delivery import send_background_page
    from bot.utils.custom_pages import page_exists

    if not isinstance(page_key, str) or not page_key.strip():
        raise ValueError('page_key must be a non-empty string')
    if bot is None:
        raise RuntimeError('send_user_page requires a bound bot runtime')
    page_key = page_key.strip()
    if not page_exists(page_key):
        return {'ok': False, 'status': 'not_sent'}
    message = await send_background_page(
        bot, telegram_id=telegram_id, page_key=page_key,
        context={'telegram_id': telegram_id},
    )
    sent = message is not None
    return {'ok': sent, 'status': 'sent' if sent else 'not_sent'}
