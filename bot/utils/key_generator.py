"""
Утилиты для генерации ключей доступа (VLESS, JSON, QR).
"""
import json
import base64
import urllib.parse
import io
import qrcode
from typing import Dict, Any

def generate_vless_link(config: Dict[str, Any]) -> str:
    """
    Генерирует ссылку vless:// из конфигурации.
    
    Args:
        config: Словарь с конфигурацией (от get_client_config)
        
    Returns:
        Строка ссылки vless://
    """
    uuid = config['uuid']
    host = config['host']
    port = config['port']
    remark = urllib.parse.quote(config['inbound_name'])
    
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    
    params = {
        "type": network,
        "security": security
    }
    
    # Добавляем параметры в зависимости от транспорта
    if network == 'ws':
        ws_settings = stream.get('wsSettings', {})
        params['path'] = ws_settings.get('path', '/')
        if ws_settings.get('headers', {}).get('Host'):
             params['host'] = ws_settings['headers']['Host']
        
    elif network == 'grpc':
        grpc_settings = stream.get('grpcSettings', {})
        params['serviceName'] = grpc_settings.get('serviceName', '')
        if grpc_settings.get('multiMode'):
            params['mode'] = 'multi'
            
    elif network == 'tcp':
        tcp_settings = stream.get('tcpSettings', {})
        header = tcp_settings.get('header', {})
        params['headerType'] = header.get('type', 'none')
        if header.get('type') == 'http':
             # TODO: Добавить host/path если используется
             pass

    # Добавляем sni/fp/alpn если это TLS/Reality
    if security == 'tls':
        tls_settings = stream.get('tlsSettings', {})
        if tls_settings.get('serverName'):
            params['sni'] = tls_settings['serverName']
        if tls_settings.get('fingerprint'):
            params['fp'] = tls_settings['fingerprint']
        if tls_settings.get('alpn'):
            params['alpn'] = ','.join(tls_settings['alpn'])

    elif security == 'reality':
        reality_settings = stream.get('realitySettings', {})
        if reality_settings.get('serverName'):
            params['sni'] = reality_settings['serverName']
        if reality_settings.get('fingerprint'):
            params['fp'] = reality_settings['fingerprint']
        if reality_settings.get('publicKey'):
            params['pbk'] = reality_settings['publicKey']
        if reality_settings.get('shortIds'):
            # Берем первый shortId для ссылки
            params['sid'] = reality_settings['shortIds'][0]
        params['flow'] = 'xtls-rprx-vision' # Обычно для reality используется vision

    # Собираем query string
    query = "&".join([f"{k}={v}" for k, v in params.items() if v])
    
    link = f"vless://{uuid}@{host}:{port}?{query}#{remark}"
    return link


def generate_vless_json(config: Dict[str, Any]) -> str:
    """
    Генерирует JSON-конфигурацию для V2Ray клиентов (Xray).
    
    Args:
        config: Словарь с конфигурацией
        
    Returns:
        JSON строка
    """
    stream = config.get('stream_settings', {})
    network = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    
    outbound = {
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": config['host'],
                    "port": config['port'],
                    "users": [
                        {
                            "id": config['uuid'],
                            "encryption": "none",
                            "flow": ""
                        }
                    ]
                }
            ]
        },
        "streamSettings": {
            "network": network,
            "security": security
        },
        "tag": "proxy"
    }

    # Копируем настройки транспорта
    if network == 'ws':
        outbound['streamSettings']['wsSettings'] = stream.get('wsSettings', {})
    elif network == 'grpc':
        outbound['streamSettings']['grpcSettings'] = stream.get('grpcSettings', {})
    elif network == 'tcp':
        outbound['streamSettings']['tcpSettings'] = stream.get('tcpSettings', {})
        
    # Копируем настройки безопасности
    if security == 'tls':
        outbound['streamSettings']['tlsSettings'] = stream.get('tlsSettings', {})
    elif security == 'reality':
        outbound['streamSettings']['realitySettings'] = stream.get('realitySettings', {})
        # Для reality обычно нужен flow
        outbound['settings']['vnext'][0]['users'][0]['flow'] = 'xtls-rprx-vision'

    final_config = {
        "log": {
            "loglevel": "warning"
        },
        "inbounds": [
            {
                "port": 1080,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {
                    "udp": True
                }
            }
        ],
        "outbounds": [
            outbound,
            {
                "protocol": "freedom",
                "tag": "direct"
            }
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "direct"
                }
            ]
        }
    }
    
    return json.dumps(final_config, indent=2, ensure_ascii=False)


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
