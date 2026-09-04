"""Panel-neutral contracts for subscription-only VPN operations."""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class VPNAPIError(Exception):
    """Base error raised while communicating with a VPN panel."""


class PanelErrorKind(str, Enum):
    """Stable internal reason classes used by panel services and logs."""

    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    NETWORK = "network"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    REJECTED = "rejected"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_API = "unsupported_api"
    AUTH_BOOTSTRAP_FAILED = "auth_bootstrap_failed"


class PanelRequestError(VPNAPIError):
    """Typed panel failure without exposing response bodies or credentials."""

    def __init__(
        self,
        kind: PanelErrorKind,
        *,
        endpoint: str,
        status: Optional[int] = None,
        detail: str = "",
    ) -> None:
        self.kind = kind
        self.endpoint = endpoint
        self.status = status
        self.detail = detail
        summary = f"{kind.value}: {endpoint}"
        if status is not None:
            summary += f" (HTTP {status})"
        if detail:
            summary += f": {detail}"
        super().__init__(summary)


class PanelRejectedError(PanelRequestError):
    """Deterministic success=false rejection returned by a panel API."""

    def __init__(
        self,
        *,
        endpoint: str,
        status: Optional[int] = None,
        detail: str = "",
        recovered_record: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            PanelErrorKind.REJECTED,
            endpoint=endpoint,
            status=status,
            detail=detail,
        )
        self.recovered_record = recovered_record


@dataclass(frozen=True)
class PanelDatabaseBackup:
    """Downloaded panel backup and its detected format."""

    data: bytes
    extension: str
    db_kind: str


@dataclass(frozen=True)
class PanelInboundDescriptor:
    """Topology metadata for one inbound eligible for subscriptions."""

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
        return self.enabled and self.node_enabled is not False

    @property
    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "Inbound is disabled"
        if self.node_enabled is False:
            return "Inbound node is disabled"
        return ""

    def as_inbound(self) -> Dict[str, Any]:
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
    """Normalized state of one logical client returned by Clients API."""

    email: str
    client: Dict[str, Any] = field(default_factory=dict)
    inbound_ids: set[int] = field(default_factory=set)
    out_of_scope_inbound_ids: set[int] = field(default_factory=set)
    unavailable_inbound_ids: set[int] = field(default_factory=set)
    placements: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    traffic_used: int = 0
    traffic_known: bool = False
    total_gb: int = 0
    expiry_time: int = 0
    enable: bool = True
    sub_id: str = ""
    limit_ip: int = 1
    limit_hwid: int = 0
    reset: int = 0
    details_complete: bool = True


@dataclass(frozen=True)
class PanelClientLimits:
    """Effective pair of mutually exclusive panel client limits."""

    limit_ip: int
    limit_hwid: int


@dataclass(frozen=True)
class PanelClientDevice:
    """Normalized hardware device registered for one logical client."""

    id: str
    first_seen: Optional[int] = None
    last_seen: Optional[int] = None
    user_agent: str = ""
    device_os: str = ""
    os_version: str = ""
    device_model: str = ""


@dataclass
class PanelServerSnapshot:
    """Complete in-memory topology and logical-client state for one pass."""

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
        return {
            inbound_id: dict(state.placements.get(inbound_id) or state.client)
            for inbound_id in state.inbound_ids
        }


@dataclass
class PanelProvisionResult:
    """Result of creating or repairing one logical subscription client."""

    email: str
    sub_id: str
    attached_inbound_ids: set[int] = field(default_factory=set)
    failed_inbound_ids: Dict[int, str] = field(default_factory=dict)
    complete: bool = False
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
    """Normalize a full or lightweight inbound response."""
    try:
        inbound_id = int(inbound.get("id"))
    except (AttributeError, TypeError, ValueError):
        return None

    protocol = str(inbound.get("protocol") or "").strip().lower()
    remark = str(inbound.get("remark") or "")
    stream = _load_settings(inbound.get("streamSettings", {}))
    settings = _load_settings(inbound.get("settings", {}))
    tls_flow_capable = _panel_bool(inbound.get("tlsFlowCapable"), False)
    if not tls_flow_capable and protocol == "vless":
        network = str(stream.get("network") or "tcp").lower()
        security = str(stream.get("security") or "none").lower()
        tls_flow_capable = network == "tcp" and security in {"reality", "tls"}
    try:
        port = int(inbound.get("port")) if inbound.get("port") is not None else None
    except (TypeError, ValueError):
        port = None
    try:
        node_id = int(inbound.get("nodeId")) if inbound.get("nodeId") not in (None, "") else None
    except (TypeError, ValueError):
        node_id = None
    return PanelInboundDescriptor(
        id=inbound_id,
        protocol=protocol,
        remark=remark,
        tag=str(inbound.get("tag") or ""),
        port=port,
        tls_flow_capable=tls_flow_capable,
        flow="xtls-rprx-vision" if tls_flow_capable and protocol == "vless" else "",
        ss_method=str(inbound.get("ssMethod") or settings.get("method") or ""),
        ignored=remark.lstrip().startswith("--!"),
        enabled=_panel_bool(inbound.get("enable"), True),
        node_id=node_id,
        node_enabled=_optional_panel_bool(inbound.get("nodeEnabled")),
        raw=dict(inbound),
    )


