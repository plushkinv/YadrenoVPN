"""Batch panel snapshots, reconciliation plans and normalized synchronization."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from bot.services.panels.base import (
    PanelClientState,
    PanelServerSnapshot,
    build_legacy_panel_snapshot,
)

logger = logging.getLogger(__name__)

EXPIRY_TOLERANCE_SECONDS = 60
PANEL_ACTION_FIELDS = (
    "created",
    "deleted",
    "updated",
    "enabled",
    "disabled",
    "reset",
)


def empty_panel_stats() -> Dict[str, int]:
    return {
        "created": 0,
        "deleted": 0,
        "updated": 0,
        "enabled": 0,
        "disabled": 0,
        "reset": 0,
        "skipped": 0,
        "errors": 0,
    }


@dataclass
class ServerSyncReport:
    server_id: int
    server_name: str
    checked: int = 0
    changed: int = 0
    skipped: int = 0
    error: Optional[str] = None
    stats: Dict[str, int] = field(default_factory=empty_panel_stats)


@dataclass
class PanelImportChange:
    key_id: int
    server_id: int
    expires_at: Optional[str]
    traffic_used: int
    traffic_limit: int
    traffic_notified_pct: int
    expiry_changed: bool
    traffic_changed: bool
    revived: bool

    def as_database_update(self) -> Dict[str, Any]:
        return {
            "key_id": self.key_id,
            "expires_at": self.expires_at,
            "traffic_used": self.traffic_used,
            "traffic_limit": self.traffic_limit,
            "traffic_notified_pct": self.traffic_notified_pct,
        }


@dataclass
class PanelSyncPlan:
    direction: str
    reports: List[ServerSyncReport] = field(default_factory=list)
    candidate_key_ids: List[int] = field(default_factory=list)
    successful_server_ids: List[int] = field(default_factory=list)
    import_changes: Dict[int, List[PanelImportChange]] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.candidate_key_ids)

    @property
    def errors(self) -> int:
        return sum(1 for report in self.reports if report.error) + sum(
            report.stats.get("errors", 0) for report in self.reports
        )


@dataclass
class SnapshotCollection:
    snapshots: Dict[int, PanelServerSnapshot] = field(default_factory=dict)
    errors: Dict[int, str] = field(default_factory=dict)


def _server_map(servers: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {
        int(server["id"]): server
        for server in servers
        if server.get("id") is not None and server.get("is_active", 1)
    }


def group_keys_by_server(
    keys: Iterable[Dict[str, Any]],
) -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for key in keys:
        try:
            server_id = int(key["server_id"])
        except (KeyError, TypeError, ValueError):
            continue
        grouped.setdefault(server_id, []).append(key)
    return grouped


async def collect_server_snapshots(
    keys: Iterable[Dict[str, Any]],
    servers: Iterable[Dict[str, Any]],
    *,
    allowed_server_ids: Optional[Iterable[int]] = None,
) -> SnapshotCollection:
    """Download one complete snapshot for every server represented by keys."""
    from bot.services.vpn_api import get_client_from_server_data, is_subscription_mode

    grouped = group_keys_by_server(keys)
    servers_by_id = _server_map(servers)
    allowed = (
        {int(value) for value in allowed_server_ids}
        if allowed_server_ids is not None
        else None
    )
    collection = SnapshotCollection()

    for server_id in grouped:
        if allowed is not None and server_id not in allowed:
            continue
        server = servers_by_id.get(server_id)
        if not server:
            collection.errors[server_id] = "Server is missing or disabled"
            continue
        try:
            client = get_client_from_server_data(server)
            subscription_mode = is_subscription_mode()
            snapshot_method = getattr(client, "get_sync_snapshot", None)
            if callable(snapshot_method):
                snapshot_result = snapshot_method(
                    subscription_mode=subscription_mode,
                )
                snapshot = (
                    await snapshot_result
                    if inspect.isawaitable(snapshot_result)
                    else snapshot_result
                )
            else:
                # Keep third-party/older adapters and lightweight test doubles
                # compatible: their complete inbound list is already a valid
                # one-request legacy snapshot.
                inbounds_method = (
                    getattr(client, "get_subscription_inbounds", None)
                    if subscription_mode
                    else None
                ) or getattr(client, "get_inbounds", None)
                if not callable(inbounds_method):
                    raise RuntimeError("Panel adapter does not support batch snapshots")
                try:
                    inbounds_result = inbounds_method(include_ignored=True)
                except TypeError:
                    inbounds_result = inbounds_method()
                inbounds = (
                    await inbounds_result
                    if inspect.isawaitable(inbounds_result)
                    else inbounds_result
                )
                snapshot = build_legacy_panel_snapshot(
                    list(inbounds or []),
                )
            if not isinstance(snapshot, PanelServerSnapshot):
                raise RuntimeError("Panel adapter returned an invalid batch snapshot")
            collection.snapshots[server_id] = snapshot
        except Exception as exc:
            collection.errors[server_id] = str(exc)
            logger.warning(
                "Batch panel snapshot failed for server %s (%s): %s",
                server.get("name", server_id),
                server_id,
                exc,
            )
    return collection


def normalized_traffic_for_key(
    key: Dict[str, Any],
    snapshot: PanelServerSnapshot,
) -> Optional[int]:
    """Convert a physical panel counter into the cumulative DB counter."""
    from bot.services.vpn_api import _cumulative_traffic_used_from_panel

    state = snapshot.get_client(key.get("panel_email"))
    if not state or not state.traffic_known:
        return None
    return _cumulative_traffic_used_from_panel(
        key,
        int(state.traffic_used),
        int(state.total_gb),
    )


def collect_changed_traffic_updates(
    keys: Iterable[Dict[str, Any]],
    snapshots: Dict[int, PanelServerSnapshot],
) -> List[tuple[int, int]]:
    """Return only changed ``(traffic_used, key_id)`` database rows."""
    updates: List[tuple[int, int]] = []
    for key in keys:
        key['_traffic_snapshot_known'] = False
        try:
            snapshot = snapshots[int(key["server_id"])]
        except (KeyError, TypeError, ValueError):
            continue
        traffic_used = normalized_traffic_for_key(key, snapshot)
        if traffic_used is None:
            continue
        key['_traffic_snapshot_known'] = True
        key["_new_traffic_used"] = traffic_used
        if traffic_used != int(key.get("traffic_used", 0) or 0):
            updates.append((traffic_used, int(key["id"])))
    return updates


def _parse_db_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_db_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _remaining(limit_value: int, used_value: int) -> Optional[int]:
    if limit_value <= 0:
        return None
    return max(0, limit_value - used_value)


def build_panel_import_change(
    key: Dict[str, Any],
    state: PanelClientState,
    *,
    now: Optional[datetime] = None,
) -> Optional[PanelImportChange]:
    """Calculate a safe Panel -> DB change without mutating either side."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    old_expiry = _parse_db_datetime(key.get("expires_at"))
    was_expired = old_expiry is not None and old_expiry <= now_utc

    panel_expiry = (
        None
        if int(state.expiry_time or 0) == 0
        else datetime.fromtimestamp(int(state.expiry_time) / 1000, tz=timezone.utc)
    )
    panel_revives = panel_expiry is None or panel_expiry > now_utc
    if was_expired and not panel_revives:
        return None

    if old_expiry is None and panel_expiry is None:
        expiry_changed = False
    elif old_expiry is None or panel_expiry is None:
        expiry_changed = True
    else:
        expiry_changed = (
            abs((panel_expiry - old_expiry).total_seconds())
            > EXPIRY_TOLERANCE_SECONDS
        )

    old_used = max(0, int(key.get("traffic_used", 0) or 0))
    old_limit = max(0, int(key.get("traffic_limit", 0) or 0))
    new_used = old_used
    new_limit = old_limit
    if state.traffic_known:
        panel_used = max(0, int(state.traffic_used or 0))
        panel_total = max(0, int(state.total_gb or 0))
        if panel_total == 0:
            new_used = max(old_used, panel_used)
            new_limit = 0
        else:
            panel_remaining = max(0, panel_total - panel_used)
            consumed_from_old_allowance = max(0, old_limit - panel_remaining)
            new_used = max(old_used, panel_used, consumed_from_old_allowance)
            new_limit = new_used + panel_remaining

    traffic_changed = new_used != old_used or new_limit != old_limit
    if not expiry_changed and not traffic_changed:
        return None

    old_remaining = _remaining(old_limit, old_used)
    new_remaining = _remaining(new_limit, new_used)
    allowance_increased = (
        (old_limit > 0 and new_limit == 0)
        or (
            old_remaining is not None
            and new_remaining is not None
            and new_remaining > old_remaining
        )
    )
    notified_pct = int(key.get("traffic_notified_pct", 100) or 0)
    if allowance_increased:
        notified_pct = 100

    return PanelImportChange(
        key_id=int(key["id"]),
        server_id=int(key["server_id"]),
        expires_at=_format_db_datetime(
            panel_expiry if expiry_changed else old_expiry
        ),
        traffic_used=new_used,
        traffic_limit=new_limit,
        traffic_notified_pct=notified_pct,
        expiry_changed=expiry_changed,
        traffic_changed=traffic_changed,
        revived=was_expired and panel_revives,
    )


