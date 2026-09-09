"""Shared SQL for time/traffic inactivity; aliases are internal SQL identifiers."""
from __future__ import annotations


def expired_term_sql(alias: str) -> str:
    """Reject non-date legacy values while accepting SQL/ISO UTC timestamps."""
    value = f"{alias}.expires_at"
    return (
        f"(COALESCE(TRIM(CAST({value} AS TEXT)) GLOB "
        "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) "
        f"AND COALESCE(datetime({value}) <= datetime('now'), 0))"
    )


def inactive_key_sql(alias: str) -> str:
    """Ban and panel availability do not define entitlement inactivity."""
    return (
        f"({expired_term_sql(alias)} OR "
        f"(COALESCE({alias}.traffic_limit, 0) > 0 AND "
        f"COALESCE({alias}.traffic_used, 0) >= {alias}.traffic_limit))"
    )


def inactive_since_sql(alias: str) -> str:
    """Time can elapse without a write; its exact deadline supplies the start."""
    return (
        f"COALESCE({alias}.inactive_since, "
        f"CASE WHEN {expired_term_sql(alias)} THEN {alias}.expires_at END)"
    )


def inactivity_token_sql(alias: str) -> str:
    """Keep released deadline tokens for an as-yet unmaterialized time expiry."""
    return (
        f"COALESCE({alias}.inactive_event_token, "
        f"CASE WHEN {expired_term_sql(alias)} THEN {alias}.expires_at END)"
    )
