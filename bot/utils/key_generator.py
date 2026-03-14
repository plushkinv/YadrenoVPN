"""
Утилиты для генерации ключей доступа (VLESS, VMess, Trojan, Shadowsocks, JSON, QR).
Мультипротокольная поддержка для 3X-UI панели.
"""
import json
import base64
import urllib.parse
import io
import logging
import qrcode
from typing import Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# УНИВЕРСАЛЬНЫЕ РОУТЕРЫ
# ============================================================================

def generate_link(config: Dict[str, Any]) -> str:
    """
    Генерирует ссылку подключения на основе протокола из конфига.
    Поддерживает: vless, vmess, trojan, shadowsocks.
    """
    protocol = config.get('protocol', 'vless')
    
    generators = {
        'vless': generate_vless_link,
        'vmess': generate_vmess_link,
        'trojan': generate_trojan_link,
        'shadowsocks': generate_shadowsocks_link,
    }
    
    gen = generators.get(protocol, generate_vless_link)
    return gen(config)


def generate_json(config: Dict[str, Any]) -> str:
    """
    Генерирует JSON-конфигурацию для Xray/V2Ray клиентов.
    Поддерживает: vless, vmess, trojan, shadowsocks.
    """
    protocol = config.get('protocol', 'vless')
    
    generators = {
        'vless': generate_vless_json,
        'vmess': generate_vmess_json,
        'trojan': generate_trojan_json,
        'shadowsocks': generate_shadowsocks_json,
    }
    
    gen = generators.get(protocol, generate_vless_json)
    return gen(config)


# ============================================================================
# ОБЩИЕ УТИЛИТЫ
# ============================================================================

def _get_remark(config: Dict[str, Any]) -> str:
    """Формирует имя подключения (remark)."""
    remark_part = config.get('inbound_name', 'VPN')
    email_part = config.get('email', '')
    return f"{remark_part}-{email_part}"


def _parse_transport_params(stream: dict, params: dict) -> None:
    """Извлекает параметры транспорта из stream_settings и добавляет в params."""
    network = stream.get('network', 'tcp')
    
    if network == 'tcp':
        tcp_settings = stream.get('tcpSettings', {})
        header = tcp_settings.get('header', {})
        if header.get('type') == 'http':
            params['headerType'] = 'http'
            request = header.get('request', {})
            request_path = request.get('path', [])
            if request_path:
                params['path'] = request_path[0]
            headers = request.get('headers', {})
            host = _search_host(headers)
            if host:
                params['host'] = host
    
    elif network == 'kcp':
        kcp_settings = stream.get('kcpSettings', {})
        header = kcp_settings.get('header', {})
        params['headerType'] = header.get('type', 'none')
        seed = kcp_settings.get('seed', '')
        if seed:
            params['seed'] = seed
    
    elif network == 'ws':
        ws_settings = stream.get('wsSettings', {})
        params['path'] = ws_settings.get('path', '/')
        host = ws_settings.get('host', '')
        if not host:
            headers = ws_settings.get('headers', {})
            host = _search_host(headers)
        if host:
            params['host'] = host
    
    elif network == 'grpc':
        grpc_settings = stream.get('grpcSettings', {})
        params['serviceName'] = grpc_settings.get('serviceName', '')
        authority = grpc_settings.get('authority', '')
        if authority:
            params['authority'] = authority
        if grpc_settings.get('multiMode'):
            params['mode'] = 'multi'
    
    elif network == 'httpupgrade':
        hu_settings = stream.get('httpupgradeSettings', {})
        params['path'] = hu_settings.get('path', '/')
        host = hu_settings.get('host', '')
        if not host:
            headers = hu_settings.get('headers', {})
            host = _search_host(headers)
        if host:
            params['host'] = host
    
    elif network == 'xhttp':
        xhttp_settings = stream.get('xhttpSettings', {})
        params['path'] = xhttp_settings.get('path', '/')
        host = xhttp_settings.get('host', '')
        if not host:
            headers = xhttp_settings.get('headers', {})
            host = _search_host(headers)
        if host:
            params['host'] = host
        params['mode'] = xhttp_settings.get('mode', 'auto')


