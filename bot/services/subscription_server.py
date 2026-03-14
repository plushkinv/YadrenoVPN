"""
Небольшой HTTP-сервер для выдачи subscription по токену.
"""
import base64
import logging
from typing import Optional
from aiohttp import web

from database.requests import get_setting, get_subscription_links_by_token

logger = logging.getLogger(__name__)

_runner: Optional[web.AppRunner] = None
_site: Optional[web.TCPSite] = None


async def _handle_subscription(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip()
    if not token:
        return web.Response(text="not found", status=404)

    links = get_subscription_links_by_token(token)
    if not links:
        return web.Response(text="not found", status=404)

    payload = "\n".join(links).encode("utf-8")
    encoded = base64.b64encode(payload).decode("utf-8")
    return web.Response(text=encoded, content_type="text/plain")


async def start_subscription_server() -> None:
    global _runner, _site
    if _runner:
        return

    app = web.Application()
    app.router.add_get("/sub/{token}", _handle_subscription)

    host = get_setting("subscription_bind_host", "0.0.0.0") or "0.0.0.0"
    port = int(get_setting("subscription_bind_port", "8080") or 8080)

    _runner = web.AppRunner(app)
    await _runner.setup()
    _site = web.TCPSite(_runner, host=host, port=port)
    await _site.start()
    logger.info("Subscription server started on %s:%s", host, port)


async def stop_subscription_server() -> None:
    global _runner, _site
    if _site:
        await _site.stop()
        _site = None
    if _runner:
        await _runner.cleanup()
        _runner = None
