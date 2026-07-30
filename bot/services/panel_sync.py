"""Batch panel snapshots, reconciliation plans and normalized synchronization."""

from __future__ import annotations

import asyncio
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
from bot.services.panel_key_state import should_panel_client_exist
from bot.utils.panel_email import is_managed_panel_email

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


def _managed_keys(
    keys: Iterable[Dict[str, Any]],
    *,
    operation: str,
) -> List[Dict[str, Any]]:
    """Return only bot-owned key rows and report invalid ownership boundaries."""
    managed: List[Dict[str, Any]] = []
    for key in keys:
        if is_managed_panel_email(key.get("panel_email")):
            managed.append(key)
            continue
        logger.warning(
            "%s skipped key %s with unmanaged panel_email=%r",
            operation,
            key.get("id"),
            key.get("panel_email"),
        )
    return managed


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
    semaphore = asyncio.Semaphore(4)

    async def collect_one(server_id: int) -> None:
        if allowed is not None and server_id not in allowed:
            return
        server = servers_by_id.get(server_id)
        if not server:
            collection.errors[server_id] = "Server is missing or disabled"
            return
        async with semaphore:
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

    await asyncio.gather(*(collect_one(server_id) for server_id in grouped))
    return collection


async def _apply_clients_api_bulk_prelude(
    server: Dict[str, Any],
    server_keys: Iterable[Dict[str, Any]],
    snapshot: PanelServerSnapshot,
) -> Dict[str, Dict[str, int]]:
    """Batch membership and enabled-state changes before point reconciliation."""
    if snapshot.api_profile != "clients_api":
        return {}

    from bot.services.vpn_api import (
        get_client_from_server_data,
        is_subscription_mode,
    )
    from bot.utils.inbounds import is_ignored_inbound

    client = get_client_from_server_data(server)
    required_methods = (
        "bulk_attach_clients",
        "bulk_detach_clients",
        "bulk_delete_clients",
        "bulk_set_clients_enabled",
    )
    if not all(
        callable(getattr(type(client), method_name, None))
        for method_name in required_methods
    ):
        return {}

    per_email: Dict[str, Dict[str, int]] = {}

    def stats_for(email: str) -> Dict[str, int]:
        return per_email.setdefault(email.strip().lower(), empty_panel_stats())

    visible_ids = {
        int(inbound["id"])
        for inbound in snapshot.inbounds
        if inbound.get("id") is not None and not is_ignored_inbound(inbound)
    }
    subscription_mode = is_subscription_mode()
    attach_groups: Dict[tuple[int, ...], List[str]] = {}
    detach_groups: Dict[tuple[int, ...], List[str]] = {}
    delete_emails: List[str] = []
    enable_emails: List[str] = []
    disable_emails: List[str] = []
    states_by_email: Dict[str, PanelClientState] = {}
    seen_emails: set[str] = set()

    for key in server_keys:
        email = str(key.get("panel_email") or "").strip()
        if not is_managed_panel_email(email):
            logger.warning(
                "Clients API bulk prelude skipped key %s with unmanaged "
                "panel_email=%r",
                key.get("id"),
                key.get("panel_email"),
            )
            continue
        normalized_email = email.lower()
        if not normalized_email or normalized_email in seen_emails:
            continue
        seen_emails.add(normalized_email)
        active = should_panel_client_exist(key)
        state = snapshot.get_client(email)
        if state is None:
            continue
        states_by_email[normalized_email] = state
        has_unavailable_memberships = bool(
            state.unavailable_inbound_ids
        )

        if not active:
            if bool(state.enable) and not has_unavailable_memberships:
                disable_emails.append(email)
            continue

        current_ids = set(state.inbound_ids)

        if subscription_mode:
            desired_ids = set(visible_ids)
        else:
            try:
                configured_id = int(key.get("panel_inbound_id"))
            except (TypeError, ValueError):
                configured_id = None
            if configured_id in snapshot.unavailable_inbound_ids:
                continue
            current_visible = current_ids.intersection(visible_ids)
            if configured_id in visible_ids:
                desired_ids = {configured_id}
            elif current_visible:
                desired_ids = {min(current_visible)}
            else:
                desired_ids = set()

        if not desired_ids and current_ids:
            if has_unavailable_memberships:
                inbound_ids = tuple(sorted(current_ids))
                detach_groups.setdefault(inbound_ids, []).append(email)
            else:
                delete_emails.append(email)
            continue

        missing_ids = tuple(sorted(desired_ids - current_ids))
        extra_ids = tuple(sorted(current_ids - desired_ids))
        if missing_ids:
            attach_groups.setdefault(missing_ids, []).append(email)
        if extra_ids:
            detach_groups.setdefault(extra_ids, []).append(email)

        if (
            not has_unavailable_memberships
            and bool(state.enable) != bool(active)
        ):
            (enable_emails if active else disable_emails).append(email)

    operation_metrics = client.operation_metrics("bulk_reconcile")
    async with operation_metrics:
        for inbound_ids, emails in attach_groups.items():
            known_states = {
                email: states_by_email[email.lower()]
                for email in emails
                if email.lower() in states_by_email
            }
            try:
                confirmed = await client.bulk_attach_clients(
                    emails,
                    inbound_ids,
                    known_states=known_states,
                )
            except Exception as exc:
                logger.warning(
                    "Bulk attach failed for server %s: %s",
                    server.get("id"),
                    exc,
                )
                continue
            confirmed_keys = {email.lower() for email in confirmed}
            for email in emails:
                if email.lower() not in confirmed_keys:
                    continue
                state = states_by_email[email.lower()]
                state.inbound_ids.update(inbound_ids)
                for inbound_id in inbound_ids:
                    state.placements[inbound_id] = dict(state.client)
                stats_for(email)["created"] += len(inbound_ids)

        for inbound_ids, emails in detach_groups.items():
            try:
                confirmed = await client.bulk_detach_clients(
                    emails,
                    inbound_ids,
                )
            except Exception as exc:
                logger.warning(
                    "Bulk detach failed for server %s: %s",
                    server.get("id"),
                    exc,
                )
                continue
            confirmed_keys = {email.lower() for email in confirmed}
            for email in emails:
                if email.lower() not in confirmed_keys:
                    continue
                state = states_by_email[email.lower()]
                for inbound_id in inbound_ids:
                    state.inbound_ids.discard(inbound_id)
                    state.placements.pop(inbound_id, None)
                stats_for(email)["deleted"] += len(inbound_ids)

        if delete_emails:
            try:
                await client.bulk_delete_clients(delete_emails)
            except Exception as exc:
                logger.warning(
                    "Bulk delete failed for server %s: %s",
                    server.get("id"),
                    exc,
                )
            else:
                for email in delete_emails:
                    state = states_by_email[email.lower()]
                    stats_for(email)["deleted"] += max(
                        1,
                        len(state.inbound_ids),
                    )
                    snapshot.clients.pop(email.lower(), None)

        for target_enable, emails in (
            (True, enable_emails),
            (False, disable_emails),
        ):
            if not emails:
                continue
            try:
                changed = await client.bulk_set_clients_enabled(
                    emails,
                    target_enable,
                )
            except Exception as exc:
                logger.warning(
                    "Bulk %s failed for server %s: %s",
                    "enable" if target_enable else "disable",
                    server.get("id"),
                    exc,
                )
                continue
            if changed < len(emails):
                # Without a per-email success list, leave the snapshot unchanged.
                # Point reconciliation remains the authoritative fallback.
                continue
            for email in emails:
                state = states_by_email[email.lower()]
                placement_count = max(1, len(state.inbound_ids))
                state.enable = target_enable
                state.client["enable"] = target_enable
                for placement in state.placements.values():
                    placement["enable"] = target_enable
                field = "enabled" if target_enable else "disabled"
                stats_for(email)[field] += placement_count

    return per_email


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
    selected_keys = _managed_keys([
        key
        for key in keys
        if selected_ids is None or int(key.get("id", 0)) in selected_ids
    ], operation="Panel -> DB sync")
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
    selected_keys = _managed_keys([
        key
        for key in keys
        if selected_ids is None or int(key.get("id", 0)) in selected_ids
    ], operation="DB -> Panel sync")
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
        bulk_stats_by_email: Dict[str, Dict[str, int]] = {}
        if apply and snapshot.api_profile == "clients_api":
            try:
                bulk_stats_by_email = await _apply_clients_api_bulk_prelude(
                    server,
                    server_keys,
                    snapshot,
                )
            except Exception as exc:
                logger.warning(
                    "Clients API bulk prelude failed for server %s: %s",
                    server_id,
                    exc,
                )
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
            prelude_stats = bulk_stats_by_email.get(
                str(key.get("panel_email") or "").strip().lower(),
                {},
            )
            for name, value in prelude_stats.items():
                if name in stats:
                    stats[name] += int(value or 0)
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
