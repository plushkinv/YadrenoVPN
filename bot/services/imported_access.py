"""Purchased imported access preserves its existing credentials and placements."""
from core.results import CoreError
from database import requests as db


def observe_imported_snapshots(keys, snapshots):
    """Reuse one panel snapshot per server; observation never mutates the panel."""
    imported = db.get_imported_panel_key_ids(preserve_terms_only=True)
    pending = db.get_pending_key_mutation_ids()
    for key in keys:
        if key['id'] not in imported or key['id'] in pending:
            continue
        try:
            binding = db.get_imported_key_binding(key['id'])
        except CoreError:
            continue
        if not binding:
            continue
        snapshot = snapshots.get(key.get('server_id'))
        state = snapshot.get_client(key.get('panel_email')) if snapshot else None
        if state is None or not state.traffic_known or state.sub_id != key.get('sub_id'):
            continue
        if db.observe_imported_key_state(key['id'], server_id=key['server_id'], email=key['panel_email'],
                sub_id=state.sub_id, expiry_ms=int(state.expiry_time), traffic_limit=int(state.total_gb),
                traffic_used=int(state.traffic_used), enabled=state.enable):
            fresh = db.get_vpn_key_by_id(key['id'])
            for field in ('expires_at', 'traffic_limit', 'traffic_limit_override', 'traffic_used', 'traffic_updated_at'):
                key[field] = fresh[field]


async def inspect_imported_renewal(key_id: int) -> dict | None:
    binding = db.get_imported_key_binding(key_id)
    if not binding or not binding['preserve_terms']:
        return None
    server = db.get_server_by_id(binding['server_id'])
    if server is None:
        raise CoreError('panel_unavailable', retryable=True)
    db.assert_panel_identity_ready(server, binding['panel_email'])
    from bot.services.vpn_api import get_client_from_server_data
    from bot.services.panels.identity import inspect_client_identity
    client = get_client_from_server_data(server)
    current = await inspect_client_identity(client, binding['panel_email'])
    saved = binding['snapshot']['record']
    if any(current['record'].get(field) != saved.get(field) for field in ('id', 'uuid', 'password', 'subId')):
        raise CoreError('panel_identity_changed')
    traffic = client._traffic_used(await client.get_client_stats(binding['panel_email']))
    if traffic is None:
        raise CoreError('panel_traffic_unavailable', retryable=True)
    return {'expiry_ms': int(current['record'].get('expiryTime') or 0),
            'traffic_used': traffic, 'traffic_limit': int(current['record'].get('totalGB') or 0),
            'enabled': bool(current['record'].get('enable', True)),
            'server_id': binding['server_id'], 'email': binding['panel_email'], 'sub_id': binding['sub_id']}


async def sync_preserved_controls(key, binding, *, dry_run=False):
    """Apply only an explicit day change or ban, preserving every other panel term."""
    from bot.services.vpn_api import get_client_from_server_data, _empty_sync_stats
    from bot.services.panels.identity import _record_payload
    from urllib.parse import quote
    stats = _empty_sync_stats()
    control = dict(binding['snapshot'].get('control') or {})
    if not control and not key.get('is_banned'):
        return {**stats, 'ok': 1, 'skipped': 1}
    if dry_run:
        return {**stats, 'ok': 1, 'updated': 1}
    panel = get_client_from_server_data(db.get_server_by_id(key['server_id']))
    record = await panel._get_client_record(key['panel_email'])
    if not record:
        raise CoreError('panel_client_missing', retryable=True)
    current, _ = panel._split_record(record)
    saved = binding['snapshot']['record']
    if any(current.get(field) != saved.get(field) for field in ('id', 'uuid', 'password', 'subId')):
        raise CoreError('panel_identity_changed')
    desired = dict(current)
    acknowledged = {}
    if 'expiry_ms' in control:
        desired['expiryTime'] = control['expiry_ms']
        acknowledged['expiry_ms'] = control['expiry_ms']
    if key.get('is_banned'):
        if 'ban_restore_enable' not in control:
            control['ban_restore_enable'] = bool(current.get('enable', True))
            db.update_imported_control(key['id'], {'ban_restore_enable': control['ban_restore_enable']})
        desired['enable'] = False
    elif 'ban_restore_enable' in control:
        desired['enable'] = control['ban_restore_enable']
        acknowledged['ban_restore_enable'] = control['ban_restore_enable']
    if desired != current:
        await panel._request('POST', '/panel/api/clients/update/' + quote(key['panel_email'], safe=''),
                             data=_record_payload(desired, key['panel_email']), retry=False)
    verified = await panel._get_client_record(key['panel_email'])
    actual = panel._split_record(verified)[0] if verified else {}
    if any(actual.get(field) != desired.get(field) for field in ('subId', 'expiryTime', 'enable')):
        raise CoreError('panel_update_pending', retryable=True)
    db.update_imported_control(key['id'], acknowledged=acknowledged)
    return {**stats, 'ok': 1, 'updated': 1}


async def sync_imported_entitlement(key: dict, binding: dict, *, dry_run: bool, device_limit_mode=None) -> dict:
    from bot.services.vpn_api import (
        get_client_from_server_data, get_key_expiry_time_ms, resolve_key_panel_limits, calculate_panel_total_for_key,
    )
    client = get_client_from_server_data(db.get_server_by_id(key['server_id']))
    record = await client._get_client_record(key['panel_email'])
    if not record:
        raise CoreError('panel_client_missing', retryable=True)
    current, _ = client._split_record(record)
    original = binding['snapshot']['record']
    if any(current.get(field) != original.get(field) for field in ('id', 'uuid', 'password', 'subId')):
        raise CoreError('panel_identity_changed')
    traffic = client._traffic_used(await client.get_client_stats(key['panel_email']))
    if traffic is None:
        raise CoreError('panel_traffic_unavailable', retryable=True)
    limits = resolve_key_panel_limits(key, mode=device_limit_mode)
    if not dry_run:
        # No scoped snapshot: the official update targets all existing memberships.
        # It neither creates a missing client nor attaches/detaches any inbound.
        if not await client.update_client_full(
                email=key['panel_email'], total_gb_bytes=calculate_panel_total_for_key(key, traffic),
                expiry_time_ms=get_key_expiry_time_ms(key), enable=db.is_key_active(key) and not key.get('is_banned'),
                limit_ip=limits.limit_ip, limit_hwid=limits.limit_hwid if client.supports_client_hwids() else None,
                sub_id=key['sub_id'], reset=0):
            raise CoreError('panel_unavailable', retryable=True)
    return {'created': 0, 'deleted': 0, 'enabled': 0, 'disabled': 0, 'updated': 1,
            'skipped': 0, 'reset': 0, 'errors': 0, 'ok': 1}
