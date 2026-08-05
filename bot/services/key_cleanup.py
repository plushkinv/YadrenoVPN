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


async def cleanup_inactive_panel_clients(
    *,
    keys: Optional[Iterable[Dict[str, Any]]] = None,
    servers: Optional[Iterable[Dict[str, Any]]] = None,
    snapshots: Optional[SnapshotCollection] = None,
) -> PanelCleanupReport:
    """Delete DB-linked inactive bot clients from every available panel."""
    from bot.services.vpn_api import get_client_from_server_data
    from database.requests import get_all_panel_sync_keys, get_all_servers

    selected_keys = list(keys) if keys is not None else get_all_panel_sync_keys()
    selected_servers = list(servers) if servers is not None else get_all_servers()
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
                desired_states = [
                    should_panel_client_exist(key)
                    for key in email_rows
                ]
                if any(desired_states):
                    if any(not state for state in desired_states):
                        logger.warning(
                            "Daily panel cleanup kept conflicting panel_email=%s "
                            "on server %s because at least one DB key is active",
                            normalized_email,
                            server_id,
                        )
                    report.skipped += 1
                    continue

                state = snapshot.get_client(normalized_email)
                if state is None:
                    report.skipped += 1
                    continue
                candidates.append(state.email)

            report.candidates = len(candidates)
            if not candidates:
                continue

            client = get_client_from_server_data(server)
            bulk_delete = getattr(client, "bulk_delete_clients", None)
            if callable(bulk_delete):
                try:
                    report.deleted += int(
                        await bulk_delete(candidates)
                    )
                except Exception as exc:
                    report.errors += len(candidates)
                    logger.warning(
                        "Daily panel cleanup bulk delete failed for server %s: %s",
                        server_id,
                        exc,
                    )
                continue

            delete_by_email = getattr(
                client,
                "delete_clients_by_email_on_server",
                None,
            )
            if not callable(delete_by_email):
                report.errors += len(candidates)
                logger.warning(
                    "Daily panel cleanup cannot delete clients on server %s",
                    server_id,
                )
                continue

            for email in candidates:
                try:
                    report.deleted += int(await delete_by_email(email))
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
    for field_name in ("custom_name", "panel_email", "client_uuid", "id"):
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
) -> ExpiredKeyCleanupReport:
    """Atomically delete retained expired keys, then notify each user once."""
    from bot.utils.delivery import is_bot_blocked_error
    from bot.utils.page_renderer import (
        PreparedPageRender,
        prepare_page_render,
    )
    from bot.utils.text import send_media_or_text
    from database.requests import (
        delete_expired_keys_older_than,
        get_expired_key_retention_days,
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
    deleted = delete_expired_keys_older_than(retention_days)
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
        "Expired-key cleanup completed: deleted=%s users=%s notified=%s errors=%s",
        report.deleted,
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
