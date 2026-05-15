from typing import Optional, Dict, Any, List
from .base import BaseVPNClient, VPNAPIError
import logging

logger = logging.getLogger(__name__)

class MarzbanClient(BaseVPNClient):
    """Клиент для работы с Marzban API."""
    
    def __init__(self, server: dict):
            super().__init__(server)

    async def login(self) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_inbounds(self) -> List[Dict[str, Any]]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_server_status(self) -> Dict[str, Any]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_stats(self) -> Dict[str, Any]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_online_clients_count(self) -> int:
            raise NotImplementedError("Marzban is not supported yet")

    async def add_client(self, inbound_id: int, email: str, total_gb: int=0, expire_days: int=30, limit_ip: int=1, enable: bool=True, tg_id: str='', flow: str='', sub_id: Optional[str]=None) -> Dict[str, Any]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_inbound_flow(self, inbound_id: int) -> str:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
            raise NotImplementedError("Marzban is not supported yet")

    async def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def update_client_traffic_limit(self, inbound_id: int, client_uuid: str, email: str, total_gb: int) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def disable_reset_for_all_clients(self) -> int:
            raise NotImplementedError("Marzban is not supported yet")

    async def extend_client_expiry(self, inbound_id: int, client_uuid: str, email: str, days: int) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_client_config(self, email: str) -> Optional[Dict[str, Any]]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_subscription_link(self, sub_id: str) -> Optional[str]:
            raise NotImplementedError("Marzban is not supported yet")

    async def get_database_backup(self) -> bytes:
            raise NotImplementedError("Marzban is not supported yet")

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def update_client_limit(self, inbound_id: int, client_uuid: str, email: str, total_gb_bytes: int) -> bool:
            raise NotImplementedError("Marzban is not supported yet")

    async def close(self):
            raise NotImplementedError("Marzban is not supported yet")
