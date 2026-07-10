"""HTTP endpoint для webhook-ов кастомных payment providers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from aiohttp import web

from database.requests import get_setting

logger = logging.getLogger(__name__)

WEBHOOKS_ENABLED_SETTING = 'custom_payment_webhooks_enabled'
WEBHOOKS_HOST_SETTING = 'custom_payment_webhooks_host'
WEBHOOKS_PORT_SETTING = 'custom_payment_webhooks_port'
WEBHOOKS_PATH_PREFIX_SETTING = 'custom_payment_webhooks_path_prefix'

DEFAULT_WEBHOOKS_HOST = '127.0.0.1'
DEFAULT_WEBHOOKS_PORT = 8088
DEFAULT_WEBHOOKS_PATH_PREFIX = '/custom-payment-webhook'
WEBHOOK_SECRET_HEADER = 'X-Yadreno-Webhook-Secret'
BOT_APP_KEY = web.AppKey('custom_payment_webhook_bot', Any)


@dataclass
class CustomPaymentWebhookServer:
    """Запущенный aiohttp runner для кастомных payment webhook-ов."""

    runner: web.AppRunner
    host: str
    port: int
    path_prefix: str

    async def stop(self) -> None:
        await self.runner.cleanup()


async def start_custom_payment_webhook_server(bot: Any) -> CustomPaymentWebhookServer | None:
    """Стартует webhook HTTP-server, если он включён в settings."""
    if not is_custom_payment_webhook_server_enabled():
        logger.info("Custom payment webhook server выключен")
        return None

    host = get_custom_payment_webhook_host()
    port = get_custom_payment_webhook_port()
    path_prefix = get_custom_payment_webhook_path_prefix()
    app = create_custom_payment_webhook_app(bot, path_prefix=path_prefix)

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    logger.info("Custom payment webhook server запущен: http://%s:%s%s/{provider_id}", host, port, path_prefix)
    return CustomPaymentWebhookServer(runner=runner, host=host, port=port, path_prefix=path_prefix)


def create_custom_payment_webhook_app(bot: Any, *, path_prefix: str | None = None) -> web.Application:
    """Создаёт aiohttp app для тестов и runtime."""
    normalized_prefix = _normalize_path_prefix(path_prefix or DEFAULT_WEBHOOKS_PATH_PREFIX)
    app = web.Application(client_max_size=1024 * 1024)
    app[BOT_APP_KEY] = bot
    app.router.add_get(f'{normalized_prefix}/health', _health_handler)
    app.router.add_post(f'{normalized_prefix}/{{provider_id}}', _payment_webhook_handler)
    return app


def is_custom_payment_webhook_server_enabled() -> bool:
    return str(get_setting(WEBHOOKS_ENABLED_SETTING, '0') or '').strip() == '1'


def get_custom_payment_webhook_host() -> str:
    host = str(get_setting(WEBHOOKS_HOST_SETTING, DEFAULT_WEBHOOKS_HOST) or '').strip()
    return host or DEFAULT_WEBHOOKS_HOST


def get_custom_payment_webhook_port() -> int:
    raw = get_setting(WEBHOOKS_PORT_SETTING, str(DEFAULT_WEBHOOKS_PORT))
    try:
        port = int(raw or DEFAULT_WEBHOOKS_PORT)
    except (TypeError, ValueError):
        return DEFAULT_WEBHOOKS_PORT
    if port <= 0 or port > 65535:
        return DEFAULT_WEBHOOKS_PORT
    return port


def get_custom_payment_webhook_path_prefix() -> str:
    return _normalize_path_prefix(
        get_setting(WEBHOOKS_PATH_PREFIX_SETTING, DEFAULT_WEBHOOKS_PATH_PREFIX)
        or DEFAULT_WEBHOOKS_PATH_PREFIX
    )


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({'ok': True})


async def _payment_webhook_handler(request: web.Request) -> web.Response:
    provider_id = str(request.match_info.get('provider_id') or '')

    from bot.utils.payment_provider_registry import get_payment_provider, validate_payment_webhook_secret

    try:
        provider = get_payment_provider(provider_id)
    except ValueError:
        provider = None
    if provider is None or provider.webhook_handler is None:
        return web.json_response({'ok': False, 'reason': 'provider_not_found'}, status=404)

    provided_secret = request.headers.get(WEBHOOK_SECRET_HEADER) or request.query.get('secret')
    if not validate_payment_webhook_secret(provider.provider_id, provided_secret):
        return web.json_response({'ok': False, 'reason': 'forbidden'}, status=403)

    try:
        request_context = await _build_request_context(request, provider.provider_id)
        from bot.services.custom_payments import process_custom_payment_webhook

        result = await process_custom_payment_webhook(
            provider.provider_id,
            request_context,
            bot=request.app.get(BOT_APP_KEY),
        )
    except Exception as e:
        logger.warning("Ошибка обработки webhook custom payment provider=%s: %s", provider_id, e)
        return web.json_response({'ok': False, 'reason': 'internal_error'}, status=500)

    return web.json_response(_public_webhook_response(result), status=_webhook_http_status(result))


async def _build_request_context(request: web.Request, provider_id: str) -> dict[str, Any]:
    body = await request.read()
    body_text = body.decode('utf-8', errors='replace')
    content_type = (request.content_type or '').casefold()
    json_payload = None
    form_payload: dict[str, Any] = {}

    if content_type == 'application/json' and body_text:
        try:
            json_payload = json.loads(body_text)
        except json.JSONDecodeError:
            json_payload = None
    elif content_type == 'application/x-www-form-urlencoded' and body_text:
        parsed = parse_qs(body_text, keep_blank_values=True)
        form_payload = {
            key: values[0] if len(values) == 1 else values
            for key, values in parsed.items()
        }

    return {
        'provider_id': provider_id,
        'method': request.method,
        'path': request.path,
        'query': dict(request.query),
        'headers': dict(request.headers),
        'body': body_text,
        'body_bytes': body,
        'json': json_payload,
        'form': form_payload,
        'remote': request.remote,
        'content_type': request.content_type,
    }


def _public_webhook_response(result: dict[str, Any]) -> dict[str, Any]:
    response = {
        'ok': bool(result.get('ok')),
        'status': result.get('status'),
        'ignored': bool(result.get('ignored')),
        'completed': bool(result.get('completed')),
        'processed_now': bool(result.get('processed_now')),
    }
    if result.get('order_id'):
        response['order_id'] = result.get('order_id')
    if result.get('reason'):
        response['reason'] = result.get('reason')
    return response


def _webhook_http_status(result: dict[str, Any]) -> int:
    if result.get('ok'):
        return 200
    try:
        status = int(result.get('http_status') or 400)
    except (TypeError, ValueError):
        return 400
    if status < 400 or status > 599:
        return 400
    return status


def _normalize_path_prefix(value: object) -> str:
    path = str(value or '').strip() or DEFAULT_WEBHOOKS_PATH_PREFIX
    if not path.startswith('/'):
        path = f'/{path}'
    if len(path) > 1:
        path = path.rstrip('/')
    if path == '/':
        return DEFAULT_WEBHOOKS_PATH_PREFIX
    return path


__all__ = [
    'DEFAULT_WEBHOOKS_HOST',
    'DEFAULT_WEBHOOKS_PATH_PREFIX',
    'DEFAULT_WEBHOOKS_PORT',
    'WEBHOOKS_ENABLED_SETTING',
    'WEBHOOKS_HOST_SETTING',
    'WEBHOOKS_PATH_PREFIX_SETTING',
    'WEBHOOKS_PORT_SETTING',
    'WEBHOOK_SECRET_HEADER',
    'BOT_APP_KEY',
    'CustomPaymentWebhookServer',
    'create_custom_payment_webhook_app',
    'get_custom_payment_webhook_host',
    'get_custom_payment_webhook_path_prefix',
    'get_custom_payment_webhook_port',
    'is_custom_payment_webhook_server_enabled',
    'start_custom_payment_webhook_server',
]