async def build_panel_to_db_plan(
    keys: Iterable[Dict[str, Any]],
    servers: Iterable[Dict[str, Any]],
    *,
    candidate_key_ids: Optional[Iterable[int]] = None,
    allowed_server_ids: Optional[Iterable[int]] = None,
    snapshots: Optional[SnapshotCollection] = None,
) -> PanelSyncPlan:
    """Build a read-only Panel -> DB plan from batch server snapshots."""
    selected_ids = (
        {int(value) for value in candidate_key_ids}
        if candidate_key_ids is not None
        else None
    )
    selected_keys = [
        key
        for key in keys
        if selected_ids is None or int(key.get("id", 0)) in selected_ids
    ]
    grouped = group_keys_by_server(selected_keys)
    servers_by_id = _server_map(servers)
    collection = snapshots or await collect_server_snapshots(
        selected_keys,
        servers,
        allowed_server_ids=allowed_server_ids,
    )
    plan = PanelSyncPlan(direction="panel_to_db")

    allowed = (
        {int(value) for value in allowed_server_ids}
        if allowed_server_ids is not None
        else None
    )
    for server_id, server_keys in grouped.items():
        if allowed is not None and server_id not in allowed:
            continue
        server = servers_by_id.get(server_id, {})
        report = ServerSyncReport(
            server_id=server_id,
            server_name=str(server.get("name") or server_keys[0].get("server_name") or server_id),
        )
        snapshot = collection.snapshots.get(server_id)
        if snapshot is None:
            report.error = collection.errors.get(server_id, "Panel snapshot is unavailable")
            plan.reports.append(report)
            continue

        plan.successful_server_ids.append(server_id)
        changes: List[PanelImportChange] = []
        for key in server_keys:
            report.checked += 1
            try:
                state = snapshot.get_client(key.get("panel_email"))
                change = (
                    build_panel_import_change(key, state)
                    if state is not None
                    else None
                )
            except Exception as exc:
                report.stats["errors"] += 1
                report.skipped += 1
                logger.warning(
                    "Panel -> DB comparison failed for key %s: %s",
                    key.get("id"),
                    exc,
                )
                continue
            if change is None:
                report.skipped += 1
                continue
            changes.append(change)
            plan.candidate_key_ids.append(change.key_id)
            report.changed += 1
            if change.expiry_changed:
                report.stats["expiry"] = report.stats.get("expiry", 0) + 1
            if change.traffic_changed:
                report.stats["traffic"] = report.stats.get("traffic", 0) + 1
            if change.revived:
                report.stats["revived"] = report.stats.get("revived", 0) + 1
        if changes:
            plan.import_changes[server_id] = changes
        plan.reports.append(report)
    return plan


