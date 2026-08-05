"""Shared normalization helpers for persisted billing payloads."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def resolve_duration_days(
    values: Mapping[str, Any],
    *,
    fallback: int = 30,
) -> int:
    """Returns stored duration while preserving zero as unlimited."""
    for field in ('period_days', 'duration_days'):
        value = values.get(field)
        if value is not None:
            return max(0, int(value))
    return max(0, int(fallback))


__all__ = ['resolve_duration_days']
