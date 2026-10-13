"""Public signature-authenticated ingress; no browser session is inferred."""
import logging
from types import MappingProxyType

from aiohttp import web

from core.extensions.webhooks import accept
from core.results import CoreError
from web_api.auth import _error_status

logger = logging.getLogger(__name__)


async def receive(request):
    try:
        result = await accept(request.match_info['module_id'], request.match_info['name'],
            await request.read(), MappingProxyType({k.lower(): v for k, v in request.headers.items()}))
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except CoreError as error:
        status = 403 if error.code == 'webhook_authentication_failed' else _error_status(error)
        return web.json_response(error.as_dict(), status=status)
    except web.HTTPException:
        raise
    except Exception as error:
        logger.error('Module webhook acceptance failed type=%s', type(error).__name__)
        return web.json_response(CoreError('webhook_unavailable', retryable=True).as_dict(), status=503)


def add_routes(app):
    app.router.add_post('/webhooks/modules/{module_id}/{name}', receive)
