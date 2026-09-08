"""Core orchestration for atomic trial-offer claims."""
from __future__ import annotations

from typing import Any


async def activate_trial_offer(user_id: int, offer_id: int) -> dict[str, Any]:
    """Serializes one user's claim and delegates the atomic write to the DB layer."""
    from bot.services.user_locks import user_locks
    from database.requests import claim_trial_offer
    from bot.utils.extension_event_registry import event_subscribers

    normalized_user_id = int(user_id)
    async with user_locks[normalized_user_id]:
        return claim_trial_offer(
            normalized_user_id, int(offer_id),
            event_subscribers=event_subscribers('trial.activated'),
        )


__all__ = ['activate_trial_offer']
