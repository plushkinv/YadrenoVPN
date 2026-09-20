"""The SMS.RU auth-code transport; no marketing or arbitrary-message API."""
from __future__ import annotations

import asyncio
import aiohttp


async def send_auth_code(*, api_key: str, phone: str, code: str) -> str:
    """Return sent/failed/unknown without logging credentials or retrying a send."""
    payload = {'api_id': api_key, 'to': phone.lstrip('+'),
               'msg': f'Код доступа: {code}. Никому не сообщайте код.', 'json': '1'}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.post('https://sms.ru/sms/send', data=payload, allow_redirects=False) as response:
                if response.status != 200:
                    return 'failed' if 400 <= response.status < 500 else 'unknown'
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    if len(raw) > 32768:
                        return 'unknown'
                import json
                result = json.loads(raw)
                if not isinstance(result, dict):
                    return 'unknown'
                if result.get('status') != 'OK' or result.get('status_code') != 100:
                    return 'failed'
                recipients = result.get('sms')
                if not isinstance(recipients, dict):
                    return 'unknown'
                item = recipients.get(phone.lstrip('+'))
                if not isinstance(item, dict):
                    return 'unknown'
                return 'sent' if item.get('status') == 'OK' and item.get('status_code') == 100 else 'failed'
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return 'unknown'
