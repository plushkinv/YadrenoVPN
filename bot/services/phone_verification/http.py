"""Bounded HTTPS requests without redirects, secret logging or send retries."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json

import aiohttp


@dataclass(frozen=True)
class ProviderResult:
    delivery: str = 'unknown'
    state: str = 'waiting'
    request_id: str | None = None


class InvalidProviderCode(Exception):
    """The provider rejected the submitted OTP."""


def request_id(value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    value = str(value)
    return value if 0 < len(value) <= 64 and value.isascii() and value.isdecimal() and int(value) > 0 else None


async def post(url: str, *, payload: dict, headers=None, auth=None) -> tuple[int, dict | None]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.post(url, json=payload, headers=headers, auth=auth, allow_redirects=False) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    if len(raw) > 32768:
                        return response.status, None
                result = json.loads(raw)
                return response.status, result if isinstance(result, dict) else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return 0, None


def failed_response(status: int, result: dict | None, success_field: str = 'success') -> ProviderResult | None:
    if 400 <= status < 500 or status == 200 and result is not None and result.get(success_field) is False:
        return ProviderResult('failed', 'failed')
    if status != 200 or result is None or result.get(success_field) is not True:
        return ProviderResult()
    return None
