"""Shared domain service for configuring a newly created VPN key."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.services.user_locks import user_locks

logger = logging.getLogger(__name__)


def _has_complete_binding(key: Mapping[str, Any]) -> bool:
    """Return whether a database row has the complete subscription identity."""
    return bool(
        key.get("server_id")
        and str(key.get("panel_email") or "").strip()
        and str(key.get("sub_id") or "").strip()
    )


class NewKeySetupStatus(str, Enum):
    """Stable outcomes consumed by interactive and background adapters."""

    READY = "ready"
    AWAITING_SERVER = "awaiting_server"
    PROVISIONING = "provisioning"
    UNAVAILABLE = "unavailable"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True)
class NewKeySetupResult:
    """One validated step of the new-key configuration state machine."""

    status: NewKeySetupStatus
    order_id: str
    key_id: int | None = None
    internal_user_id: int | None = None
    telegram_id: int | None = None
    username: str | None = None
    server_id: int | None = None
    server_name: str | None = None
    servers: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    key_data: Mapping[str, Any] | None = None
    page_key: str | None = None
    error_code: str | None = None
    error: str | None = None
    already_configured: bool = False

    @property
    def retryable(self) -> bool:
        return self.status is NewKeySetupStatus.RETRYABLE_FAILURE


@dataclass(frozen=True)
class _SetupContext:
    order: Mapping[str, Any]
    key: Mapping[str, Any]
    user: Mapping[str, Any]
    servers: tuple[Mapping[str, Any], ...]


def _failure(
    order_id: str,
    *,
    status: NewKeySetupStatus,
    page_key: str,
    error_code: str,
    error: str | None = None,
    context: _SetupContext | None = None,
) -> NewKeySetupResult:
    return NewKeySetupResult(
        status=status,
        order_id=str(order_id),
        key_id=int(context.key["id"]) if context else None,
        internal_user_id=int(context.order["user_id"]) if context else None,
        telegram_id=int(context.user["telegram_id"]) if context else None,
        username=(
            str(context.user.get("username") or "") or None
            if context
            else None
        ),
        servers=context.servers if context else (),
        page_key=page_key,
        error_code=error_code,
        error=error,
    )


def _load_setup_context(
    order_id: str,
    *,
    expected_telegram_id: int | None,
) -> _SetupContext | NewKeySetupResult:
    from bot.utils.groups import get_servers_for_key
    from database.requests import (
        find_order_by_order_id,
        get_active_servers,
        get_key_details_for_user,
        get_user_by_id,
    )

    normalized_order_id = str(order_id or "").strip()
    order = find_order_by_order_id(normalized_order_id)
    if not order or str(order.get("status") or "") != "paid":
        return _failure(
            normalized_order_id,
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="payment_order_unavailable",
            error_code="order_unavailable",
        )

    user = get_user_by_id(int(order.get("user_id") or 0))
    telegram_id = int((user or {}).get("telegram_id") or 0)
    if not user or not telegram_id:
        return _failure(
            normalized_order_id,
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="key_operation_failed",
            error_code="owner_unavailable",
        )
    if expected_telegram_id is not None and telegram_id != int(expected_telegram_id):
        return _failure(
            normalized_order_id,
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="payment_order_unavailable",
            error_code="owner_mismatch",
        )

    key_id = int(order.get("vpn_key_id") or 0)
    key = get_key_details_for_user(key_id, telegram_id) if key_id else None
    if not key:
        return _failure(
            normalized_order_id,
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="key_operation_failed",
            error_code="key_unavailable",
        )

    tariff_id = int(order.get("tariff_id") or key.get("tariff_id") or 0)
    raw_servers = get_servers_for_key(tariff_id) if tariff_id else get_active_servers()
    servers = tuple(dict(server) for server in raw_servers)
    return _SetupContext(
        order=dict(order),
        key=dict(key),
        user=dict(user),
        servers=servers,
    )


def _result_from_context(
    context: _SetupContext,
    status: NewKeySetupStatus,
    **kwargs: Any,
) -> NewKeySetupResult:
    return NewKeySetupResult(
        status=status,
        order_id=str(context.order["order_id"]),
        key_id=int(context.key["id"]),
        internal_user_id=int(context.order["user_id"]),
        telegram_id=int(context.user["telegram_id"]),
        username=str(context.user.get("username") or "") or None,
        servers=context.servers,
        **kwargs,
    )


async def resolve_new_key_setup(
    order_id: str,
    *,
    expected_telegram_id: int | None = None,
    server_id: int | None = None,
) -> NewKeySetupResult:
    """Resolve the next deterministic step without mutating the key or panel."""
    context = _load_setup_context(
        order_id,
        expected_telegram_id=expected_telegram_id,
    )
    if isinstance(context, NewKeySetupResult):
        return context

    if _has_complete_binding(context.key):
        return _result_from_context(
            context,
            NewKeySetupStatus.READY,
            server_id=int(context.key["server_id"]),
            key_data=dict(context.key),
            already_configured=True,
        )

    if not context.servers:
        return _failure(
            str(context.order["order_id"]),
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="new_key_no_servers",
            error_code="no_servers",
            context=context,
        )

    selected_server: Mapping[str, Any] | None = None
    if server_id is None:
        if len(context.servers) > 1:
            return _result_from_context(
                context,
                NewKeySetupStatus.AWAITING_SERVER,
                page_key="new_key_server_select",
            )
        selected_server = context.servers[0]
    else:
        selected_server = next(
            (
                server
                for server in context.servers
                if int(server.get("id") or 0) == int(server_id)
            ),
            None,
        )
        if selected_server is None:
            return _failure(
                str(context.order["order_id"]),
                status=NewKeySetupStatus.UNAVAILABLE,
                page_key="key_operation_unavailable",
                error_code="server_unavailable",
                context=context,
            )

    normalized_server_id = int(selected_server["id"])
    return _result_from_context(
        context,
        NewKeySetupStatus.PROVISIONING,
        server_id=normalized_server_id,
        server_name=str(selected_server.get("name") or "") or None,
    )


async def provision_new_key(
    setup: NewKeySetupResult,
    *,
    expected_telegram_id: int | None = None,
) -> NewKeySetupResult:
    """Provision one resolved draft exactly once within the process."""
    if setup.status is not NewKeySetupStatus.PROVISIONING:
        return setup
    if setup.internal_user_id is None:
        return replace(
            setup,
            status=NewKeySetupStatus.UNAVAILABLE,
            page_key="key_operation_failed",
            error_code="owner_unavailable",
        )

    async with user_locks[int(setup.internal_user_id)]:
        current = await resolve_new_key_setup(
            setup.order_id,
            expected_telegram_id=expected_telegram_id,
            server_id=setup.server_id,
        )
        if current.status is not NewKeySetupStatus.PROVISIONING:
            return current
        try:
            return await _provision_resolved_new_key(current)
        except Exception as error:
            logger.exception(
                "New key provisioning failed order=%s key=%s server=%s",
                current.order_id,
                current.key_id,
                current.server_id,
            )
            return replace(
                current,
                status=NewKeySetupStatus.RETRYABLE_FAILURE,
                page_key="key_operation_failed",
                error_code="provisioning_failed",
                error=str(error),
            )


@regular_panel_operation
async def _provision_resolved_new_key(
    setup: NewKeySetupResult,
) -> NewKeySetupResult:
    from bot.services.vpn_api import (
        get_client,
        get_key_expiry_time_ms,
        provision_client_on_server,
        resolve_key_panel_limits,
    )
    from bot.utils.billing_values import resolve_duration_days
    from bot.utils.panel_email import generate_unique_panel_email
    from database.requests import (
        find_order_by_order_id,
        get_key_details_for_user,
        get_tariff_by_id,
        get_user_by_id,
        update_payment_key_id,
        update_vpn_key_binding,
    )

    order = find_order_by_order_id(setup.order_id)
    if not order or not setup.key_id or not setup.telegram_id or not setup.server_id:
        raise RuntimeError("New-key setup context disappeared")
    key = get_key_details_for_user(setup.key_id, setup.telegram_id)
    if not key:
        raise RuntimeError("New-key draft disappeared")
    if _has_complete_binding(key):
        return replace(
            setup,
            status=NewKeySetupStatus.READY,
            key_data=dict(key),
            already_configured=True,
        )

    user = get_user_by_id(int(order["user_id"]))
    tariff = get_tariff_by_id(int(order.get("tariff_id") or key.get("tariff_id") or 0))
    if not user or not tariff:
        raise RuntimeError("New-key owner or tariff is unavailable")

    stable_identity = f"payment-key:{setup.order_id}:{setup.key_id}"
    panel_email = generate_unique_panel_email(
        user,
        stable_identity=stable_identity,
    )
    days = resolve_duration_days(order)
    limit_gb = max(0, int(tariff.get("traffic_limit_gb") or 0))
    persisted_limit_bytes = (
        max(0, int(key.get("traffic_limit") or 0))
        if "traffic_limit" in key
        else None
    )
    exact_expiry_time_ms = (
        get_key_expiry_time_ms(dict(key))
        if "expires_at" in key
        else None
    )
    panel_limits = resolve_key_panel_limits(key)
    requested_sub_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"yadrenovpn-subscription:{stable_identity}",
    ).hex
    provisioned = await provision_client_on_server(
        server_id=setup.server_id,
        email=panel_email,
        total_gb=limit_gb,
        total_gb_bytes=persisted_limit_bytes,
        expire_days=days,
        expiry_time_ms=exact_expiry_time_ms,
        limit_ip=panel_limits.limit_ip,
        limit_hwid=panel_limits.limit_hwid,
        enable=True,
        tg_id=str(setup.telegram_id),
        sub_id=requested_sub_id,
    )
    ready_count = len(provisioned.attached_inbound_ids)
    effective_sub_id = str(provisioned.sub_id or "").strip()
    if ready_count == 0 or not effective_sub_id:
        try:
            await (await get_client(setup.server_id)).delete_client(panel_email)
        except Exception:
            logger.exception(
                "Failed to clean unusable subscription candidate order=%s email=%s",
                setup.order_id,
                panel_email,
            )
        raise RuntimeError("Panel did not provision any subscription inbound")
    if not update_vpn_key_binding(
        key_id=setup.key_id,
        server_id=setup.server_id,
        panel_email=panel_email,
        sub_id=effective_sub_id,
    ):
        try:
            await (await get_client(setup.server_id)).delete_client(panel_email)
        except Exception:
            logger.exception(
                "Failed to clean unbound subscription candidate order=%s email=%s",
                setup.order_id,
                panel_email,
            )
        raise RuntimeError("Failed to persist subscription key configuration")

    try:
        from bot.services.subscription_composition import (
            schedule_key_subscription_reconciles,
        )

        schedule_key_subscription_reconciles(key_id=int(setup.key_id))
    except Exception as error:
        logger.warning(
            "Could not immediately schedule subscription composition after "
            "key configuration key=%s type=%s",
            setup.key_id,
            type(error).__name__,
        )

    if provisioned.complete:
        sync_stats = {
            "created": ready_count,
            "deleted": 0,
            "enabled": 0,
            "disabled": 0,
            "updated": 0,
            "skipped": ready_count,
            "reset": 0,
            "errors": 0,
            "ok": 1,
        }
    else:
        from bot.services.vpn_api import sync_key_to_panel_state

        sync_kwargs = (
            {"panel_snapshot": provisioned.snapshot}
            if provisioned.snapshot is not None
            else {}
        )
        try:
            sync_stats = await sync_key_to_panel_state(setup.key_id, **sync_kwargs)
        except Exception as error:
            logger.warning(
                "Initial subscription reconciliation failed order=%s key=%s: %s",
                setup.order_id,
                setup.key_id,
                error,
                exc_info=True,
            )
            sync_stats = {
                "created": ready_count,
                "deleted": 0,
                "enabled": 0,
                "disabled": 0,
                "updated": 0,
                "skipped": ready_count,
                "reset": 0,
                "errors": 1,
                "ok": 0,
            }
    event_context = {
        "panel_email": panel_email,
        "sub_id": effective_sub_id,
        "sync_stats": sync_stats,
    }

    update_payment_key_id(setup.order_id, setup.key_id)
    from bot.services.key_lifecycle import emit_key_lifecycle_event_safe

    await emit_key_lifecycle_event_safe(
        "key_configured",
        {
            "key_id": setup.key_id,
            "user_id": int(order["user_id"]),
            "tariff_id": int(order.get("tariff_id") or 0),
            "order_id": setup.order_id,
            "server_id": setup.server_id,
            "created_in_this_flow": False,
            **event_context,
        },
    )
    from bot.services.extension_completion import (
        run_extension_completion_after_key_configured,
    )

    await run_extension_completion_after_key_configured(
        setup.order_id,
        key_id=setup.key_id,
    )

    ready_key = get_key_details_for_user(setup.key_id, setup.telegram_id)
    if not ready_key:
        raise RuntimeError("Configured key cannot be loaded")
    return replace(
        setup,
        status=NewKeySetupStatus.READY,
        key_data=dict(ready_key),
        already_configured=False,
        page_key=None,
        error_code=None,
        error=None,
    )


__all__ = [
    "NewKeySetupResult",
    "NewKeySetupStatus",
    "provision_new_key",
    "resolve_new_key_setup",
]
