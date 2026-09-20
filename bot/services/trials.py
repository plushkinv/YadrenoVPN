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
        result = claim_trial_offer(
            normalized_user_id, int(offer_id),
            event_subscribers=event_subscribers('trial.activated'),
        )
    if result.get('ok'):
        await emit_trial_key_created(normalized_user_id, int(offer_id), result)
    return result


async def emit_trial_key_created(user_id, offer_id, result):
    """Retain the released lifecycle payload for every successful trial claim."""
    from bot.services.key_lifecycle import emit_key_lifecycle_event_safe
    offer = result['offer']
    await emit_key_lifecycle_event_safe('key_created', {
        'key_id': int(result['key_id']), 'user_id': user_id, 'tariff_id': int(offer['tariff_id']),
        'days': max(0, int(offer.get('duration_days') or 0)),
        'traffic_limit': max(0, int(offer.get('traffic_limit_gb') or 0)) * 1024**3,
        'order_id': str(result['order_id']), 'payment_type': 'trial', 'source': 'trial',
        'trial_offer_id': offer_id})


__all__ = ['activate_trial_offer']
