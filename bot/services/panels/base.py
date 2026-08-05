import abc
from abc import abstractmethod
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Iterable, List

class VPNAPIError(Exception):
    """Error when working with VPN API."""
    pass


class PanelRejectedError(VPNAPIError):
    """Deterministic business-level rejection returned by a panel API."""

    def __init__(
        self,
        message: str,
        *,
        recovered_record: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.recovered_record = recovered_record


@dataclass(frozen=True)
class PanelDatabaseBackup:
    """The downloaded backup file of the panel and its actual format."""

    data: bytes
    extension: str
    db_kind: str


@dataclass(frozen=True)
class PanelInboundDescriptor:
    """Lightweight protocol metadata for one manageable inbound."""

    id: int
    protocol: str
    remark: str = ""
    tag: str = ""
    port: Optional[int] = None
    tls_flow_capable: bool = False
    flow: str = ""
    ss_method: str = ""
    ignored: bool = False
    enabled: bool = True
    node_id: Optional[int] = None
    node_enabled: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def available(self) -> bool:
        """Whether the inbound can participate in a panel mutation now."""
        return self.enabled and self.node_enabled is not False

    @property
    def unavailable_reason(self) -> str:
        """Return a stable reason for an explicitly unavailable inbound."""
        if not self.enabled:
            return "Inbound is disabled"
        if self.node_enabled is False:
            return "Inbound node is disabled"
        return ""

    def as_inbound(self) -> Dict[str, Any]:
        """Return a legacy-compatible metadata-only inbound dictionary."""
        result = dict(self.raw)
        result.update(
            {
                "id": self.id,
                "protocol": self.protocol,
                "remark": self.remark,
                "tag": self.tag,
                "port": self.port,
                "tlsFlowCapable": self.tls_flow_capable,
                "ssMethod": self.ss_method,
                "enable": self.enabled,
            }
        )
        if self.node_id is not None:
            result["nodeId"] = self.node_id
        return result


@dataclass
class PanelClientState:
    """Normalized state of one logical panel client."""

    email: str
    client: Dict[str, Any] = field(default_factory=dict)
    inbound_ids: set[int] = field(default_factory=set)
    unavailable_inbound_ids: set[int] = field(default_factory=set)
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
    details_complete: bool = True


@dataclass
class PanelServerSnapshot:
    """Complete in-memory panel state used by one synchronization pass."""

    api_profile: str
    inbounds: List[Dict[str, Any]]
    clients: Dict[str, PanelClientState]
    unavailable_inbound_ids: set[int] = field(default_factory=set)

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


@dataclass
class PanelProvisionResult:
    """Canonical result of creating or repairing one logical panel client."""

    email: str
    sub_id: str
    primary_inbound_id: Optional[int]
    credential: str
    attached_inbound_ids: set[int] = field(default_factory=set)
    failed_inbound_ids: Dict[int, str] = field(default_factory=dict)
    complete: bool = False
    api_profile: str = "legacy_inbounds"
    snapshot: Optional[PanelServerSnapshot] = None

    @property
    def created_count(self) -> int:
        return len(self.attached_inbound_ids)


def _panel_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _panel_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_panel_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    return _panel_bool(value)


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


def build_inbound_descriptor(inbound: Dict[str, Any]) -> Optional[PanelInboundDescriptor]:
    """Normalize a full or lightweight inbound into one descriptor."""
    try:
        inbound_id = int(inbound.get("id"))
    except (AttributeError, TypeError, ValueError):
        return None

    protocol = str(inbound.get("protocol") or "").strip().lower()
    remark = str(inbound.get("remark") or "")
    stream = _load_settings(inbound.get("streamSettings", {}))
    settings = _load_settings(inbound.get("settings", {}))
    raw_tls_flow_capable = inbound.get("tlsFlowCapable", False)
    if isinstance(raw_tls_flow_capable, str):
        tls_flow_capable = raw_tls_flow_capable.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    else:
        tls_flow_capable = bool(raw_tls_flow_capable)
    if not tls_flow_capable and protocol == "vless":
        network = str(stream.get("network") or "tcp").lower()
        security = str(stream.get("security") or "none").lower()
        tls_flow_capable = network == "tcp" and security in {"reality", "tls"}
    flow = "xtls-rprx-vision" if tls_flow_capable and protocol == "vless" else ""
    ss_method = str(
        inbound.get("ssMethod")
        or settings.get("method")
        or ""
    )
    try:
        port = int(inbound.get("port")) if inbound.get("port") is not None else None
    except (TypeError, ValueError):
        port = None
    try:
        node_id = (
            int(inbound.get("nodeId"))
            if inbound.get("nodeId") not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        node_id = None
    return PanelInboundDescriptor(
        id=inbound_id,
        protocol=protocol,
        remark=remark,
        tag=str(inbound.get("tag") or ""),
        port=port,
        tls_flow_capable=tls_flow_capable,
        flow=flow,
        ss_method=ss_method,
        ignored=remark.lstrip().startswith("--!"),
        enabled=_panel_bool(inbound.get("enable"), True),
        node_id=node_id,
        node_enabled=_optional_panel_bool(inbound.get("nodeEnabled")),
        raw=dict(inbound),
    )


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

    async def get_inbound_descriptors(
        self,
        *,
        subscription_mode: bool = False,
        include_ignored: bool = False,
    ) -> List[PanelInboundDescriptor]:
        """Return normalized inbound metadata with a legacy-compatible fallback."""
        inbounds = (
            await self.get_subscription_inbounds(include_ignored=True)
            if subscription_mode
            else await self.get_inbounds(include_ignored=True)
        )
        descriptors = [
            descriptor
            for descriptor in (build_inbound_descriptor(item) for item in inbounds)
            if descriptor is not None
        ]
        if include_ignored:
            return descriptors
        return [descriptor for descriptor in descriptors if not descriptor.ignored]

    async def provision_client(
        self,
        *,
        email: str,
        total_gb: int = 0,
        total_gb_bytes: Optional[int] = None,
        expire_days: int = 0,
        expiry_time_ms: Optional[int] = None,
        limit_ip: int = 1,
        enable: bool = True,
        tg_id: str = "",
        sub_id: Optional[str] = None,
        subscription_mode: bool = False,
        inbound_ids: Optional[Iterable[int]] = None,
    ) -> PanelProvisionResult:
        """Create a logical client through the adapter's compatible point API."""
        all_descriptors = await self.get_inbound_descriptors(
            subscription_mode=subscription_mode,
            include_ignored=False,
        )
        requested = {int(value) for value in inbound_ids} if inbound_ids is not None else None
        known_by_id = {item.id: item for item in all_descriptors}
        descriptors = [
            item
            for item in all_descriptors
            if item.available and (requested is None or item.id in requested)
        ]

        attached: set[int] = set()
        descriptor_ids = {item.id for item in descriptors}
        failed: Dict[int, str] = {}
        for inbound_id in sorted((requested or set()) - descriptor_ids):
            descriptor = known_by_id.get(inbound_id)
            failed[inbound_id] = (
                descriptor.unavailable_reason
                if descriptor is not None and not descriptor.available
                else "Inbound is missing, ignored, or incompatible"
            )
        results: Dict[int, Dict[str, Any]] = {}
        for descriptor in descriptors:
            try:
                result = await self.add_client(
                    inbound_id=descriptor.id,
                    email=email,
                    total_gb=total_gb,
                    total_gb_bytes=total_gb_bytes,
                    expire_days=expire_days,
                    expiry_time_ms=expiry_time_ms,
                    limit_ip=limit_ip,
                    enable=enable,
                    tg_id=tg_id,
                    flow=descriptor.flow,
                    sub_id=sub_id,
                )
                attached.add(descriptor.id)
                results[descriptor.id] = result
            except Exception as exc:
                failed[descriptor.id] = str(exc)

        primary_id = min(attached) if attached else None
        primary = results.get(primary_id, {}) if primary_id is not None else {}
        canonical_sub_id = str(primary.get("sub_id") or sub_id or "")
        credential = str(
            primary.get("uuid")
            or primary.get("password")
            or primary.get("auth")
            or email
        )
        return PanelProvisionResult(
            email=email,
            sub_id=canonical_sub_id,
            primary_inbound_id=primary_id,
            credential=credential,
            attached_inbound_ids=attached,
            failed_inbound_ids=failed,
            complete=(
                bool(descriptors)
                and len(attached) == len(descriptors)
                and not failed
            ),
            snapshot=None,
        )

    async def get_client_links(self, email: str) -> List[str]:
        """Return ready share links when the panel exposes a client-level API."""
        return []

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
    async def add_client(
        self,
        inbound_id: int,
        email: str,
        total_gb: int = 0,
        expire_days: int = 0,
        limit_ip: int = 1,
        enable: bool = True,
        tg_id: str = "",
        flow: str = "",
        sub_id: Optional[str] = None,
        total_gb_bytes: Optional[int] = None,
        expiry_time_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
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
