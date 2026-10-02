"""DB-owned listener configuration, separate from legacy provider webhooks."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from urllib.parse import urlsplit

DEFAULT_PORT = 18764
LOOPBACK_HOST = '127.0.0.1'
DEFAULT_TRUSTED_PROXIES = ('127.0.0.0/8', '::1/128')


def private_bind(value: str) -> str:
    address = ipaddress.ip_address(value)
    private_ranges = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7')
    if not (address.is_loopback or any(address in ipaddress.ip_network(net) for net in private_ranges)):
        raise ValueError('backend bind must be an explicit loopback or private address')
    return str(address)


def trusted_proxies(values) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
        raise ValueError('trusted proxies must be an explicit nonempty address/network list')
    result = []
    for value in values:
        network = ipaddress.ip_network(value, strict=False)
        # An explicit closed connection must not trust arbitrary Internet peers.
        if network.prefixlen == 0 or not (network.network_address.is_loopback or
                any(network.subnet_of(ipaddress.ip_network(net)) for net in
                    ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7')
                    if ipaddress.ip_network(net).version == network.version)):
            raise ValueError('trusted proxy must be a loopback or private network')
        result.append(str(network))
    return tuple(dict.fromkeys(result))


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
    host: str = LOOPBACK_HOST
    trusted_proxies: tuple[str, ...] = DEFAULT_TRUSTED_PROXIES


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
    try:
        host = private_bind(get_setting('web_listen_host', LOOPBACK_HOST))
        proxies = trusted_proxies(json.loads(get_setting('web_trusted_proxies', json.dumps(DEFAULT_TRUSTED_PROXIES))))
    except (TypeError, ValueError):
        if enabled:
            raise
        host, proxies = LOOPBACK_HOST, DEFAULT_TRUSTED_PROXIES
    return WebSettings(enabled, port, origin, host, proxies)
