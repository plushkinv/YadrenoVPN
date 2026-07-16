import abc
from abc import abstractmethod
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

class VPNAPIError(Exception):
    """Error when working with VPN API."""
    pass

@dataclass(frozen=True)
class PanelDatabaseBackup:
    """The downloaded backup file of the panel and its actual format."""

    data: bytes
    extension: str
    db_kind: str


@dataclass
class PanelClientState:
    """Normalized state of one logical panel client."""

    email: str
    client: Dict[str, Any] = field(default_factory=dict)
    inbound_ids: set[int] = field(default_factory=set)
    placements: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    traffic_used: int = 0
    traffic_known: bool = False
    total_gb: int = 0
    expiry_time: int = 0
    enable: bool = True
    sub_id: str = ""
    limit_ip: int = 1
    reset: int = 0
    source: str = "legacy_inbounds"


@dataclass
class PanelServerSnapshot:
    """Complete in-memory panel state used by one synchronization pass."""

    api_profile: str
    inbounds: List[Dict[str, Any]]
    clients: Dict[str, PanelClientState]

    def get_client(self, email: Any) -> Optional[PanelClientState]:
        normalized = str(email or "").strip().lower()
        return self.clients.get(normalized) if normalized else None

    def presence_for_email(self, email: Any) -> Dict[int, Dict[str, Any]]:
        state = self.get_client(email)
        if not state:
            return {}
        presence: Dict[int, Dict[str, Any]] = {}
        for inbound_id in state.inbound_ids:
            placement = state.placements.get(inbound_id)
            presence[inbound_id] = dict(placement or state.client)
        return presence


def _panel_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _load_settings(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def build_legacy_panel_snapshot(
    inbounds: List[Dict[str, Any]],
    api_profile: str = "legacy",
) -> PanelServerSnapshot:
    """Build a normalized snapshot from one legacy inbounds response."""
    clients: Dict[str, PanelClientState] = {}

    def get_state(email: Any) -> Optional[PanelClientState]:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return None
        state = clients.get(normalized)
        if state is None:
            state = PanelClientState(email=str(email).strip())
            clients[normalized] = state
        return state

    for inbound in inbounds or []:
        try:
            inbound_id = int(inbound.get("id"))
        except (TypeError, ValueError):
            continue

        settings = _load_settings(inbound.get("settings", {}))
        for raw_client in settings.get("clients", []) or []:
            if not isinstance(raw_client, dict):
                continue
            state = get_state(raw_client.get("email"))
            if state is None:
                continue
            client = dict(raw_client)
            state.inbound_ids.add(inbound_id)
            state.placements[inbound_id] = client
            if not state.client:
                state.client = client
            state.total_gb = max(state.total_gb, _panel_int(client.get("totalGB")))
            state.expiry_time = max(state.expiry_time, _panel_int(client.get("expiryTime")))
            state.enable = bool(client.get("enable", state.enable))
            state.sub_id = str(client.get("subId") or state.sub_id)
            state.limit_ip = _panel_int(client.get("limitIp"), state.limit_ip)
            state.reset = _panel_int(client.get("reset"), state.reset)

        for stats in inbound.get("clientStats", []) or []:
            if not isinstance(stats, dict):
                continue
            state = get_state(stats.get("email"))
            if state is None:
                continue
            state.traffic_known = True
            state.traffic_used += _panel_int(stats.get("up")) + _panel_int(stats.get("down"))
            state.total_gb = max(
                state.total_gb,
                _panel_int(stats.get("total") or stats.get("totalGB")),
            )
            state.expiry_time = max(
                state.expiry_time,
                _panel_int(stats.get("expiryTime") or stats.get("expiry_time")),
            )

    for state in clients.values():
        if state.placements and not state.traffic_known:
            # Legacy panels omit clientStats for a client that has not used traffic.
            state.traffic_known = True
            state.traffic_used = 0

    return PanelServerSnapshot(
        api_profile=api_profile,
        inbounds=list(inbounds or []),
        clients=clients,
    )

class BaseVPNClient(abc.ABC):
    """Basic client for working with VPN panels."""
    
    def __init__(self, server: dict):
        pass

    @abstractmethod
    async def login(self) -> bool:
        pass

    @abstractmethod
    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        pass

    async def get_subscription_inbounds(
        self,
        include_ignored: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return inbounds that can participate in a shared subscription."""
        return await self.get_inbounds(include_ignored=include_ignored)

    async def get_sync_snapshot(
        self,
        subscription_mode: bool = False,
    ) -> PanelServerSnapshot:
        """Download one complete server snapshot for batch synchronization."""
        if subscription_mode:
            inbounds = await self.get_subscription_inbounds(include_ignored=True)
        else:
            inbounds = await self.get_inbounds(include_ignored=True)
        return build_legacy_panel_snapshot(inbounds)

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
    async def get_database_backup(self) -> PanelDatabaseBackup:
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
