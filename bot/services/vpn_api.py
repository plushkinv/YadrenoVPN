"""Subscription-only facade for supported VPN panel operations."""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from bot.services.panel_key_state import should_panel_client_exist
from bot.services.panel_sync_coordinator import panel_sync_coordinator, regular_panel_operation
from bot.utils.panel_email import is_managed_panel_email

from .panels.base import (
    BaseVPNClient,
    PanelClientDevice,
    PanelClientLimits,
    PanelClientState,
    PanelErrorKind,
    PanelInboundDescriptor,
    PanelProvisionResult,
    PanelRejectedError,
    PanelRequestError,
    PanelServerSnapshot,
    VPNAPIError,
)
from .panels.xui import XUIClient


logger = logging.getLogger(__name__)

_clients: Dict[int, BaseVPNClient] = {}
_ensure_locks: Dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def _unlocked_preview():
    yield


async def get_client_inbound_descriptors(
    client: BaseVPNClient,
    *,
    include_ignored: bool = False,
) -> List[PanelInboundDescriptor]:
    """Return the subscription topology exposed by a panel adapter."""
    result = client.get_inbound_descriptors(include_ignored=include_ignored)
    return await result if inspect.isawaitable(result) else result


async def provision_client_on_server(
    *,
    server_id: int,
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
    client: Optional[BaseVPNClient] = None,
) -> PanelProvisionResult:
    """Create or repair one logical client through unified Clients API."""
    if not is_managed_panel_email(email):
        raise VPNAPIError(f"Refusing to provision unmanaged panel client: {email!r}")
    panel_client = client or await get_client(server_id)
    if int(limit_hwid) > 0:
        await refresh_client_capabilities(panel_client)
        if not supports_client_hwids(panel_client):
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_VERSION,
                endpoint="/panel/api/clients/add",
                detail="client HWID limits require 3X-UI 3.7.0+",
            )
    result = panel_client.provision_client(
        email=email,
        total_gb=total_gb,
        total_gb_bytes=total_gb_bytes,
        expire_days=expire_days,
        expiry_time_ms=expiry_time_ms,
        limit_ip=limit_ip,
        limit_hwid=limit_hwid,
        enable=enable,
        tg_id=tg_id,
        sub_id=sub_id,
        inbound_ids=inbound_ids,
    )
    result = await result if inspect.isawaitable(result) else result
    if not isinstance(result, PanelProvisionResult):
        raise VPNAPIError("Panel adapter returned an invalid provisioning result")
    return result


def get_client_from_server_data(server: Dict[str, Any]) -> BaseVPNClient:
    """Return the cached client for a saved server."""
    server_id = int(server["id"])
    if server_id not in _clients:
        _clients[server_id] = XUIClient(server)
    return _clients[server_id]


async def invalidate_client_cache(server_id: int) -> None:
    client = _clients.pop(int(server_id), None)
    if client is None:
        return
    try:
        await client.close()
    except Exception:
        logger.exception("Could not close panel client server_id=%s", server_id)


async def close_all_clients() -> None:
    clients = list(_clients.items())
    _clients.clear()
    for server_id, client in clients:
        try:
            await client.close()
        except Exception:
            logger.exception("Could not close panel client server_id=%s", server_id)


async def get_client(server_id: int) -> XUIClient:
    from database.requests import get_server_by_id

    normalized_id = int(server_id)
    cached = _clients.get(normalized_id)
    if cached is not None:
        return cached  # type: ignore[return-value]
    server = get_server_by_id(normalized_id)
    if not server:
        raise ValueError(f"Сервер с ID {normalized_id} не найден")
    return get_client_from_server_data(server)  # type: ignore[return-value]


def supports_client_external_links(client: BaseVPNClient) -> bool:
    """Return the adapter's stored, side-effect-free composition capability."""
    return bool(client.supports_client_external_links())