class BaseVPNClient(abc.ABC):
    """Subscription-only client contract implemented by supported panels."""

    @abc.abstractmethod
    async def login(self) -> bool:
        pass

    @abc.abstractmethod
    async def validate_connection(self) -> bool:
        pass

    @abc.abstractmethod
    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        pass

    async def get_inbound_descriptors(
        self,
        *,
        include_ignored: bool = False,
    ) -> List[PanelInboundDescriptor]:
        descriptors = [
            descriptor
            for descriptor in (
                build_inbound_descriptor(item)
                for item in await self.get_inbounds(include_ignored=True)
            )
            if descriptor is not None
        ]
        return descriptors if include_ignored else [item for item in descriptors if not item.ignored]

    @abc.abstractmethod
    async def provision_client(
        self,
        *,
        email: str,
        total_gb: int = 0,
        total_gb_bytes: Optional[int] = None,
        expire_days: int = 0,
        expiry_time_ms: Optional[int] = None,
        limit_ip: int = 1,
        limit_hwid: int = 0,
        enable: bool = True,
        tg_id: str = "",
        sub_id: Optional[str] = None,
        inbound_ids: Optional[Iterable[int]] = None,
    ) -> PanelProvisionResult:
        pass

    @abc.abstractmethod
    async def get_sync_snapshot(self) -> PanelServerSnapshot:
        pass

    @abc.abstractmethod
    async def get_server_status(self) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_online_clients_count(self) -> int:
        pass

    @abc.abstractmethod
    async def get_nodes(self) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def delete_client(self, email: str) -> bool:
        pass

    @abc.abstractmethod
    async def reset_client_traffic(self, email: str) -> bool:
        pass

    @abc.abstractmethod
    async def update_client_limit(self, email: str, total_gb_bytes: int) -> bool:
        pass

    @abc.abstractmethod
    async def extend_client_expiry(self, email: str, days: int) -> bool:
        pass

    @abc.abstractmethod
    async def get_subscription_link(self, sub_id: str) -> Optional[str]:
        pass

    async def refresh_capabilities(self) -> bool:
        """Refresh capability metadata before a capability-gated mutation."""
        return bool(await self.login())

    def supports_client_external_links(self) -> bool:
        """Return whether this panel can compose client subscription feeds."""
        return False

    def supports_client_hwids(self) -> bool:
        """Return whether per-client HWID limits and device routes exist."""
        return False

    async def get_client_devices(self, email: str) -> List[PanelClientDevice]:
        """Return devices registered for one logical client."""
        raise NotImplementedError("Client HWIDs are not supported")

    async def delete_client_device(self, email: str, device_id: str) -> bool:
        """Delete one registered device from a logical client."""
        raise NotImplementedError("Client HWIDs are not supported")

    async def get_client_external_links(self, email: str) -> List[Dict[str, Any]]:
        """Return the complete external-link list for one logical client."""
        raise NotImplementedError("Client external links are not supported")

    async def replace_client_external_links(
        self,
        email: str,
        links: Iterable[Dict[str, Any]],
    ) -> bool:
        """Replace the complete external-link list for one logical client."""
        raise NotImplementedError("Client external links are not supported")

    @abc.abstractmethod
    async def get_database_backup(self) -> PanelDatabaseBackup:
        pass

    @abc.abstractmethod
    async def close(self) -> None:
        pass
