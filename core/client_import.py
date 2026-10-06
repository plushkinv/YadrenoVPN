"""Signed, installation-bound handoff of an already authorized subscription URL."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
from urllib.parse import quote, unquote, urlencode, urlsplit

import idna

CLIENTS = {
    'happ': ('Happ', 'happ://add/', 'raw'),
    'incy': ('INCY', 'incy://import/', 'raw'),
    'v2raytun': ('v2RayTun', 'v2raytun://import/', 'raw'),
    'hiddify': ('Hiddify', 'hiddify://import/', 'raw'),
    'v2rayng': ('v2rayNG', 'v2rayng://install-config?url=', 'encoded'),
    'streisand': ('Streisand', 'streisand://import/', 'raw'),
    'shadowrocket': ('Shadowrocket', 'sub://', 'base64'),
    'exclave': ('Exclave', 'exclave://subscription?url=', 'encoded'),
    'v2box': ('V2Box', 'v2box://install-sub?url=', 'encoded'),
}
MAX_TOKEN_LENGTH = 11000
_TOKEN = re.compile(r'v1\.([A-Za-z0-9_-]+)\.([a-f0-9]{64})\Z')


def _subscription_url(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 8192
            or '\\' in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('invalid subscription URL')
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('invalid subscription URL')
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise ValueError('invalid subscription URL')
    return value


def _signature(payload, origin):
    from config import BOT_TOKEN
    # Separate this capability from Telegram authentication and bind it to the
    # configured installation origin. No new persisted secret or token table.
    key = hmac.digest(BOT_TOKEN.encode('utf-8'), b'YadrenoClientImport/v1', 'sha256')
    return hmac.new(key, (origin + '\n' + payload).encode('utf-8'), hashlib.sha256).hexdigest()


def import_link(url: str, origin: str, client: str | None = None) -> str:
    """Call only with access already authorized by the API or Telegram delivery."""
    from web_api.settings import normalize_public_origin
    origin = normalize_public_origin(origin)
    if client is not None and client not in CLIENTS:
        raise ValueError('unsupported client')
    payload = 'v1.' + base64.urlsafe_b64encode(_subscription_url(url).encode('utf-8')).decode('ascii').rstrip('=')
    values = {'token': payload + '.' + _signature(payload, origin)}
    if client is not None:
        values['client'] = client
    return origin + '/open-client#' + urlencode(values)


def _browser_host(host):
    """Serialize HTTP hosts as the browser does, without DNS or network calls."""
    if ':' in host:
        return '[' + ipaddress.IPv6Address(host).compressed + ']'
    # Browser URL uses non-transitional UTS46, unlike Python's IDNA2003 codec.
    host = idna.uts46_remap(unquote(host, errors='strict'), std3_rules=False, transitional=False)
    host = '.'.join(label if label.isascii() else 'xn--' + label.encode('punycode').decode('ascii')
                    for label in host.split('.')).lower()
    if any(char in '\x00\t\n\r #%/:<>?@[\\]^|' for char in host):
        raise ValueError('invalid subscription URL')
    numbers = host.removesuffix('.').split('.')
    if re.fullmatch(r'[0-9]+|0x[0-9a-f]*', numbers[-1]):
        if len(numbers) > 4:
            raise ValueError('invalid subscription URL')
        parsed = []
        for number in numbers:
            radix = 16 if number.startswith('0x') else 8 if len(number) > 1 and number.startswith('0') else 10
            digits = number[2:] if radix == 16 else number[1:] if radix == 8 else number
            if not re.fullmatch({16: '[0-9a-f]*', 8: '[0-7]*', 10: '[0-9]+'}[radix], digits):
                raise ValueError('invalid subscription URL')
            parsed.append(int(digits or '0', radix))
        if any(number > 255 for number in parsed[:-1]) or parsed[-1] >= 256 ** (5 - len(parsed)):
            raise ValueError('invalid subscription URL')
        host = str(ipaddress.IPv4Address(sum(number << (24 - 8 * index) for index, number in enumerate(parsed[:-1])) + parsed[-1]))
    return host


def _browser_url(url):
    """Match the released new URL(url).href input to Shadowrocket's base64."""
    parts = urlsplit(url)
    host = _browser_host(parts.hostname)
    port = parts.port
    if port is not None and (parts.scheme, port) not in {('http', 80), ('https', 443)}:
        host += ':' + str(port)
    segments = []
    path = (parts.path or '/').split('/')
    for index, segment in enumerate(path):
        dot = segment.lower().replace('%2e', '.')
        if dot == '..' and len(segments) > 1:
            segments.pop()
        if dot not in {'.', '..'}:
            segments.append(segment)
        elif index == len(path) - 1:
            segments.append('')

    def encode(value, excluded):
        return quote(value, safe=''.join(chr(char) for char in range(33, 127) if chr(char) not in excluded))

    result = parts.scheme + '://' + host + encode('/'.join(segments), '\"#<>?`{}^')
    # urlunsplit drops empty delimiters, but the released browser URL keeps them.
    if '?' in url.partition('#')[0]:
        result += '?' + encode(parts.query, '\"#<>\'')
    if '#' in url:
        result += '#' + encode(parts.fragment, '\"<>`')
    return result


def resolve_import(token: str, client: str, origin: str) -> dict:
    """A bearer link permits only supported app imports, never HTTP redirects."""
    if client not in CLIENTS or not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        raise ValueError('invalid client import link')
    match = _TOKEN.fullmatch(token)
    if match is None or not hmac.compare_digest(match[2], _signature('v1.' + match[1], origin)):
        raise ValueError('invalid client import link')
    try:
        url = _subscription_url(base64.urlsafe_b64decode(match[1] + '=' * (-len(match[1]) % 4)).decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('invalid client import link') from None
    name, prefix, encoding = CLIENTS[client]
    value = (quote(url, safe="~()*!.'-_") if encoding == 'encoded' else
             base64.b64encode(_browser_url(url).encode('ascii')).decode('ascii') if encoding == 'base64' else url)
    return {'client_name': name, 'uri': prefix + value, 'url': url}