def supports_client_hwids(client: BaseVPNClient) -> bool:
    """Return the adapter's stored, side-effect-free HWID capability."""
    checker = getattr(client, "supports_client_hwids", None)
    return bool(checker()) if callable(checker) else False


async def refresh_client_capabilities(
    client: BaseVPNClient,
    *,
    force: bool = False,
) -> bool:
    """Refresh live capability metadata before a gated panel mutation."""
    refresher = getattr(client, "refresh_capabilities", None)
    if callable(refresher):
        try:
            result = refresher(force=force)
        except TypeError:
            result = refresher()
        result = await result if inspect.isawaitable(result) else result
        return bool(result)
    return bool(await client.login())


async def get_client_external_links(
    *,
    server_id: int,
    panel_email: str,
    client: Optional[BaseVPNClient] = None,
) -> List[Dict[str, Any]]:
    """Read all external links for one bot-owned logical client."""
    if not is_managed_panel_email(panel_email):
        raise VPNAPIError(
            f"Refusing to read external links for unmanaged panel client: {panel_email!r}"
        )
    panel_client = client or await get_client(server_id)
    links = panel_client.get_client_external_links(panel_email)
    links = await links if inspect.isawaitable(links) else links
    if not isinstance(links, list) or any(not isinstance(item, dict) for item in links):
        raise VPNAPIError("Panel adapter returned an invalid external-link collection")
    return [dict(item) for item in links]


async def get_key_devices_for_user(
    *,
    key_id: int,
    telegram_id: int,
) -> List[PanelClientDevice]:
    """Read current devices after verifying key ownership and configuration."""
    from database.requests import (
        DEVICE_LIMIT_MODE_HWID,
        get_device_limit_mode,
        get_key_details_for_user,
    )

    if get_device_limit_mode() != DEVICE_LIMIT_MODE_HWID:
        raise VPNAPIError("Client devices are unavailable in IP limit mode")
    key = get_key_details_for_user(int(key_id), int(telegram_id))
    if not key:
        raise VPNAPIError("VPN key is missing or belongs to another user")
    server_id = key.get("server_id")
    panel_email = str(key.get("panel_email") or "").strip()
    if not server_id or not key.get("sub_id") or not is_managed_panel_email(panel_email):
        raise VPNAPIError("VPN key is not configured on a panel")
    panel_client = await get_client(int(server_id))
    if not supports_client_hwids(panel_client):
        raise PanelRequestError(
            PanelErrorKind.UNSUPPORTED_VERSION,
            endpoint="/panel/api/clients/hwids/:email",
            detail="client HWIDs require 3X-UI 3.7.0+",
        )
    devices = panel_client.get_client_devices(panel_email)
    devices = await devices if inspect.isawaitable(devices) else devices
    if not isinstance(devices, list) or any(
        not isinstance(device, PanelClientDevice) for device in devices
    ):
        raise VPNAPIError("Panel adapter returned an invalid device collection")
    return devices


@regular_panel_operation
async def delete_key_device_for_user(
    *,
    key_id: int,
    telegram_id: int,
    device_id: str,
) -> bool:
    """Delete one device after repeating ownership checks inside the operation gate."""
    from database.requests import (
        DEVICE_LIMIT_MODE_HWID,
        get_device_limit_mode,
        get_key_details_for_user,
    )

    if get_device_limit_mode() != DEVICE_LIMIT_MODE_HWID:
        return False
    key = get_key_details_for_user(int(key_id), int(telegram_id))
    if not key:
        return False
    server_id = key.get("server_id")
    panel_email = str(key.get("panel_email") or "").strip()
    normalized_device_id = str(device_id or "").strip()
    if (
        not server_id
        or not key.get("sub_id")
        or not normalized_device_id.isascii()
        or not normalized_device_id.isdecimal()
        or int(normalized_device_id) <= 0
        or not is_managed_panel_email(panel_email)
    ):
        return False
    panel_client = await get_client(int(server_id))
    if not supports_client_hwids(panel_client):
        raise PanelRequestError(
            PanelErrorKind.UNSUPPORTED_VERSION,
            endpoint="/panel/api/clients/hwids/:email/:id",
            detail="client HWIDs require 3X-UI 3.7.0+",
        )
    result = panel_client.delete_client_device(panel_email, normalized_device_id)
    result = await result if inspect.isawaitable(result) else result
    return bool(result)