def _parse_security_params(stream: dict, params: dict) -> None:
    """Извлекает параметры безопасности (TLS/Reality) из stream_settings."""
    security = stream.get('security', 'none')
    
    if security == 'tls':
        params['security'] = 'tls'
        tls_settings = stream.get('tlsSettings', {})
        
        if tls_settings.get('serverName'):
            params['sni'] = tls_settings['serverName']
        
        settings = tls_settings.get('settings', {})
        fp = settings.get('fingerprint', '') or tls_settings.get('fingerprint', '')
        if fp:
            params['fp'] = fp
        
        alpns = tls_settings.get('alpn', [])
        if alpns:
            params['alpn'] = ','.join(alpns)
    
    elif security == 'reality':
        params['security'] = 'reality'
        reality_settings = stream.get('realitySettings', {})
        settings_inner = reality_settings.get('settings', {})
        
        # SNI
        sni = settings_inner.get('serverName', '')
        if not sni:
            sni = reality_settings.get('serverName', '')
        if not sni:
            server_names = reality_settings.get('serverNames', [])
            if server_names:
                sni = server_names[0]
        if not sni:
            sni = reality_settings.get('dest', '').split(':')[0]
        if sni:
            params['sni'] = sni
        
        # Fingerprint
        fp = settings_inner.get('fingerprint', '') or reality_settings.get('fingerprint', '') or 'chrome'
        params['fp'] = fp
        
        # Public Key
        pbk = settings_inner.get('publicKey', '') or reality_settings.get('publicKey', '')
        if pbk:
            params['pbk'] = pbk
        
        # Short ID
        short_ids = reality_settings.get('shortIds', [])
        sid = short_ids[0] if short_ids else ''
        if not sid:
            sid = reality_settings.get('shortId', '')
        if sid:
            params['sid'] = sid
        
        # Spider X
        spx = settings_inner.get('spiderX', '') or reality_settings.get('spiderX', '') or '/'
        if spx:
            params['spx'] = spx
    
    else:
        params['security'] = 'none'


def _search_host(headers: dict) -> str:
    """Ищет значение Host в заголовках (может быть строкой или списком)."""
    if not headers:
        return ''
    host = headers.get('Host', headers.get('host', ''))
    if isinstance(host, list):
        return host[0] if host else ''
    return host


# ============================================================================
# VLESS
# ============================================================================

def generate_vless_link(config: Dict[str, Any]) -> str:
    """Генерирует ссылку vless:// из конфигурации."""
    uuid = config['uuid']
    host = config['host']
    port = config['port']
    name = urllib.parse.quote(_get_remark(config), safe='')
    
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    
    # Порядок параметров как у 3X-UI панели
    params = {
        "type": network,
        "encryption": "none",  # Обязательный параметр для VLESS
    }
    
    _parse_transport_params(stream, params)
    _parse_security_params(stream, params)
    
    # Flow (для VLESS TCP + Reality/TLS)
    flow = config.get('flow', '')
    if flow:
        params['flow'] = flow
    
    # safe='' чтобы / кодировался как %2F (как у панели)
    query = "&".join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v])
    link = f"vless://{uuid}@{host}:{port}?{query}#{name}"
    logger.info(f"Generated VLESS link params: security={params.get('security')}, sni={params.get('sni')}, pbk={params.get('pbk','')[:16]}..., flow={params.get('flow')}, fp={params.get('fp')}")
    return link


def generate_vless_json(config: Dict[str, Any]) -> str:
    """Генерирует JSON-конфигурацию для VLESS."""
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    flow = config.get('flow', '')
    
    outbound = {
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": config['host'],
                "port": config['port'],
                "users": [{
                    "id": config['uuid'],
                    "encryption": "none",
                    "flow": flow
                }]
            }]
        },
        "streamSettings": _build_stream_settings(stream),
        "tag": "proxy"
    }
    
    return _wrap_outbound(outbound)


# ============================================================================
# VMESS
# ============================================================================

