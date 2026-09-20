"""Desired-state rules for bot-managed VPN panel clients."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bot.utils.panel_email import is_managed_key


def should_panel_client_exist(key: Mapping[str, Any]) -> bool:
    """Return whether the database key should be materialized on its panel."""
    if not is_managed_key(key):
        return False

    from database.requests import is_key_active

    return is_key_active(dict(key)) and not bool(key.get("is_banned", 0))


__all__ = ["should_panel_client_exist"]
