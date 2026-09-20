"""DB-owned listener configuration, separate from legacy provider webhooks."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_PORT = 18764
LOOPBACK_HOST = '127.0.0.1'


def normalize_public_origin(value: str) -> str:
    parts = urlsplit(value.strip())
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in {'', '/'} or parts.query or parts.fragment):
        raise ValueError('public origin must be an HTTPS hostname with the application at /')
    host = parts.hostname.encode('idna').decode('ascii').lower()
    if any(character.isspace() for character in host) or not host:
        raise ValueError('invalid public origin hostname')
    port = parts.port
    host = '[' + host + ']' if ':' in host else host
    return 'https://' + host + (f':{port}' if port and port != 443 else '')


@dataclass(frozen=True)
class WebSettings:
    enabled: bool
    port: int
    public_origin: str


def get_web_settings() -> WebSettings:
    from database.requests import get_setting

    enabled = get_setting('web_enabled', '0') == '1'
    raw_port = get_setting('web_listen_port', str(DEFAULT_PORT))
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError('web listener port must be between 1 and 65535')
    except (TypeError, ValueError):
        if enabled:
            raise
        port = DEFAULT_PORT
    origin = str(get_setting('web_public_origin', '') or '')
    if origin:
        try:
            origin = normalize_public_origin(origin)
        except ValueError:
            if enabled:
                raise
            origin = ''
    if enabled and not origin:
        raise ValueError('enabled web requires a configured HTTPS public origin')
    return WebSettings(enabled, port, origin)