def generate_vmess_link(config: Dict[str, Any]) -> str:
    """Генерирует ссылку vmess:// из конфигурации (base64 JSON)."""
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    name = _get_remark(config)
    
    obj = {
        "v": "2",
        "ps": name,
        "add": config['host'],
        "port": config['port'],
        "id": config['uuid'],
        "scy": config.get('security_method', 'auto'),
        "net": network,
        "type": "none",
    }
    
    # Транспорт
    if network == 'tcp':
        tcp = stream.get('tcpSettings', {})
        header = tcp.get('header', {})
        obj['type'] = header.get('type', 'none')
        if obj['type'] == 'http':
            request = header.get('request', {})
            request_path = request.get('path', ['/'])
            obj['path'] = request_path[0] if request_path else '/'
            headers = request.get('headers', {})
            obj['host'] = _search_host(headers)
    elif network == 'ws':
        ws = stream.get('wsSettings', {})
        obj['path'] = ws.get('path', '/')
        host = ws.get('host', '')
        if not host:
            headers = ws.get('headers', {})
            host = _search_host(headers)
        obj['host'] = host
    elif network == 'grpc':
        grpc = stream.get('grpcSettings', {})
        obj['path'] = grpc.get('serviceName', '')
        obj['authority'] = grpc.get('authority', '')
        if grpc.get('multiMode'):
            obj['type'] = 'multi'
    elif network == 'kcp':
        kcp = stream.get('kcpSettings', {})
        header = kcp.get('header', {})
        obj['type'] = header.get('type', 'none')
        obj['path'] = kcp.get('seed', '')
    elif network == 'httpupgrade':
        hu = stream.get('httpupgradeSettings', {})
        obj['path'] = hu.get('path', '/')
        host = hu.get('host', '')
        if not host:
            headers = hu.get('headers', {})
            host = _search_host(headers)
        obj['host'] = host
    elif network == 'xhttp':
        xhttp = stream.get('xhttpSettings', {})
        obj['path'] = xhttp.get('path', '/')
        host = xhttp.get('host', '')
        if not host:
            headers = xhttp.get('headers', {})
            host = _search_host(headers)
        obj['host'] = host
        obj['mode'] = xhttp.get('mode', 'auto')
    
    # Безопасность
    obj['tls'] = security
    if security == 'tls':
        tls_settings = stream.get('tlsSettings', {})
        alpns = tls_settings.get('alpn', [])
        if alpns:
            obj['alpn'] = ','.join(alpns)
        if tls_settings.get('serverName'):
            obj['sni'] = tls_settings['serverName']
        settings = tls_settings.get('settings', {})
        if settings.get('fingerprint'):
            obj['fp'] = settings['fingerprint']
    
    json_str = json.dumps(obj, indent=2, ensure_ascii=False)
    return "vmess://" + base64.b64encode(json_str.encode()).decode()


def generate_vmess_json(config: Dict[str, Any]) -> str:
    """Генерирует JSON-конфигурацию для VMess."""
    stream = config.get('stream_settings', {})
    
    outbound = {
        "protocol": "vmess",
        "settings": {
            "vnext": [{
                "address": config['host'],
                "port": config['port'],
                "users": [{
                    "id": config['uuid'],
                    "security": config.get('security_method', 'auto'),
                    "alterId": 0
                }]
            }]
        },
        "streamSettings": _build_stream_settings(stream),
        "tag": "proxy"
    }
    
    return _wrap_outbound(outbound)


# ============================================================================
# TROJAN
# ============================================================================

def generate_trojan_link(config: Dict[str, Any]) -> str:
    """Генерирует ссылку trojan:// из конфигурации."""
    password = config.get('password', config.get('uuid', ''))
    host = config['host']
    port = config['port']
    name = urllib.parse.quote(_get_remark(config), safe='')
    
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    
    params = {"type": network}
    
    _parse_transport_params(stream, params)
    _parse_security_params(stream, params)
    
    # safe='' чтобы / кодировался как %2F (как у панели 3X-UI)
    query = "&".join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v])
    return f"trojan://{password}@{host}:{port}?{query}#{name}"