async def apply_panel_to_db_plan(plan: PanelSyncPlan) -> PanelSyncPlan:
    """Apply a freshly rebuilt Panel -> DB plan atomically per server."""
    from database.requests import apply_panel_import_batch

    for report in plan.reports:
        changes = plan.import_changes.get(report.server_id, [])
        if not changes or report.error:
            continue
        try:
            applied = apply_panel_import_batch(
                [change.as_database_update() for change in changes]
            )
            report.stats["applied"] = applied
        except Exception as exc:
            report.error = str(exc)
            logger.exception(
                "Panel -> DB transaction failed for server %s",
                report.server_id,
            )
    return plan


async def run_db_to_panel_sync(
    keys: Iterable[Dict[str, Any]],
    servers: Iterable[Dict[str, Any]],
    *,
    apply: bool,
    candidate_key_ids: Optional[Iterable[int]] = None,
    allowed_server_ids: Optional[Iterable[int]] = None,
    snapshots: Optional[SnapshotCollection] = None,
) -> PanelSyncPlan:
    """Preview or apply DB -> Panel materialization using one snapshot/server."""
    from bot.services.vpn_api import ensure_subscription_keys_on_server

    selected_ids = (
        {int(value) for value in candidate_key_ids}
        if candidate_key_ids is not None
        else None
    )
    selected_keys = [
        key
        for key in keys
        if selected_ids is None or int(key.get("id", 0)) in selected_ids
    ]
    grouped = group_keys_by_server(selected_keys)
    servers_by_id = _server_map(servers)
    collection = snapshots or await collect_server_snapshots(
        selected_keys,
        servers,
        allowed_server_ids=allowed_server_ids,
    )
    plan = PanelSyncPlan(direction="db_to_panel")
    allowed = (
        {int(value) for value in allowed_server_ids}
        if allowed_server_ids is not None
        else None
    )

    for server_id, server_keys in grouped.items():
        if allowed is not None and server_id not in allowed:
            continue
        server = servers_by_id.get(server_id, {})
        report = ServerSyncReport(
            server_id=server_id,
            server_name=str(server.get("name") or server_keys[0].get("server_name") or server_id),
        )
        snapshot = collection.snapshots.get(server_id)
        if snapshot is None:
            report.error = collection.errors.get(server_id, "Panel snapshot is unavailable")
            plan.reports.append(report)
            continue

        plan.successful_server_ids.append(server_id)
        for key in server_keys:
            report.checked += 1
            try:
                stats = await ensure_subscription_keys_on_server(
                    int(key["id"]),
                    panel_snapshot=snapshot,
                    dry_run=not apply,
                )
            except Exception as exc:
                report.stats["errors"] += 1
                logger.warning("Key %s materialization failed: %s", key.get("id"), exc)
                continue
            for name, value in stats.items():
                if name in report.stats:
                    report.stats[name] += int(value or 0)
            action_count = sum(int(stats.get(name, 0) or 0) for name in PANEL_ACTION_FIELDS)
            if action_count:
                report.changed += 1
                plan.candidate_key_ids.append(int(key["id"]))
            else:
                report.skipped += 1
        plan.reports.append(report)
    return plan


__all__ = [
    "EXPIRY_TOLERANCE_SECONDS",
    "PanelImportChange",
    "PanelSyncPlan",
    "ServerSyncReport",
    "SnapshotCollection",
    "apply_panel_to_db_plan",
    "build_panel_import_change",
    "build_panel_to_db_plan",
    "collect_changed_traffic_updates",
    "collect_server_snapshots",
    "run_db_to_panel_sync",
]