@regular_panel_operation
async def replace_client_external_links(
    *,
    server_id: int,
    panel_email: str,
    links: Iterable[Dict[str, Any]],
    client: Optional[BaseVPNClient] = None,
) -> bool:
    """Replace all external links for one bot-owned logical client."""
    if not is_managed_panel_email(panel_email):
        raise VPNAPIError(
            f"Refusing to write external links for unmanaged panel client: {panel_email!r}"
        )
    panel_client = client or await get_client(server_id)
    result = panel_client.replace_client_external_links(panel_email, links)
    result = await result if inspect.isawaitable(result) else result
    return bool(result)


async def test_server_connection(server_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the complete supported contract and return a neutral failure."""
    if (
        not str(server_data.get("api_token") or "").strip()
        and str(server_data.get("login") or "").strip()
        and str(server_data.get("password") or "").strip()
        and server_data.get("_force_auth_bootstrap") is not True
    ):
        from database.db_servers import get_panel_recovery_api_token

        reusable_token = get_panel_recovery_api_token(
            protocol=str(server_data.get("protocol") or "https"),
            host=str(server_data.get("host") or ""),
            port=int(server_data.get("port") or 0),
            web_base_path=str(server_data.get("web_base_path") or ""),
        )
        if reusable_token:
            server_data["api_token"] = reusable_token
            server_data["_shared_panel_auth"] = True
    client = XUIClient(server_data)
    try:
        await client.validate_connection()
        stats = await client.get_stats()
        return {
            "success": True,
            "message": "Подключение успешно!",
            "stats": stats,
        }
    except Exception:
        logger.exception(
            "Panel connection validation failed server_id=%s host=%s",
            server_data.get("id"),
            server_data.get("host"),
        )
        return {
            "success": False,
            "message": "Не удалось подключиться к панели",
            "stats": None,
        }
    finally:
        await client.close()


def format_traffic(bytes_count: int) -> str:
    value = int(bytes_count or 0)
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB"
    if value < 1024 ** 4:
        return f"{value / 1024 ** 3:.2f} GB"
    return f"{value / 1024 ** 4:.2f} TB"


def _traffic_remaining_bytes(key: Dict[str, Any]) -> int:
    limit = int(key.get("traffic_limit") or 0)
    if limit <= 0:
        return 0
    return max(0, limit - int(key.get("traffic_used") or 0))


def calculate_panel_total_for_key(
    key: Dict[str, Any],
    panel_used_bytes: int = 0,
) -> int:
    """Return panel totalGB bytes preserving cumulative DB accounting."""
    if int(key.get("traffic_limit") or 0) <= 0:
        return 0
    return max(0, int(panel_used_bytes or 0)) + _traffic_remaining_bytes(key)


def _base_traffic_limit_for_key(key: Dict[str, Any]) -> int:
    if key.get("tariff_system_type") == "admin_custom":
        override = key.get("traffic_limit_override")
        if override is not None:
            return max(0, int(override))
        return max(0, int(key.get("traffic_limit") or 0))
    return max(0, int(key.get("tariff_traffic_limit_gb") or 0)) * 1024 ** 3


def get_key_limit_ip(key: Dict[str, Any]) -> int:
    override = key.get("max_ips_override")
    if override is not None:
        return max(1, min(999, int(override)))
    tariff_max_ips = key.get("tariff_max_ips")
    if tariff_max_ips is None and key.get("tariff_id"):
        from database.db_tariffs import get_tariff_by_id

        tariff = get_tariff_by_id(int(key["tariff_id"]))
        tariff_max_ips = (tariff or {}).get("max_ips")
    return max(1, min(999, int(tariff_max_ips or 1)))


def resolve_panel_client_limits(
    limit_value: int,
    *,
    mode: Optional[str] = None,
) -> PanelClientLimits:
    """Resolve the tariff's single limit into the panel's two limit fields."""
    from database.requests import (
        DEVICE_LIMIT_MODE_HWID,
        get_device_limit_mode,
    )

    normalized_limit = max(1, min(999, int(limit_value or 1)))
    effective_mode = mode or get_device_limit_mode()
    if effective_mode == DEVICE_LIMIT_MODE_HWID:
        return PanelClientLimits(limit_ip=0, limit_hwid=normalized_limit)
    return PanelClientLimits(limit_ip=normalized_limit, limit_hwid=0)


def resolve_key_panel_limits(
    key: Dict[str, Any],
    *,
    mode: Optional[str] = None,
) -> PanelClientLimits:
    """Resolve one key's tariff/override limit for the current global mode."""
    return resolve_panel_client_limits(get_key_limit_ip(key), mode=mode)


def get_key_expiry_time_ms(key: Dict[str, Any]) -> int:
    expires_at = key.get("expires_at")
    if not expires_at:
        return 0
    try:
        value = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value > datetime.now(timezone.utc) + timedelta(days=90000):
            return 0
        return int(value.timestamp() * 1000)
    except (TypeError, ValueError):
        logger.warning("Could not parse key expiry: %r", expires_at)
        return 0


def _build_server_data_from_key(key: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": key.get("server_id"),
        "name": key.get("server_name"),
        "host": key.get("host"),
        "port": key.get("port"),
        "web_base_path": key.get("web_base_path"),
        "login": key.get("login"),
        "password": key.get("password"),
        "protocol": key.get("protocol", "https"),
        "api_token": key.get("api_token"),
        "panel_version": key.get("panel_version"),
        "panel_checked_at": key.get("panel_checked_at"),
        "inbound_group_id": key.get("inbound_group_id"),
    }


def _panel_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _panel_bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _traffic_used_from_record(data: Dict[str, Any]) -> Optional[int]:
    up = _panel_int(data.get("up"))
    down = _panel_int(data.get("down"))
    if up is not None or down is not None:
        return int(up or 0) + int(down or 0)
    for field_name in (
        "traffic_used",
        "trafficUsed",
        "usedTraffic",
        "usedBytes",
        "used_bytes",
        "used",
        "totalUsed",
    ):
        value = _panel_int(data.get(field_name))
        if value is not None:
            return value
    return None


def _cumulative_traffic_used_from_panel(
    key: Dict[str, Any],
    used_on_server: int,
    total_on_server: int,
) -> int:
    limit = int(key.get("traffic_limit") or 0)
    stored_used = int(key.get("traffic_used") or 0)
    if limit > 0 and total_on_server > 0:
        remaining = max(0, int(total_on_server) - int(used_on_server))
        return max(stored_used, max(0, limit - remaining))
    return max(stored_used, int(used_on_server))


async def get_key_traffic_snapshot(
    client: BaseVPNClient,
    key: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return normalized traffic from the logical Clients API record."""
    email = str(key.get("panel_email") or "").strip()
    if not email:
        return None
    try:
        stats = client.get_client_stats(email)
        stats = await stats if inspect.isawaitable(stats) else stats
    except Exception:
        logger.exception("Could not read client traffic email=%s", email)
        return None
    if not isinstance(stats, dict):
        return None
    used = _traffic_used_from_record(stats)
    if used is None:
        return None
    total = int(
        _panel_int(
            stats.get("total", stats.get("totalGB", stats.get("trafficLimit"))),
            0,
        )
        or 0
    )
    return {
        "traffic_used": _cumulative_traffic_used_from_panel(key, used, total),
        "panel_traffic_used": used,
        "totalGB": total,
        "expiryTime": int(
            _panel_int(stats.get("expiryTime", stats.get("expiry_time")), 0) or 0
        ),
    }


@regular_panel_operation
async def reset_key_traffic_if_active(key_id: int) -> bool:
    from database.requests import get_vpn_key_by_id

    key = get_vpn_key_by_id(key_id)
    if not key or not key.get("server_active"):
        return False
    email = key.get("panel_email")
    if not is_managed_panel_email(email):
        return False
    try:
        client = get_client_from_server_data(_build_server_data_from_key(key))
        return bool(await client.reset_client_traffic(str(email)))
    except Exception:
        logger.exception("Could not reset key traffic key_id=%s", key_id)
        return False


@regular_panel_operation
async def extend_key_on_server(key_id: int, days: int) -> bool:
    from database.requests import get_vpn_key_by_id

    key = get_vpn_key_by_id(key_id)
    if not key or not key.get("server_active"):
        return False
    email = key.get("panel_email")
    if not is_managed_panel_email(email):
        return False
    try:
        client = get_client_from_server_data(_build_server_data_from_key(key))
        return bool(await client.extend_client_expiry(str(email), int(days)))
    except Exception:
        logger.exception("Could not extend key on panel key_id=%s", key_id)
        return False


def restore_traffic_limit_in_db(key_id: int) -> bool:
    from database.requests import (
        get_vpn_key_by_id,
        reset_key_traffic_notification,
        update_key_traffic_limit,
    )

    key = get_vpn_key_by_id(key_id)
    if not key:
        return False
    traffic_limit = _base_traffic_limit_for_key(key)
    reset_key_traffic_notification(key_id)
    update_key_traffic_limit(key_id, traffic_limit)
    return True


@regular_panel_operation
async def restore_key_traffic_limit(key_id: int) -> bool:
    from database.requests import get_vpn_key_by_id

    if not restore_traffic_limit_in_db(key_id):
        return False
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get("server_active"):
        return True
    email = key.get("panel_email")
    if not is_managed_panel_email(email):
        return False
    try:
        client = get_client_from_server_data(_build_server_data_from_key(key))
        return bool(
            await client.update_client_limit(
                str(email),
                _base_traffic_limit_for_key(key),
            )
        )
    except Exception:
        logger.exception("Could not restore traffic limit key_id=%s", key_id)
        return False


def _client_needs_update(
    state: PanelClientState,
    *,
    expiry_time_ms: int,
    total_gb_bytes: int,
    enable: bool,
    limit_ip: Optional[int],
    limit_hwid: Optional[int],
    sub_id: str,
) -> bool:
    client = state.client
    checks = [
        (_panel_int(client.get("expiryTime"), state.expiry_time), expiry_time_ms),
        (_panel_int(client.get("totalGB"), state.total_gb), total_gb_bytes),
        (_panel_int(client.get("reset"), state.reset), 0),
    ]
    if limit_ip is not None:
        checks.append((_panel_int(client.get("limitIp"), state.limit_ip), limit_ip))
    if limit_hwid is not None:
        checks.append((
            _panel_int(client.get("limitHwid"), state.limit_hwid),
            limit_hwid,
        ))
    return (
        any(current != expected for current, expected in checks)
        or _panel_bool(client.get("enable"), state.enable) != enable
        or str(client.get("subId") or state.sub_id or "") != sub_id
    )


def _empty_sync_stats() -> Dict[str, int]:
    return {
        "created": 0,
        "deleted": 0,
        "enabled": 0,
        "disabled": 0,
        "updated": 0,
        "skipped": 0,
        "reset": 0,
        "errors": 0,
        "ok": 0,
    }


async def _ensure_subscription_keys_on_server_impl(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
    dry_run: bool = False,
    device_limit_mode: Optional[str] = None,
) -> Dict[str, int]:
    """Reconcile one database key with its logical panel client."""
    from database.requests import get_vpn_key_by_id

    stats = _empty_sync_stats()
    lock = _ensure_locks.setdefault(int(key_id), asyncio.Lock())
    async with lock:
        key = get_vpn_key_by_id(int(key_id))
        if not key:
            stats["errors"] = 1
            return stats
        if not all((key.get("server_id"), key.get("panel_email"), key.get("sub_id"))):
            stats["skipped"] = 1
            stats["ok"] = 1
            return stats
        email = str(key["panel_email"])
        if not is_managed_panel_email(email):
            stats["errors"] = 1
            return stats

        try:
            client = get_client_from_server_data(_build_server_data_from_key(key))
            snapshot = panel_snapshot or await client.get_sync_snapshot()
            state = snapshot.get_client(email)
            active = should_panel_client_exist(key)
            expiry_time_ms = get_key_expiry_time_ms(key)
            limits = resolve_key_panel_limits(key, mode=device_limit_mode)
            hwid_mode = limits.limit_hwid > 0
            hwid_supported = supports_client_hwids(client)
            preserve_limits = hwid_mode and not hwid_supported
            if active and state is None and preserve_limits:
                stats["errors"] = 1
                return stats
            target_limit_ip = None if preserve_limits else limits.limit_ip
            target_limit_hwid = limits.limit_hwid if hwid_supported else None
            panel_used = 0 if reset_traffic else (
                state.traffic_used if state is not None and state.traffic_known else 0
            )
            total_bytes = calculate_panel_total_for_key(key, panel_used)
            out_of_scope_before = (
                set(state.out_of_scope_inbound_ids) if state is not None else set()
            )

            async def detach_out_of_scope() -> None:
                if state is None or not state.out_of_scope_inbound_ids:
                    return
                inbound_ids = set(state.out_of_scope_inbound_ids)
                bulk_detach = getattr(client, "bulk_detach_clients", None)
                if not callable(bulk_detach):
                    stats["errors"] += len(inbound_ids)
                    return
                confirmed = await bulk_detach([email], inbound_ids)
                confirmed_emails = {
                    str(value).strip().lower() for value in confirmed
                }
                if email.lower() not in confirmed_emails:
                    stats["errors"] += len(inbound_ids)
                    return
                state.out_of_scope_inbound_ids.difference_update(inbound_ids)
                for inbound_id in inbound_ids:
                    state.placements.pop(inbound_id, None)
                stats["deleted"] += len(inbound_ids)

            if not active:
                if state is None:
                    stats["skipped"] = 1
                    stats["ok"] = 1
                    return stats
                inactive_needs_update = (
                    not state.unavailable_inbound_ids
                    and bool(state.inbound_ids)
                    and _client_needs_update(
                        state,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=total_bytes,
                        enable=False,
                        limit_ip=target_limit_ip,
                        limit_hwid=target_limit_hwid,
                        sub_id=str(key["sub_id"]),
                    )
                )
                if dry_run:
                    stats["deleted"] = len(out_of_scope_before)
                    if inactive_needs_update:
                        stats["disabled"] = int(state.enable)
                        stats["updated"] = 1
                    if not any(
                        stats[name]
                        for name in ("deleted", "disabled", "updated")
                    ):
                        stats["skipped"] = 1
                else:
                    await detach_out_of_scope()
                    changed = False
                    if inactive_needs_update:
                        changed = await client.update_client_full(
                            email=email,
                            total_gb_bytes=total_bytes,
                            expiry_time_ms=expiry_time_ms,
                            enable=False,
                            limit_ip=(state.limit_ip if preserve_limits else target_limit_ip),
                            limit_hwid=(None if preserve_limits else target_limit_hwid),
                            sub_id=str(key["sub_id"]),
                            reset=0,
                            known_state=state,
                        )
                    if changed:
                        stats["disabled"] = int(state.enable)
                        stats["updated"] = 1
                    elif not stats["deleted"]:
                        stats["skipped"] = 1
                stats["ok"] = int(stats["errors"] == 0)
                return stats

            descriptors = await get_client_inbound_descriptors(
                client,
                include_ignored=False,
            )
            target_ids = {item.id for item in descriptors if item.available}
            attached_before = set(state.inbound_ids) if state is not None else set()
            if not dry_run:
                await detach_out_of_scope()
            remaining_out_of_scope = (
                set(state.out_of_scope_inbound_ids) if state is not None else set()
            )
            if not target_ids:
                if dry_run:
                    stats["deleted"] = len(out_of_scope_before)
                stats["errors"] += 1
                stats["ok"] = 0
                return stats
            needs_update = (
                state is None
                or not target_ids.issubset(attached_before)
                or bool(remaining_out_of_scope)
                or (
                    state is not None
                    and not state.unavailable_inbound_ids
                    and _client_needs_update(
                        state,
                        expiry_time_ms=expiry_time_ms,
                        total_gb_bytes=total_bytes,
                        enable=True,
                        limit_ip=target_limit_ip,
                        limit_hwid=target_limit_hwid,
                        sub_id=str(key["sub_id"]),
                    )
                )
            )
            if dry_run:
                missing = target_ids - attached_before
                stats["created"] = len(missing)
                stats["deleted"] = len(out_of_scope_before)
                stats["updated"] = int(needs_update and not missing)
                stats["reset"] = int(reset_traffic and state is not None)
                stats["skipped"] = int(not needs_update and not reset_traffic)
                stats["ok"] = 1
                return stats

            if reset_traffic and state is not None:
                await client.reset_client_traffic(email)
                stats["reset"] = 1
                panel_used = 0
                total_bytes = calculate_panel_total_for_key(key, 0)
                needs_update = True

            if needs_update:
                provision_limit_ip = (
                    state.limit_ip if preserve_limits and state is not None
                    else int(target_limit_ip or 0)
                )
                provision_limit_hwid = (
                    state.limit_hwid if preserve_limits and state is not None
                    else int(target_limit_hwid or 0)
                )
                provisioned = await provision_client_on_server(
                    server_id=int(key["server_id"]),
                    email=email,
                    total_gb_bytes=total_bytes,
                    expiry_time_ms=expiry_time_ms,
                    limit_ip=provision_limit_ip,
                    limit_hwid=provision_limit_hwid,
                    enable=True,
                    tg_id=str(key.get("telegram_id") or ""),
                    sub_id=str(key["sub_id"]),
                    inbound_ids=target_ids,
                    client=client,
                )
                stats["created"] = len(provisioned.attached_inbound_ids - attached_before)
                stats["updated"] = int(bool(attached_before))
                stats["errors"] += len(provisioned.failed_inbound_ids)
                if state is not None and not state.enable and provisioned.attached_inbound_ids:
                    stats["enabled"] = 1
                remaining_after_provision = set(remaining_out_of_scope)
                if provisioned.snapshot is not None:
                    confirmed_state = provisioned.snapshot.get_client(email)
                    if confirmed_state is not None:
                        remaining_after_provision = set(
                            confirmed_state.out_of_scope_inbound_ids
                        )
                if remaining_after_provision:
                    bulk_detach = getattr(client, "bulk_detach_clients", None)
                    if not callable(bulk_detach):
                        stats["errors"] += len(remaining_after_provision)
                    else:
                        confirmed = await bulk_detach(
                            [email],
                            remaining_after_provision,
                        )
                        if email.lower() in {
                            str(value).strip().lower() for value in confirmed
                        }:
                            stats["deleted"] += len(remaining_after_provision)
                        else:
                            stats["errors"] += len(remaining_after_provision)
            else:
                stats["skipped"] = int(stats["deleted"] == 0)
            stats["ok"] = int(stats["errors"] == 0)
            return stats
        except Exception:
            stats["errors"] += 1
            logger.exception("Key reconciliation failed key_id=%s", key_id)
            return stats


async def ensure_subscription_keys_on_server(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
    dry_run: bool = False,
    device_limit_mode: Optional[str] = None,
) -> Dict[str, int]:
    """Reconcile one key and make every non-preview failure observable."""
    context = _unlocked_preview() if dry_run else panel_sync_coordinator.regular()
    async with context:
        try:
            stats = await _ensure_subscription_keys_on_server_impl(
                key_id,
                reset_traffic=reset_traffic,
                panel_snapshot=panel_snapshot,
                dry_run=dry_run,
                device_limit_mode=device_limit_mode,
            )
        except Exception as error:
            if not dry_run:
                logger.warning(
                    "panel_key_sync_failed key_id=%s reset_traffic=%s "
                    "snapshot_reused=%s exception_type=%s",
                    key_id,
                    int(bool(reset_traffic)),
                    int(panel_snapshot is not None),
                    type(error).__name__,
                    exc_info=True,
                )
            raise

    if not dry_run and (
        not bool(stats.get("ok"))
        or int(stats.get("errors", 0) or 0) != 0
    ):
        logger.warning(
            "panel_key_sync_incomplete key_id=%s reset_traffic=%s "
            "snapshot_reused=%s created=%s deleted=%s enabled=%s "
            "disabled=%s updated=%s skipped=%s reset=%s errors=%s ok=%s",
            key_id,
            int(bool(reset_traffic)),
            int(panel_snapshot is not None),
            stats.get("created", 0),
            stats.get("deleted", 0),
            stats.get("enabled", 0),
            stats.get("disabled", 0),
            stats.get("updated", 0),
            stats.get("skipped", 0),
            stats.get("reset", 0),
            stats.get("errors", 0),
            stats.get("ok", 0),
        )
    return stats


async def sync_key_to_panel_state(
    key_id: int,
    reset_traffic: bool = False,
    panel_snapshot: Optional[PanelServerSnapshot] = None,
) -> Dict[str, int]:
    return await ensure_subscription_keys_on_server(
        key_id,
        reset_traffic=reset_traffic,
        panel_snapshot=panel_snapshot,
    )


@regular_panel_operation
async def push_key_to_panel(key_id: int, reset_traffic: bool = False) -> bool:
    stats = await sync_key_to_panel_state(key_id, reset_traffic=reset_traffic)
    return bool(stats.get("ok")) and not stats.get("errors")


async def get_subscription_url_for_key(
    key: Dict[str, Any],
    *,
    suppress_errors: bool = True,
) -> Optional[str]:
    """Resolve a key subscription URL.

    Interactive callers keep the historical ``None`` fallback. Durable
    reconciliation disables suppression so a transient component-panel error
    cannot be mistaken for an absent URL and remove an already managed link.
    """
    sub_id = key.get("sub_id")
    server_id = key.get("server_id")
    if not sub_id or not server_id:
        return None
    try:
        client = await get_client(int(server_id))
        return await client.get_subscription_link(str(sub_id))
    except Exception:
        logger.exception("Could not build subscription URL key_id=%s", key.get("id"))
        if not suppress_errors:
            raise
        return None


__all__ = [
    "VPNAPIError",
    "PanelRejectedError",
    "PanelClientDevice",
    "PanelClientLimits",
    "calculate_panel_total_for_key",
    "close_all_clients",
    "ensure_subscription_keys_on_server",
    "delete_key_device_for_user",
    "extend_key_on_server",
    "format_traffic",
    "get_client",
    "get_client_external_links",
    "get_key_devices_for_user",
    "get_client_from_server_data",
    "get_client_inbound_descriptors",
    "get_key_expiry_time_ms",
    "get_key_limit_ip",
    "resolve_key_panel_limits",
    "resolve_panel_client_limits",
    "get_key_traffic_snapshot",
    "get_subscription_url_for_key",
    "invalidate_client_cache",
    "provision_client_on_server",
    "push_key_to_panel",
    "refresh_client_capabilities",
    "replace_client_external_links",
    "reset_key_traffic_if_active",
    "restore_key_traffic_limit",
    "restore_traffic_limit_in_db",
    "sync_key_to_panel_state",
    "supports_client_external_links",
    "supports_client_hwids",
    "test_server_connection",
]