def generate_trojan_json(config: Dict[str, Any]) -> str:
    """Генерирует JSON-конфигурацию для Trojan."""
    stream = config.get('stream_settings', {})
    password = config.get('password', config.get('uuid', ''))
    
    outbound = {
        "protocol": "trojan",
        "settings": {
            "servers": [{
                "address": config['host'],
                "port": config['port'],
                "password": password
            }]
        },
        "streamSettings": _build_stream_settings(stream),
        "tag": "proxy"
    }
    
    return _wrap_outbound(outbound)


# ============================================================================
# SHADOWSOCKS
# ============================================================================

def generate_shadowsocks_link(config: Dict[str, Any]) -> str:
    """Генерирует ссылку ss:// из конфигурации."""
    method = config.get('method', 'aes-256-gcm')
    password = config.get('password', '')
    server_password = config.get('server_password', '')
    host = config['host']
    port = config['port']
    name = urllib.parse.quote(_get_remark(config))
    
    # Для Shadowsocks 2022 в режиме Multi-User пароль формируется как ServerPassword:ClientPassword
    if method.startswith('2022-') and server_password and server_password != password:
        password = f"{server_password}:{password}"
    
    # Формат: ss://base64(method:password)@host:port
    user_info = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip('=')
    
    # Добавляем параметры транспорта (как делает 3x-ui: ?type=tcp)
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    
    params = {"type": network}
    _parse_transport_params(stream, params)
    _parse_security_params(stream, params)
    
    # Исключаем security=none чтобы не мусорить, если это дефолт для SS
    if params.get('security') == 'none':
        del params['security']
        
    query = "&".join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v])
    
    if query:
        return f"ss://{user_info}@{host}:{port}?{query}#{name}"
    else:
        return f"ss://{user_info}@{host}:{port}#{name}"


def generate_shadowsocks_json(config: Dict[str, Any]) -> str:
    """Генерирует JSON-конфигурацию для Shadowsocks."""
    stream = config.get('stream_settings', {})
    
    outbound = {
        "protocol": "shadowsocks",
        "settings": {
            "servers": [{
                "address": config['host'],
                "port": config['port'],
                "method": config.get('method', 'aes-256-gcm'),
                "password": f"{config['server_password']}:{config['password']}" if config.get('server_password') and config.get('method', '').startswith('2022-') and config['server_password'] != config['password'] else config.get('password', ''),
            }]
        },
        "streamSettings": _build_stream_settings(stream),
        "tag": "proxy"
    }
    
    return _wrap_outbound(outbound)


# ============================================================================
# ОБЩИЕ ХЕЛПЕРЫ ДЛЯ JSON
# ============================================================================

def _build_stream_settings(stream: dict) -> dict:
    """Строит объект streamSettings для JSON-конфига."""
    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    
    result = {
        "network": network,
        "security": security
    }
    
    # Транспорт
    transport_map = {
        'tcp': 'tcpSettings',
        'kcp': 'kcpSettings',
        'ws': 'wsSettings',
        'grpc': 'grpcSettings',
        'httpupgrade': 'httpupgradeSettings',
        'xhttp': 'xhttpSettings',
    }
    key = transport_map.get(network)
    if key and key in stream:
        result[key] = stream[key]
    
    # Безопасность
    if security == 'tls' and 'tlsSettings' in stream:
        result['tlsSettings'] = stream['tlsSettings']
    elif security == 'reality' and 'realitySettings' in stream:
        result['realitySettings'] = stream['realitySettings']
    
    return result


def _wrap_outbound(outbound: dict) -> str:
    """Оборачивает outbound в полный клиентский конфиг Xray."""
    final_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": 1080,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": True}
        }],
        "outbounds": [
            outbound,
            {"protocol": "freedom", "tag": "direct"}
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [{
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "direct"
            }]
        }
    }
    return json.dumps(final_config, indent=2, ensure_ascii=False)


# ============================================================================
# QR-КОД
# ============================================================================

def generate_qr_code(data: str) -> bytes:
    """
    Генерирует QR-код из строки.
    
    Args:
        data: Данные для QR-кода
        
    Returns:
        Байты изображения (PNG)
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr.getvalue()
