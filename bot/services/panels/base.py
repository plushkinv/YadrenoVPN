import abc
from abc import abstractmethod
from typing import Optional, Dict, Any, List

class VPNAPIError(Exception):
    """Ошибка при работе с VPN API."""
    pass

class BaseVPNClient(abc.ABC):
    """Базовый клиент для работы с VPN-панелями."""
    
    def __init__(self, server: dict):
        pass

    @abstractmethod
    async def login(self) -> bool:
        pass

    @abstractmethod
    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_server_status(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_online_clients_count(self) -> int:
        pass

    @abstractmethod
    async def get_nodes(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def add_client(self, inbound_id: int, email: str, total_gb: int=0, expire_days: int=30, limit_ip: int=1, enable: bool=True, tg_id: str='', flow: str='', sub_id: Optional[str]=None) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_inbound_flow(self, inbound_id: int) -> str:
        pass

    @abstractmethod
    async def get_client_stats(self, email: str, resolve_inbound: bool = True) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
        pass

    @abstractmethod
    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        pass

    @abstractmethod
    async def update_client_traffic_limit(self, inbound_id: int, client_uuid: str, email: str, total_gb: int) -> bool:
        pass

    @abstractmethod
    async def disable_reset_for_all_clients(self) -> int:
        pass

    @abstractmethod
    async def extend_client_expiry(self, inbound_id: int, client_uuid: str, email: str, days: int) -> bool:
        pass

    @abstractmethod
    async def get_client_config(self, email: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_subscription_link(self, sub_id: str) -> Optional[str]:
        pass

    @abstractmethod
    async def get_database_backup(self) -> bytes:
        pass

    @abstractmethod
    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        pass

    @abstractmethod
    async def update_client_limit(self, inbound_id: int, client_uuid: str, email: str, total_gb_bytes: int) -> bool:
        pass

    @abstractmethod
    async def close(self):
        pass
