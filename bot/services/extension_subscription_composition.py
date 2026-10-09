"""Idempotent Core-facade commands for key subscription composition."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

_OPERATIONS = {
    "bind_key_subscription",
    "unbind_key_subscription",
    "request_key_subscription_reconcile",
}


async def apply_extension_subscription_operation(
    *,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    owner_user_id: int,
    host_key_id: int,
    component_key_id: int | None = None,
) -> dict[str, Any]:
    """Apply one owner-scoped graph command through the shared operation ledger."""
    from database.requests import (
        build_extension_core_request_fingerprint,
        claim_extension_core_operation,
        finalize_extension_core_operation,
        normalize_extension_core_idempotency_key,
    )

    if operation not in _OPERATIONS:
        raise ValueError("unsupported subscription composition operation")
    normalized_key = normalize_extension_core_idempotency_key(idempotency_key)
    owner_id = _positive_int(owner_user_id, "owner_user_id")
    host_id = _positive_int(host_key_id, "host_key_id")
    component_id = (
        None
        if component_key_id is None
        else _positive_int(component_key_id, "component_key_id")
    )
    if operation != "request_key_subscription_reconcile" and component_id is None:
        raise ValueError("component_key_id is required")
    if operation == "request_key_subscription_reconcile" and component_id is not None:
        raise ValueError("component_key_id is not accepted for reconcile")

    payload = {
        "contract_version": 1,
        "host_key_id": host_id,
        "component_key_id": component_id,
    }
    amount = component_id if component_id is not None else host_id
    reason = "key_subscription_composition"
    fingerprint = build_extension_core_request_fingerprint(
        operation=operation,
        target_user_id=owner_id,
        amount=amount,
        reason=reason,
        payload=payload,
    )
    claimed = claim_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=normalized_key,
        operation=operation,
        target_user_id=owner_id,
        amount=amount,
        reason=reason,
        request_fingerprint=fingerprint,
        request_payload=payload,
    )
    if not claimed.get("claimed"):
        return _replayed_result(claimed)

    source_namespace = f"extension:{extension_id}"
    source_reference = f"{extension_id}:{normalized_key}"
    try:
        from bot.services.subscription_composition import (
            bind_key_subscription,
            request_key_subscription_reconcile,
            unbind_key_subscription,
        )

        if operation == "bind_key_subscription":
            domain_result = await bind_key_subscription(
                host_key_id=host_id,
                component_key_id=int(component_id),
                source_namespace=source_namespace,
                owner_user_id=owner_id,
                source_reference=source_reference,
            )
        elif operation == "unbind_key_subscription":
            domain_result = await unbind_key_subscription(
                host_key_id=host_id,
                component_key_id=int(component_id),
                owner_user_id=owner_id,
            )
        else:
            domain_result = await request_key_subscription_reconcile(
                host_key_id=host_id,
                owner_user_id=owner_id,
            )
    except Exception as error:
        logger.error(
            "Extension subscription operation failed extension=%s operation=%s "
            "error_type=%s",
            extension_id,
            operation,
            type(error).__name__,
        )
        # Keep the operation claim pending. Reusing the same idempotency key
        # will safely resume the command; every underlying graph mutation is
        # itself idempotent.
        return {
            **claimed,
            "ok": False,
            "status": "retrying",
            "stored_status": "pending",
            "error_code": "operation_failed",
            "applied": False,
            "already_applied": False,
            "operation": operation,
            "host_key_id": host_id,
            "component_key_id": component_id,
        }

    stored_status = "applied" if domain_result.get("ok") else "rejected"
    metadata = {
        "operation": operation,
        "host_key_id": host_id,
        "component_key_id": component_id,
        **_safe_domain_result(domain_result),
    }
    finalized = finalize_extension_core_operation(
        extension_id=extension_id,
        idempotency_key=normalized_key,
        status=stored_status,
        metadata=metadata,
    )
    return {
        **finalized,
        **_safe_domain_result(domain_result),
        "stored_status": stored_status,
        "operation": operation,
        "host_key_id": host_id,
        "component_key_id": component_id,
    }


def _replayed_result(ledger: dict[str, Any]) -> dict[str, Any]:
    metadata = ledger.get("metadata")
    domain = _safe_domain_result(metadata if isinstance(metadata, dict) else {})
    stored_status = str(ledger.get("stored_status") or ledger.get("status") or "failed")
    if stored_status == "applied":
        domain["ok"] = True
        domain["applied"] = False
        domain["already_applied"] = True
    return {
        **ledger,
        **domain,
        "stored_status": stored_status,
        "already_applied": bool(domain.get("already_applied")),
    }


def _safe_domain_result(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "ok",
        "status",
        "error_code",
        "binding_id",
        "host_key_id",
        "component_key_id",
        "applied",
        "already_applied",
    }
    return {key: value.get(key) for key in allowed if key in value}


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


__all__ = ["apply_extension_subscription_operation"]
