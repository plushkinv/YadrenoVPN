"""Buffered arbitrary panel HTTP with the adapter's existing Bearer recovery."""
from __future__ import annotations

import asyncio
import json as json_module
import math
import re
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from urllib.parse import urlencode, urlsplit

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy

from .base import PanelErrorKind, PanelRequestError


@dataclass(frozen=True)
class PanelResponse:
    """Detached response; it contains no request, session or credential handle."""
    status: int
    headers: CIMultiDictProxy[str]
    body: bytes

    def text(self, encoding: str | None = None) -> str:
        if encoding is None:
            content_type = Message()
            content_type['content-type'] = self.headers.get('Content-Type', '')
            encoding = content_type.get_content_charset() or 'utf-8'
        return self.body.decode(encoding)

    def json(self):
        return json_module.loads(self.body)


def _prepare_request(method, endpoint, *, params, json, data, headers, timeout):
    if not isinstance(method, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method):
        raise ValueError('method must be an HTTP method token')
    if not isinstance(endpoint, str) or not endpoint or any(char in endpoint for char in '\r\n\\'):
        raise ValueError('endpoint must be a relative panel path')
    parsed = urlsplit(endpoint)
    if parsed.scheme or parsed.netloc or parsed.fragment or endpoint.startswith('//'):
        raise ValueError('endpoint must be a relative panel path without a fragment')
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('timeout must be a positive number of seconds')
    prepared_headers = CIMultiDict(headers or {})
    if 'Authorization' in prepared_headers or 'Proxy-Authorization' in prepared_headers:
        raise ValueError('authorization is provided by the panel client')
    if json is not None and data is not None:
        raise ValueError('json and data cannot be supplied together')
    if json is not None:
        body = json_module.dumps(json).encode('utf-8')
        prepared_headers.setdefault('Content-Type', 'application/json')
    elif isinstance(data, (Mapping, list, tuple)):
        body = urlencode(data, doseq=True).encode('utf-8')
        prepared_headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
    elif isinstance(data, str):
        body = data.encode('utf-8')
        prepared_headers.setdefault('Content-Type', 'text/plain; charset=utf-8')
    elif isinstance(data, (bytes, bytearray, memoryview)):
        body = bytes(data)
        prepared_headers.setdefault('Content-Type', 'application/octet-stream')
    elif data is None:
        body = None
    else:
        raise TypeError('data must be bytes, text, a mapping or form pairs')
    prepared_headers.setdefault('X-Requested-With', 'XMLHttpRequest')
    # Freeze query/body before any await: a 401 replay uses the same request.
    query = deepcopy(params)
    return method.upper(), '/' + endpoint.lstrip('/'), query, body, prepared_headers


async def request_authorized(client, method, endpoint, *, params=None, json=None,
                             data=None, headers=None, timeout=None) -> PanelResponse:
    method, endpoint, query, body, prepared_headers = _prepare_request(
        method, endpoint, params=params, json=json, data=data, headers=headers, timeout=timeout,
    )
    # Error metadata excludes arbitrary query values and never includes headers/body.
    error_endpoint = urlsplit(endpoint).path

    async def no_connection_replay(request, handler):
        # aiohttp normally retries idempotent methods after a disconnect, even
        # if the panel has already accepted the body. Convert that exception
        # inside the per-request middleware before aiohttp can replay it.
        try:
            return await handler(request)
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as exc:
            raise PanelRequestError(PanelErrorKind.NETWORK, endpoint=error_endpoint,
                                    detail=type(exc).__name__) from exc

    async def attempt(token, *, replay=False):
        session = await client._ensure_session()
        request_headers = prepared_headers.copy()
        request_headers['Authorization'] = f'Bearer {token}'
        options = {} if timeout is None else {'timeout': aiohttp.ClientTimeout(total=timeout)}
        client._record_attempt(retry=replay)
        try:
            async with session.request(
                method, client.base_url + endpoint, params=query, data=body,
                headers=request_headers, allow_redirects=False, **options,
                middlewares=(no_connection_replay,),
            ) as response:
                content = await response.read()
                client._record_bytes(len(content))
                return PanelResponse(response.status, CIMultiDictProxy(CIMultiDict(response.headers)), content)
        except asyncio.TimeoutError as exc:
            raise PanelRequestError(PanelErrorKind.TIMEOUT, endpoint=error_endpoint,
                                    detail='request timed out') from exc
        except aiohttp.ClientError as exc:
            raise PanelRequestError(PanelErrorKind.NETWORK, endpoint=error_endpoint,
                                    detail=type(exc).__name__) from exc

    await client.login()
    token = client.api_token
    if not token:
        raise PanelRequestError(PanelErrorKind.UNAUTHORIZED, endpoint=error_endpoint,
                                detail='Bearer token is unavailable')
    response = await attempt(token)
    if response.status == 401:
        rejection = PanelRequestError(PanelErrorKind.UNAUTHORIZED, endpoint=error_endpoint, status=401)
        replacement = await client._recover_after_401(token, rejection)
        response = await attempt(replacement, replay=True)
        if response.status == 401:
            await client._clear_token_if_current(replacement)
            raise rejection
    if 200 <= response.status < 400:
        from bot.services.panel_auth_notifications import record_auth_success
        record_auth_success(client._panel_auth_key, client.server_id)
    return response
