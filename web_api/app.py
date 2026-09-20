"""The opt-in aiohttp listener lives in the application's existing event loop."""
from __future__ import annotations

from dataclasses import dataclass
from aiohttp import web

from runtime.readiness import is_active
from web_api.settings import LOOPBACK_HOST, WebSettings, get_web_settings

SETTINGS_KEY = web.AppKey('web_settings', WebSettings)


@web.middleware
async def activation_middleware(request: web.Request, handler):
    if request.path != '/health' and not is_active():
        return web.json_response({'code': 'temporarily_unavailable', 'details': {}, 'retryable': True}, status=503)
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    ready = is_active()
    return web.json_response({'ready': ready}, status=200 if ready else 503)


def create_app(settings: WebSettings | None = None) -> web.Application:
    from web_api.auth import add_routes, security_middleware
    app = web.Application(middlewares=[activation_middleware, security_middleware], client_max_size=64 * 1024)
    app[SETTINGS_KEY] = settings or get_web_settings()
    app.router.add_get('/health', health)
    from web_api.schemas import openapi
    app.router.add_get('/api/v1/openapi.json', openapi)
    add_routes(app)
    from web_api.account_links import add_routes as add_account_link_routes
    add_account_link_routes(app)
    from web_api.subscription_import import add_routes as add_import_routes
    add_import_routes(app)
    from web_api.payments import add_routes as add_payment_routes
    add_payment_routes(app)
    from web_api.modules import add_routes as add_module_routes
    add_module_routes(app)
    from web_api.user import add_routes as add_user_routes
    add_user_routes(app)
    return app


@dataclass
class WebServer:
    runner: web.AppRunner
    settings: WebSettings

    async def stop(self) -> None:
        await self.runner.cleanup()


async def start_web_server() -> WebServer | None:
    settings = get_web_settings()
    if not settings.enabled:
        return None
    runner = web.AppRunner(create_app(settings))
    await runner.setup()
    try:
        await web.TCPSite(runner, LOOPBACK_HOST, settings.port).start()
    except BaseException:
        await runner.cleanup()
        raise
    return WebServer(runner, settings)
