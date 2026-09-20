"""Lossless identity inspection and recovery through the official Clients API."""
from __future__ import annotations

import json
from urllib.parse import quote, urlencode

from core.results import CoreError

_CREDENTIALS = ('id', 'password', 'auth', 'secret', 'privateKey', 'publicKey', 'preSharedKey')
_TERMS = ('subId', 'expiryTime', 'totalGB', 'limitIp', 'limitHwid', 'enable', 'reset',
          'resetDay', 'resetMax', 'trafficReset', 'trafficResetDay')


async def all_identity_inbounds(client) -> list[dict]:
    """Read every physical placement, including ignored, disabled and other groups."""
    response = await client._request('GET', '/panel/api/inbounds/list')
    if not isinstance(response.get('obj'), list):
        raise CoreError('panel_identity_invalid')
    result = []
    for inbound in response['obj']:
        settings = inbound.get('settings')
        if isinstance(settings, str):
            settings = json.loads(settings)
        if not isinstance(settings, dict) or not isinstance(settings.get('clients', []), list):
            raise CoreError('panel_identity_invalid')
        result.append({**inbound, 'settings': settings})
    return result


def _placements(inbounds, names):
    names = {name.casefold() for name in names}
    rows = []
    for inbound in inbounds:
        for entry in inbound['settings'].get('clients', []):
            if str(entry.get('email') or '').casefold() in names:
                rows.append({'inbound_id': int(inbound['id']), 'protocol': inbound['protocol'],
                             'client': dict(entry)})
    return rows


def _record_payload(record, email):
    payload = dict(record, email=email)
    if record.get('uuid'):
        payload['id'] = record['uuid']
    if isinstance(payload.get('allowedIPs'), str):
        payload['allowedIPs'] = json.loads(payload['allowedIPs']) if payload['allowedIPs'] else []
    return payload


async def inspect_client_identity(client, email: str) -> dict:
    record = await client._get_client_record(email)
    if not record:
        raise CoreError('panel_client_missing')
    canonical, attached = client._split_record(record)
    placements = _placements(await all_identity_inbounds(client), {email})
    if attached != {item['inbound_id'] for item in placements} or not placements:
        raise CoreError('panel_identity_ambiguous')
    return {'version': 1, 'record': canonical, 'placements': placements,
            'sub_id': str(canonical.get('subId') or '')}


def _verify_placements(expected, current, names):
    before = {(item['inbound_id'], item['protocol']): item['client'] for item in expected['placements']}
    after = {(item['inbound_id'], item['protocol']): item['client'] for item in current}
    if len(after) != len(current) or before.keys() != after.keys():
        raise CoreError('panel_identity_changed')
    for key, original in before.items():
        observed = after[key]
        if observed.get('email') not in names:
            raise CoreError('panel_identity_changed')
        for field in _CREDENTIALS + _TERMS:
            if original.get(field) != observed.get(field):
                raise CoreError('panel_identity_changed')


async def rename_client_identity(client, old_email: str, new_email: str, expected_identity: dict) -> dict:
    """Reconcile both names without replacing credentials or restoring stale traffic."""
    if old_email == new_email or expected_identity.get('version') != 1:
        raise CoreError('panel_identity_invalid')
    records = []
    for name in (old_email, new_email):
        record = await client._get_client_record(name)
        if record:
            records.append((name, client._split_record(record)[0]))
    if len(records) != 1:
        raise CoreError('panel_identity_ambiguous')
    canonical_name, record = records[0]
    original = expected_identity['record']
    for field in ('id', 'uuid', 'password', 'auth', 'subId'):
        if original.get(field) != record.get(field):
            raise CoreError('panel_identity_changed')
    placements = _placements(await all_identity_inbounds(client), {old_email, new_email})
    _verify_placements(expected_identity, placements, {old_email, new_email})
    old_ids = [item['inbound_id'] for item in placements if item['client']['email'] == old_email]
    new_ids = [item['inbound_id'] for item in placements if item['client']['email'] == new_email]

    async def update(source, target, ids):
        endpoint = '/panel/api/clients/update/' + quote(source, safe='')
        if ids:
            endpoint += '?' + urlencode({'inboundIds': ','.join(map(str, ids))})
        await client._request('POST', endpoint, data=_record_payload(record, target), retry=False)

    if canonical_name == new_email and old_ids:
        # Newer panels select by email. Restore only the changed placements,
        # then retry forward. The durable snapshot owns both names throughout.
        await update(new_email, old_email, new_ids or old_ids)
        canonical_name = old_email
        old_ids += new_ids
    if canonical_name == old_email:
        await update(old_email, new_email, old_ids)
    verified = await inspect_client_identity(client, new_email)
    _verify_placements(expected_identity, verified['placements'], {new_email})
    if await client._get_client_record(old_email):
        raise CoreError('panel_identity_ambiguous')
    return verified
