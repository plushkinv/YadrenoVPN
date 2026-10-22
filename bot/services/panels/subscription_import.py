"""Inspect full subscription groups through configured official panel APIs only."""
from __future__ import annotations

from core.results import CoreError


async def inspect_subscription_group(client, sub_id: str) -> dict:
    rows = await client._list_client_rows()
    records = {}
    for row in rows:
        email = str(row.get('email') or '')
        if not email or email.casefold() in records:
            raise CoreError('subscription_ambiguous')
        records[email.casefold()] = row
    names = {name for name, row in records.items() if row.get('subId') == sub_id}
    if not names:
        raise CoreError('subscription_not_found')
    members = []
    for name in sorted(names):
        row = records[name]
        full = await client._get_client_record(row['email'])
        if not full:
            raise CoreError('subscription_changed')
        canonical, attached = client._split_record(full)
        if canonical.get('email') != row['email'] or canonical.get('subId') != sub_id:
            raise CoreError('subscription_changed')
        traffic = client._traffic_used(row.get('traffic'))
        if traffic is None:
            traffic = client._traffic_used(row)
        if traffic is None:
            traffic = client._traffic_used(await client.get_client_stats(row['email']))
        if traffic is None:
            raise CoreError('panel_traffic_unavailable', retryable=True)
        members.append({'record': canonical, 'inbound_ids': sorted(attached), 'traffic_used': traffic})
    return {'version': 2, 'sub_ids': [sub_id], 'members': members}
