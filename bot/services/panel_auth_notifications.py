"""Best-effort administrator notices for rejected, preserved panel tokens."""
from __future__ import annotations

import logging
import time
from functools import partial

from config import ADMIN_IDS
from database.requests import claim_panel_auth_notification, get_all_servers
from runtime import delivery
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)
_failures: dict[str, set[int]] = {}


def panel_endpoint_key(server: dict) -> str:
    """Match the physical endpoint used by the panel's authentication lock."""
    path = str(server.get('web_base_path') or '').strip('/')
    return (
        f"{str(server.get('protocol') or 'https').casefold()}://"
        f"{str(server['host']).casefold()}:{int(server['port'])}"
        + (f'/{path}' if path else '')
    )


def record_auth_success(panel_key: str, server_id: int | None) -> None:
    """Clear only the logical server proven healthy; keep the saved cooldown."""
    failed = _failures.get(panel_key)
    if failed is not None:
        failed.discard(server_id)
        if not failed:
            _failures.pop(panel_key, None)


async def _send(bot, *, admin_id: int, text: str) -> None:
    await bot.send_message(admin_id, text, parse_mode='HTML')


def record_token_rejection(panel_key: str, server_id: int | None) -> None:
    """Record real failures only; optional delivery must not mask a panel error."""
    if server_id is None:
        return
    try:
        failed = _failures.setdefault(panel_key, set())
        failed.add(server_id)
        bot = delivery.get_bot()
        recipients = set(ADMIN_IDS)
        if bot is None or not recipients:
            return
        if not claim_panel_auth_notification(panel_key, time.time()):
            return
        servers = [
            server for server in get_all_servers()
            if server['id'] in failed and panel_endpoint_key(server) == panel_key
        ]
        # Deleted or reconfigured rows must not appear as failures of this panel.
        failed.intersection_update(server['id'] for server in servers)
        if not servers:
            return
        panel = servers[0]
        names = '\n'.join(
            f"• {escape_html(str(server['name']))} (ID {int(server['id'])})"
            for server in servers
        )
        text = (
            '⚠️ <b>Панель не принимает API-ключ</b>\n\n'
            f"Панель: {escape_html(str(panel['host']))}:{int(panel['port'])}\n"
            f'Серверы с ошибкой:\n{names}\n\n'
            'Токен сохранён. Бот продолжит использовать его при следующих обращениях. '
            'Если ошибка сохраняется, проверьте API-ключ в панели 3X-UI и обновите '
            'его в карточках перечисленных серверов в разделе «Сервера».'
        )
        for admin_id in recipients:
            delivery.dispatch(partial(_send, admin_id=admin_id, text=text), bot=bot)
    except Exception as error:
        logger.warning('Panel auth notice failed type=%s', type(error).__name__)
