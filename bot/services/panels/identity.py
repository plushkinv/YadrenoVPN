"""Lossless identity inspection and recovery through the official Clients API."""
from __future__ import annotations

import json
from urllib.parse import quote, urlencode

from core.results import CoreError
from .base import PanelRejectedError

_IDENTITY = ('id', 'uuid', 'password', 'auth', 'secret', 'privateKey', 'publicKey', 'preSharedKey', 'subId')
_TERMS = ('expiryTime', 'totalGB', 'limitIp', 'limitHwid', 'enable', 'reset',
          'resetDay', 'resetMax', 'trafficReset', 'trafficResetDay')


def _snapshot_inbound_ids(snapshot):
    """Read current snapshots and already persisted version-1 rename journals."""
    try:
        if snapshot.get('version') == 1:
            values = [item['inbound_id'] for item in snapshot['placements']]
        elif snapshot.get('version') == 2:
            values = snapshot['inbound_ids']
        else:
            raise ValueError()
        ids = {int(value) for value in values}
        if not ids or len(ids) != len(values) or min(ids) <= 0:
            raise ValueError()
        return ids
    except (KeyError, TypeError, ValueError):
        raise CoreError('panel_identity_invalid') from None


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
    if canonical.get('email') != email or not attached:
        raise CoreError('panel_identity_ambiguous')
    return {'version': 2, 'record': canonical, 'inbound_ids': sorted(attached),
            'sub_id': str(canonical.get('subId') or '')}


def _verify_identity(original, current):
    if any(original.get(field) != current.get(field) for field in _IDENTITY):
        raise CoreError('panel_identity_changed')


async def _current_identity(client, old_email, new_email, original):
    records = []
    for name in (old_email, new_email):
        record = await client._get_client_record(name)
        if record:
            canonical, attached = client._split_record(record)
            if canonical.get('email') != name:
                raise CoreError('panel_identity_changed')
            records.append((name, canonical, attached))
    if len(records) != 1:
        raise CoreError('panel_identity_ambiguous')
    name, record, attached = records[0]
    _verify_identity(original, record)
    return name, record, attached


async def _partial_rename_ids(client, old_email, new_email, expected_ids):
    """Locate names only after Clients API rejects continuation of a rename."""
    response = await client._request('GET', '/panel/api/inbounds/list')
    if not isinstance(response.get('obj'), list):
        raise CoreError('panel_identity_invalid')
    ids = {old_email: set(), new_email: set()}
    seen = set()
    for inbound in response['obj']:
        settings = inbound.get('settings')
        if isinstance(settings, str):
            settings = json.loads(settings)
        if not isinstance(settings, dict) or not isinstance(settings.get('clients', []), list):
            raise CoreError('panel_identity_invalid')
        for entry in settings.get('clients', []):
            name = entry.get('email')
            if name not in ids:
                continue
            inbound_id = int(inbound['id'])
            if inbound_id in seen:
                raise CoreError('panel_identity_ambiguous')
            seen.add(inbound_id)
            ids[name].add(inbound_id)
    if seen != expected_ids:
        raise CoreError('panel_identity_changed')
    return ids[old_email], ids[new_email]


async def rename_client_identity(client, old_email: str, new_email: str, expected_identity: dict) -> dict:
    """Confirm the canonical change; recover partial upstream writes by name only."""
    if old_email == new_email:
        raise CoreError('panel_identity_invalid')
    inbound_ids = _snapshot_inbound_ids(expected_identity)
    original = expected_identity['record']
    canonical_name, record, attached = await _current_identity(client, old_email, new_email, original)
    if canonical_name == old_email and attached != inbound_ids:
        raise CoreError('panel_identity_changed')

    async def update(source, target, current, ids):
        endpoint = '/panel/api/clients/update/' + quote(source, safe='')
        endpoint += '?' + urlencode({'inboundIds': ','.join(map(str, sorted(ids)))})
        desired = dict(current, email=target)
        if 'expiry_time_ms' in expected_identity:
            desired['expiryTime'] = expected_identity['expiry_time_ms']
        await client._request('POST', endpoint, data=_record_payload(desired, target), retry=False)
        return desired

    try:
        desired = await update(canonical_name, new_email, record, inbound_ids)
    except PanelRejectedError:
        if canonical_name != new_email:
            raise
        # A lost response may conceal a partially applied rename. Most repeats
        # finish through Clients API alone. Older email-selected implementations
        # need the changed subset restored before the full forward rename.
        canonical_name, record, _ = await _current_identity(client, old_email, new_email, original)
        if canonical_name != new_email:
            raise CoreError('panel_identity_changed')
        old_ids, new_ids = await _partial_rename_ids(client, old_email, new_email, inbound_ids)
        if not old_ids:
            raise
        await update(new_email, old_email, record, new_ids or old_ids)
        canonical_name, record, _ = await _current_identity(client, old_email, new_email, original)
        if canonical_name != old_email:
            raise CoreError('panel_identity_changed')
        desired = await update(old_email, new_email, record, inbound_ids)

    verified = await inspect_client_identity(client, new_email)
    _verify_identity(original, verified['record'])
    if (set(verified['inbound_ids']) != inbound_ids or
            any(desired.get(field) != verified['record'].get(field) for field in _TERMS)):
        raise CoreError('panel_identity_changed')
    if await client._get_client_record(old_email):
        raise CoreError('panel_identity_ambiguous')
    return verified
