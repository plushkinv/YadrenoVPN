"""Validated inputs and bounded public results for local web installation."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import urlsplit

from web_api.settings import DEFAULT_TRUSTED_PROXIES, normalize_public_origin, private_bind, trusted_proxies


class SetupError(Exception):
    def __init__(self, code, message, *, stage='preflight', exit_code=3, details=None):
        super().__init__(message)
        self.code, self.stage, self.exit_code = code, stage, exit_code
        self.details = details or {}


def hostname(value):
    if not isinstance(value, str):
        raise ValueError('Укажите отдельный домен или поддомен сайта.')
    value = value.rstrip('.').encode('idna').decode('ascii').lower()
    if len(value) > 253 or '.' not in value or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.')):
        raise ValueError('Домен должен быть отдельным hostname без пути, порта и wildcard.')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError('Нужен домен или поддомен, а не IP-адрес.')


@dataclass(frozen=True)
class SetupOptions:
    proxy: str
    domain: str
    public_url: str
    email: str | None
    agree_tos: bool
    backend_port: int | None
    backend_bind: str
    https_port: int
    listen_address: str | None
    trusted_proxies: tuple[str, ...]
    check_only: bool = False

    @classmethod
    def parse(cls, values):
        try:
            proxy = values.proxy
            if proxy not in {'auto', 'managed-nginx', 'external'}:
                raise ValueError('Допустимые значения --proxy: auto, managed-nginx, external.')
            public_url = normalize_public_origin(values.public_url) if values.public_url else None
            domain = hostname(values.domain or (urlsplit(public_url).hostname if public_url else None))
            port = values.https_port
            if port is None:
                origin_port = urlsplit(public_url).port if public_url else None
                port = 443 if origin_port is None else origin_port
            if not 1 <= port <= 65535 or port == 80:
                raise ValueError('HTTPS-порт должен быть 1–65535 и отличаться от HTTP-01 порта 80.')
            expected = 'https://' + domain + (f':{port}' if port != 443 else '')
            if public_url and public_url != expected:
                raise ValueError('--domain, --public-url и --https-port должны обозначать один origin.')
            if proxy == 'external' and not public_url:
                raise ValueError('Для внешнего прокси нужен --public-url https://domain.')
            if values.backend_port is not None and not 1 <= values.backend_port <= 65535:
                raise ValueError('Внутренний порт должен быть 1–65535.')
            email = values.email
            if email is None and proxy in {'auto', 'managed-nginx'}:
                email = 'admin@' + domain
            if email and not re.fullmatch(r'[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+\.[^\s@\x00-\x1f]+', email):
                raise ValueError('Некорректный контакт для сертификата.')
            listen = str(ipaddress.ip_address(values.listen_address)) if values.listen_address else None
            bind = private_bind(values.backend_bind)
            proxies = trusted_proxies(values.trusted_proxy or DEFAULT_TRUSTED_PROXIES)
            if proxy in {'auto', 'managed-nginx'} and not ipaddress.ip_address(bind).is_loopback:
                raise ValueError('Управляемый Nginx использует loopback; закрытый адрес нужен режиму external.')
            if not ipaddress.ip_address(bind).is_loopback and not values.trusted_proxy:
                raise ValueError('Для закрытого внешнего прокси явно задайте --trusted-proxy.')
            return cls(proxy, domain, expected, email, values.agree_tos, values.backend_port,
                       bind, port, listen, proxies, values.check_only)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise SetupError('invalid_arguments', str(exc), stage='arguments', exit_code=2) from None


def result(options=None, *, ok=False, code='not_ready', stage='preflight', changed=False, **extra):
    return {'ok': ok, 'code': code, 'stage': stage, 'changed': changed,
            'public_url': options.public_url if options else None,
            'listen': {'host': options.backend_bind, 'port': options.backend_port} if options else None,
            'button': {'changed': False, 'code': 'not_attempted'}, **extra}
