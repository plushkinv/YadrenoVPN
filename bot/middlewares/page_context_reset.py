"""Сброс контекста /yaa при переходе по пользовательской части бота."""
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.page_context import clear_page_context
from config import ADMIN_IDS


class ResetAdminPageContextMiddleware(BaseMiddleware):
    """
    Перед любым новым действием в пользовательской части очищает старый контекст.

    Если обработчик снова рендерит поддерживаемую страницу через render_page(),
    новый контекст будет записан уже после фактического рендера. Поэтому /yaa
    не сможет случайно отработать по странице, с которой администратор уже ушёл.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user and user.id in ADMIN_IDS:
            clear_page_context(user.id)
        return await handler(event, data)
