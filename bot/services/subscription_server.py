"""
Небольшой HTTP-сервер для выдачи subscription по токену.
"""
import base64
import errno
import logging
from typing import Optional
from aiohttp import web

from database.requests import get_setting, get_subscription_links_by_token, set_setting

logger = logging.getLogger(__name__)

_runner: Optional[web.AppRunner] = None
_site: Optional[web.TCPSite] = None
_bound_port: Optional[int] = None


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
    global _runner, _site, _bound_port
    if _runner:
        return

    host = get_setting("subscription_bind_host", "0.0.0.0") or "0.0.0.0"
    preferred_port = int(get_setting("subscription_bind_port", "18080") or 18080)

    # Пробуем старт на выбранном порту, затем перебираем следующие порты.
    for port in range(preferred_port, preferred_port + 30):
        app = web.Application()
        app.router.add_get("/sub/{token}", _handle_subscription)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)

        try:
            await site.start()
            _runner = runner
            _site = site
            _bound_port = port
            if port != preferred_port:
                set_setting("subscription_bind_port", str(port))
                logger.warning(
                    "Порт подписок %s занят, переключено на %s (обновите reverse proxy при необходимости).",
                    preferred_port, port
                )
            logger.info("Subscription server started on %s:%s", host, port)
            return
        except OSError as e:
            await runner.cleanup()
            if e.errno == errno.EADDRINUSE:
                continue
            raise

    raise RuntimeError(
        f"Не удалось запустить subscription server: все порты заняты в диапазоне "
        f"{preferred_port}-{preferred_port + 29}"
    )


async def stop_subscription_server() -> None:
    global _runner, _site, _bound_port
    if _site:
        await _site.stop()
        _site = None
    if _runner:
        await _runner.cleanup()
        _runner = None
    _bound_port = None
