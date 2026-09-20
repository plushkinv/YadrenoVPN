"""Inspect full subscription groups through configured official panel APIs only."""
from __future__ import annotations

from core.results import CoreError
from .identity import all_identity_inbounds, _placements


async def inspect_subscription_group(client, sub_id: str) -> dict:
    rows = await client._list_client_rows()
    inbounds = await all_identity_inbounds(client)
    records = {}
    for row in rows:
        email = str(row.get('email') or '')
        if not email or email.casefold() in records:
            raise CoreError('subscription_ambiguous')
        records[email.casefold()] = row
    names, sub_ids = set(), {sub_id}
    placements = [entry for inbound in inbounds for entry in inbound['settings'].get('clients', [])]
    # A client may carry historical subscription aliases in its physical placements.
    while True:
        before = (len(names), len(sub_ids))
        for row in [*rows, *placements]:
            email = str(row.get('email') or '').casefold()
            value = str(row.get('subId') or '')
            if email in names or value in sub_ids:
                if not email:
                    raise CoreError('subscription_ambiguous')
                names.add(email)
                if value:
                    sub_ids.add(value)
        if before == (len(names), len(sub_ids)):
            break
    if not names:
        raise CoreError('subscription_not_found')
    members = []
    for name in sorted(names):
        if name not in records:
            raise CoreError('subscription_ambiguous')
        row = records[name]
        full = await client._get_client_record(row['email'])
        if not full:
            raise CoreError('subscription_changed')
        canonical, attached = client._split_record(full)
        actual = _placements(inbounds, {row['email']})
        if (canonical.get('email') != row['email'] or canonical.get('subId') not in sub_ids or
                attached != {item['inbound_id'] for item in actual} or
                len(attached) != len(actual)):
            raise CoreError('subscription_changed')
        if any(str(item['client'].get('subId') or '') not in sub_ids for item in actual):
            raise CoreError('subscription_changed')
        traffic = client._traffic_used(row.get('traffic'))
        if traffic is None:
            traffic = client._traffic_used(row)
        if traffic is None:
            traffic = client._traffic_used(await client.get_client_stats(row['email']))
        if traffic is None:
            raise CoreError('panel_traffic_unavailable', retryable=True)
        members.append({'record': canonical, 'placements': actual, 'traffic_used': traffic})
    return {'version': 1, 'sub_ids': sorted(sub_ids), 'members': members}
