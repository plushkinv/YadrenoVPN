"""Daily cleanup of inactive panel clients and expired database keys."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from aiogram import Bot

from bot.services.panel_key_state import should_panel_client_exist
from bot.services.panel_sync import (
    SnapshotCollection,
    collect_server_snapshots,
    group_keys_by_server,
)
from bot.services.panel_sync_coordinator import panel_sync_coordinator
from bot.utils.panel_email import is_managed_panel_email

logger = logging.getLogger(__name__)

EXPIRED_KEYS_DELETED_PAGE_KEY = "expired_keys_deleted"
MAX_DELETED_KEY_NAMES = 10


@dataclass
class PanelCleanupServerReport:
    """Result of one server in the inactive-client cleanup pass."""

    server_id: int
    server_name: str
    checked: int = 0
    candidates: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    error: Optional[str] = None


@dataclass
class PanelCleanupReport:
    """Aggregated inactive-client cleanup result."""

    servers: List[PanelCleanupServerReport] = field(default_factory=list)
    confirmed_absent: set[tuple[int, str]] = field(
        default_factory=set,
        repr=False,
    )
    retained_for_active: set[tuple[int, str]] = field(
        default_factory=set,
        repr=False,
    )

    @property
    def deleted(self) -> int:
        return sum(report.deleted for report in self.servers)

    @property
    def errors(self) -> int:
        return sum(report.errors for report in self.servers) + sum(
            1 for report in self.servers if report.error
        )


@dataclass
class ExpiredKeyCleanupReport:
    """Result of one expired-key database retention pass."""

    retention_days: Optional[int] = None
    deleted: int = 0
    users: int = 0
    notified: int = 0
    notification_errors: int = 0
    notifications_enabled: bool = True
    pending_panel: int = 0
    skipped_reason: Optional[str] = None


def _server_map(servers: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {
        int(server["id"]): server
        for server in servers
        if server.get("id") is not None and server.get("is_active", 1)
    }


def _managed_rows_by_email(
    keys: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for key in keys:
        email = key.get("panel_email")
        if not is_managed_panel_email(email):
            logger.warning(
                "Daily panel cleanup skipped key %s with unmanaged panel_email=%r",
                key.get("id"),
                email,
            )
            continue
        rows[str(email).strip().lower()].append(key)
    return dict(rows)


def _normalized_binding(
    server_id: Any,
    panel_email: Any,
) -> Optional[tuple[int, str]]:
    if server_id is None or not is_managed_panel_email(panel_email):
        return None
    return int(server_id), str(panel_email).strip().lower()


def _should_keep_panel_client(
    key: Mapping[str, Any],
    expiry_due_key_ids: set[int],
) -> bool:
    """Return whether one DB row still protects its logical panel client."""
    if should_panel_client_exist(key):
        return True
    if bool(key.get("is_banned", 0)):
        return False
    try:
        key_id = int(key.get("id"))
    except (TypeError, ValueError):
        return True
    return key_id not in expiry_due_key_ids


def _expiry_due_key_ids_for_daily_cleanup() -> set[int]:
    """Load DB-backed inactivity candidates for the effective panel delay."""
    from database.requests import (
        get_expired_key_panel_cleanup_delay_days,
        get_expired_key_retention_days,
        get_expired_keys_older_than,
    )

    try:
        retention_days = get_expired_key_retention_days()
    except ValueError as exc:
        logger.error(
            "Expired panel-client cleanup skipped because retention is invalid: %s",
            exc,
        )
        return set()

    delay_days = get_expired_key_panel_cleanup_delay_days()
    effective_days = (
        retention_days
        if delay_days is None
        else min(delay_days, retention_days)
    )
    return {
        int(row["id"])
        for row in get_expired_keys_older_than(effective_days)
    }


async def cleanup_inactive_panel_clients(
    *,
    keys: Optional[Iterable[Dict[str, Any]]] = None,
    servers: Optional[Iterable[Dict[str, Any]]] = None,
    snapshots: Optional[SnapshotCollection] = None,
    expiry_due_key_ids: Optional[Iterable[int]] = None,
) -> PanelCleanupReport:
    """Delete due inactive clients; the legacy expiry allowlist includes traffic."""
    from bot.services.vpn_api import get_client_from_server_data
    from database.requests import get_all_panel_sync_keys, get_all_servers

    selected_keys = list(keys) if keys is not None else get_all_panel_sync_keys()
    selected_servers = list(servers) if servers is not None else get_all_servers()
    if expiry_due_key_ids is not None:
        due_key_ids = {
            int(key_id) for key_id in expiry_due_key_ids
        }
    else:
        due_key_ids = _expiry_due_key_ids_for_daily_cleanup()
    grouped = group_keys_by_server(selected_keys)
    servers_by_id = _server_map(selected_servers)
    result = PanelCleanupReport()

    async with panel_sync_coordinator.regular():
        collection = snapshots or await collect_server_snapshots(
            selected_keys,
            selected_servers,
        )

        for server_id, server_keys in grouped.items():
            server = servers_by_id.get(server_id, {})
            report = PanelCleanupServerReport(
                server_id=server_id,
                server_name=str(
                    server.get("name")
                    or server_keys[0].get("server_name")
                    or server_id
                ),
            )
            result.servers.append(report)

            snapshot = collection.snapshots.get(server_id)
            if snapshot is None:
                report.error = collection.errors.get(
                    server_id,
                    "Panel snapshot is unavailable",
                )
                continue
            if not server:
                report.error = "Server is missing or disabled"
                continue

            candidates: List[str] = []
            rows_by_email = _managed_rows_by_email(server_keys)
            for normalized_email, email_rows in rows_by_email.items():
                report.checked += 1
                active_states = [
                    should_panel_client_exist(key)
                    for key in email_rows
                ]
                desired_states = [
                    _should_keep_panel_client(key, due_key_ids)
                    for key in email_rows
                ]
                if any(desired_states):
                    if any(active_states):
                        result.retained_for_active.add(
                            (server_id, normalized_email)
                        )
                    if any(not state for state in desired_states):
                        logger.warning(
                            "Daily panel cleanup kept conflicting panel_email=%s "
                            "on server %s because at least one DB key still requires it",
                            normalized_email,
                            server_id,
                        )
                    report.skipped += 1
                    continue

                state = snapshot.get_client(normalized_email)
                if state is None:
                    result.confirmed_absent.add((server_id, normalized_email))
                    report.skipped += 1
                    continue
                candidates.append(state.email)

            report.candidates = len(candidates)
            if not candidates:
                continue

            client = get_client_from_server_data(server)
            for email in candidates:
                normalized_email = str(email).strip().lower()
                try:
                    deleted = bool(await client.delete_client(email))
                    if not deleted:
                        report.errors += 1
                        logger.warning(
                            "Daily panel cleanup was not confirmed for %s on server %s",
                            normalized_email,
                            server_id,
                        )
                        continue
                    report.deleted += 1
                    result.confirmed_absent.add((server_id, normalized_email))
                except Exception as exc:
                    report.errors += 1
                    logger.warning(
                        "Daily panel cleanup failed for %s on server %s: %s",
                        email,
                        server_id,
                        exc,
                    )

    logger.info(
        "Daily panel cleanup completed: servers=%s deleted=%s errors=%s",
        len(result.servers),
        result.deleted,
        result.errors,
    )
    return result


def _deleted_key_display_name(key: Mapping[str, Any]) -> str:
    for field_name in ("custom_name", "panel_email", "id"):
        value = key.get(field_name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def build_deleted_keys_html(
    keys: Iterable[Mapping[str, Any]],
    *,
    limit: int = MAX_DELETED_KEY_NAMES,
) -> str:
    """Build a bounded safe-HTML key-name list from DB-backed templates."""
    from bot.utils.user_ui_texts import render_ui_text

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    rows = list(keys)
    rendered = [
        render_ui_text(
            "key.deleted_list.item",
            name=_deleted_key_display_name(key),
        )
        for key in rows[:limit]
    ]
    remaining = len(rows) - len(rendered)
    if remaining > 0:
        rendered.append(
            render_ui_text("key.deleted_list.more", count=remaining)
        )
    return "\n".join(rendered)


async def cleanup_expired_database_keys(
    bot: Bot,
    *,
    panel_report: Optional[PanelCleanupReport] = None,
) -> ExpiredKeyCleanupReport:
    """Delete retained keys only after their managed panel state is safe."""
    from bot.utils.delivery import is_bot_blocked_error
    from bot.utils.page_renderer import (
        PreparedPageRender,
        prepare_page_render,
    )
    from bot.utils.text import send_media_or_text
    from database.requests import (
        delete_expired_keys_older_than,
        get_expired_key_retention_days,
        get_expired_keys_older_than,
        is_expired_key_deletion_notifications_enabled,
        mark_user_bot_blocked,
    )

    report = ExpiredKeyCleanupReport()
    try:
        retention_days = get_expired_key_retention_days()
    except ValueError as exc:
        report.skipped_reason = str(exc)
        logger.error(
            "Expired-key cleanup skipped because retention is invalid: %s",
            exc,
        )
        return report

    report.retention_days = retention_days
    candidates = get_expired_keys_older_than(retention_days)
    if not candidates:
        return report

    due_ids_by_binding: Dict[tuple[int, str], set[int]] = defaultdict(set)
    eligible_key_ids: set[int] = set()
    for key in candidates:
        key_id = int(key["id"])
        binding = _normalized_binding(
            key.get("server_id"),
            key.get("panel_email"),
        )
        if binding is None:
            eligible_key_ids.add(key_id)
            continue
        due_ids_by_binding[binding].add(key_id)

    confirmed_absent = (
        panel_report.confirmed_absent if panel_report is not None else set()
    )
    retained_for_active = (
        panel_report.retained_for_active
        if panel_report is not None
        else set()
    )
    for binding, due_ids in due_ids_by_binding.items():
        if binding in confirmed_absent or binding in retained_for_active:
            eligible_key_ids.update(due_ids)
        else:
            report.pending_panel += len(due_ids)

    if not eligible_key_ids:
        logger.info(
            "Expired-key cleanup retained %s keys pending panel confirmation",
            report.pending_panel,
        )
        return report

    # Retention must not erase an as-yet unobserved inactivity event. Legacy
    # hooks run before the final age/state recheck and may restore the key.
    from bot.services.key_lifecycle import process_expired_key_lifecycle_events

    await process_expired_key_lifecycle_events()
    deleted = delete_expired_keys_older_than(
        retention_days,
        eligible_key_ids=eligible_key_ids,
    )
    report.deleted = len(deleted)
    if not deleted:
        return report

    grouped: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for key in deleted:
        telegram_id = int(key.get("telegram_id") or 0)
        if telegram_id > 0:
            grouped[telegram_id].append(key)
    report.users = len(grouped)

    report.notifications_enabled = (
        is_expired_key_deletion_notifications_enabled()
    )
    if not report.notifications_enabled:
        logger.info(
            "Expired-key cleanup deleted %s keys; user notifications are disabled",
            report.deleted,
        )
        return report

    for telegram_id, user_keys in grouped.items():
        context = {
            "telegram_id": telegram_id,
            "retention_days": retention_days,
            "deleted_key_count": len(user_keys),
            "deleted_keys_html": build_deleted_keys_html(user_keys),
        }
        try:
            prepared = await prepare_page_render(
                bot,
                EXPIRED_KEYS_DELETED_PAGE_KEY,
                context=context,
            )
            if not isinstance(prepared, PreparedPageRender):
                logger.info(
                    "Expired-key deletion notification skipped by page flow"
                )
                report.notification_errors += 1
                continue
            await send_media_or_text(
                bot,
                chat_id=telegram_id,
                text=prepared.text,
                media=prepared.media,
                media_type=prepared.media_type,
                reply_markup=prepared.reply_markup,
            )
            report.notified += 1
        except Exception as exc:
            report.notification_errors += 1
            if is_bot_blocked_error(exc):
                mark_user_bot_blocked(telegram_id)
            logger.warning(
                "Expired-key deletion notification failed for telegram_id=%s: %s",
                telegram_id,
                exc,
            )

    logger.info(
        "Expired-key cleanup completed: deleted=%s pending_panel=%s users=%s "
        "notified=%s errors=%s",
        report.deleted,
        report.pending_panel,
        report.users,
        report.notified,
        report.notification_errors,
    )
    return report


__all__ = [
    "EXPIRED_KEYS_DELETED_PAGE_KEY",
    "ExpiredKeyCleanupReport",
    "MAX_DELETED_KEY_NAMES",
    "PanelCleanupReport",
    "PanelCleanupServerReport",
    "build_deleted_keys_html",
    "cleanup_expired_database_keys",
    "cleanup_inactive_panel_clients",
]
