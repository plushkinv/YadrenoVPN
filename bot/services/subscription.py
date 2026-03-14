"""
Сервис формирования единой подписки из всех активных X-UI серверов.
"""
import json
import logging
import secrets
import uuid
from typing import Dict, Any, List

from config import DEFAULT_LIMIT_IP, DEFAULT_TOTAL_GB
from bot.services.vpn_api import get_client
from bot.utils.key_generator import generate_link
from database.requests import (
    get_active_servers,
    get_setting,
    get_vpn_key_nodes,
    get_vpn_key_subscription_token,
    replace_vpn_key_nodes,
    set_vpn_key_subscription_token,
    update_payment_key_id,
    update_vpn_key_config,
)

logger = logging.getLogger(__name__)
DEFAULT_SUBSCRIPTION_BASE_URL = "https://q1sub.servernux.com"


def _build_shared_email(telegram_id: int, key_id: int) -> str:
    suffix = uuid.uuid4().hex[:6]
    return f"sub_{telegram_id}_{key_id}_{suffix}"


def build_subscription_url(token: str, primary_host: str = "") -> str:
    """
    Собирает публичный URL подписки.
    """
    raw_base_url = get_setting("subscription_base_url", DEFAULT_SUBSCRIPTION_BASE_URL)
    base_url = (raw_base_url or DEFAULT_SUBSCRIPTION_BASE_URL).strip().rstrip("/")
    if base_url:
        return f"{base_url}/sub/{token}"

    port = int(get_setting("subscription_bind_port", "8080") or 8080)
    host = primary_host or "127.0.0.1"
    return f"http://{host}:{port}/sub/{token}"


def _token_for_key(key_id: int) -> str:
    existing = get_vpn_key_subscription_token(key_id)
    if existing:
        return existing

    while True:
        token = secrets.token_urlsafe(24).replace("-", "").replace("_", "")
        try:
            if set_vpn_key_subscription_token(key_id, token):
                return token
        except Exception:
            logger.warning("Повторная попытка генерации subscription token для key_id=%s", key_id)


async def provision_subscription_for_new_order(
    order: Dict[str, Any],
    telegram_id: int
) -> Dict[str, Any]:
    """
    Создаёт клиентов на всех активных серверах и собирает единую подписку.
    """
    key_id = order.get("vpn_key_id")
    if not key_id:
        raise RuntimeError("У заказа отсутствует vpn_key_id")

    existing_nodes = get_vpn_key_nodes(key_id)
    existing_token = get_vpn_key_subscription_token(key_id)
    if existing_nodes and existing_token:
        return {
            "key_id": key_id,
            "subscription_url": build_subscription_url(existing_token),
            "nodes_count": len(existing_nodes),
        }

    servers = get_active_servers()
    if not servers:
        raise RuntimeError("Нет активных серверов")

    days = order.get("period_days") or order.get("duration_days") or 30
    limit_gb = int(DEFAULT_TOTAL_GB / (1024 ** 3))
    shared_uuid = str(uuid.uuid4())
    shared_sub_id = uuid.uuid4().hex
    panel_email = _build_shared_email(telegram_id, key_id)

    nodes: List[Dict[str, Any]] = []
    errors: List[str] = []

    for server in servers:
        server_id = server["id"]
        try:
            client = await get_client(server_id)
            inbounds = await client.get_inbounds()
            vless_inbounds = [ib for ib in inbounds if (ib.get("protocol") or "").lower() == "vless"]

            for inbound in vless_inbounds:
                inbound_id = inbound["id"]

                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                exists = any(c.get("email") == panel_email for c in clients)

                if not exists:
                    flow = await client.get_inbound_flow(inbound_id)
                    await client.add_client(
                        inbound_id=inbound_id,
                        email=panel_email,
                        total_gb=limit_gb,
                        expire_days=days,
                        limit_ip=DEFAULT_LIMIT_IP,
                        enable=True,
                        tg_id=str(telegram_id),
                        flow=flow,
                        client_uuid=shared_uuid,
                        sub_id=shared_sub_id,
                    )

                config = await client.get_client_config_for_inbound(panel_email, inbound_id)
                if not config:
                    continue

                access_link = generate_link(config)
                nodes.append({
                    "server_id": server_id,
                    "panel_inbound_id": inbound_id,
                    "panel_email": panel_email,
                    "client_uuid": shared_uuid,
                    "access_link": access_link,
                })
        except Exception as e:
            errors.append(f"{server.get('name', server_id)}: {e}")

    if not nodes:
        details = "\n".join(errors[:3]) if errors else "нет VLESS inbound"
        raise RuntimeError(f"Не удалось сформировать подписку: {details}")

    first_node = nodes[0]
    update_vpn_key_config(
        key_id=key_id,
        server_id=first_node["server_id"],
        panel_inbound_id=first_node["panel_inbound_id"],
        panel_email=panel_email,
        client_uuid=shared_uuid
    )
    update_payment_key_id(order["order_id"], key_id)
    replace_vpn_key_nodes(key_id, nodes)

    token = _token_for_key(key_id)
    primary_host = servers[0].get("host", "")
    return {
        "key_id": key_id,
        "subscription_url": build_subscription_url(token, primary_host=primary_host),
        "nodes_count": len(nodes),
    }
