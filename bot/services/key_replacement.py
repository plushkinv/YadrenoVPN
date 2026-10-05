"""Recoverable candidate-first replacement shared by authenticated adapters."""
from core.results import CoreError
from database import requests as db


def _binding(key):
    return {'server_id': key.get('server_id'), 'email': key.get('panel_email'), 'sub_id': key.get('sub_id')}


async def replace_key(account, operation, key):
    from bot.services import vpn_api
    from bot.utils.panel_email import generate_unique_panel_email, is_managed_key
    from core.panel_identity import physical_panel_key
    import uuid

    inputs = operation['request']['inputs']
    progress = operation['request'].get('progress')
    key_id = inputs['key_id']
    if progress is None:
        target_id = inputs['server_id']
        from bot.services.order_terms import key_servers
        candidates = key_servers(key)
        if target_id not in {item['id'] for item in candidates} or not db.is_key_active(key) or db.is_traffic_exhausted(key):
            raise CoreError('action_unavailable')
        owner = db.get_user_by_id(account.account_id)
        stable = 'replace:' + operation['id']
        progress = {'phase': 'prepared', 'old': _binding(key), 'old_key': dict(key),
                    'old_managed': is_managed_key(key), 'old_active': bool(key.get('server_active')),
                    'target': {'server_id': target_id, 'email': generate_unique_panel_email(owner, stable_identity=stable),
                               'sub_id': uuid.uuid5(uuid.NAMESPACE_URL, stable).hex}}
        progress['target_endpoint'] = physical_panel_key(db.get_server_by_id(target_id))
        progress['old_endpoint'] = physical_panel_key(db.get_server_by_id(key['server_id'])) if key.get('server_id') else None
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
    target = progress['target']
    target_server = db.get_server_by_id(target['server_id'])
    old_server = db.get_server_by_id(progress['old']['server_id']) if progress['old']['server_id'] else None
    if (not target_server or physical_panel_key(target_server) != progress['target_endpoint'] or
            (progress['old_endpoint'] and (not old_server or physical_panel_key(old_server) != progress['old_endpoint']))):
        raise CoreError('panel_identity_changed', retryable=True)
    if progress['phase'] == 'prepared':
        if _binding(key) != progress['old']:
            raise CoreError('panel_identity_changed')
        traffic = int(key.get('traffic_used') or 0)
        if progress['old']['server_id'] and progress['old_active'] and progress['old_managed']:
            old_client = await vpn_api.get_client(progress['old']['server_id'])
            if int(key.get('traffic_limit') or 0) > 0:
                snapshot = await vpn_api.get_key_traffic_snapshot(old_client, key)
                if not snapshot:
                    raise CoreError('panel_traffic_unavailable', retryable=True)
                traffic = snapshot['traffic_used']
        key = {**key, 'traffic_used': traffic}
        if not db.is_key_active(key) or db.is_traffic_exhausted(key):
            raise CoreError('action_unavailable')
        limits = vpn_api.resolve_key_panel_limits(key)
        progress['traffic_used'] = traffic
        progress['remaining_bytes'] = vpn_api.calculate_panel_total_for_key(key, 0) if key.get('traffic_limit') else 0
        progress['traffic_limit'] = key.get('traffic_limit') or 0
        progress['expires_at'] = key.get('expires_at')
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
        panel = await vpn_api.get_client(target['server_id'])
        existing_candidate = await panel._get_client_record(target['email'])
        if existing_candidate and panel._split_record(existing_candidate)[0].get('subId') != target['sub_id']:
            raise CoreError('panel_identity_changed')
        # Persist before the first external write, including an unknown response.
        progress['candidate_started'] = True
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
        candidate = await vpn_api.provision_client_on_server(
            server_id=target['server_id'], email=target['email'], sub_id=target['sub_id'],
            total_gb_bytes=progress['remaining_bytes'],
            expiry_time_ms=vpn_api.get_key_expiry_time_ms(key),
            limit_ip=limits.limit_ip,
            limit_hwid=limits.limit_hwid,
            enable=not key.get('is_banned'),
            tg_id=str(account.telegram_id) if account.telegram_id is not None else '', client=panel)
        if not candidate.attached_inbound_ids or candidate.sub_id != target['sub_id']:
            raise CoreError('panel_provisioning_pending', retryable=True)
        progress['repair_needed'] = not candidate.complete
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
        db.switch_key_operation_binding(account.account_id, operation['id'])
        progress['phase'] = 'switched'
        from bot.services.subscription_composition import schedule_key_subscription_reconciles
        schedule_key_subscription_reconciles(key_id=key_id)
    current = db.get_vpn_key_by_id(key_id)
    if not current or _binding(current) != target:
        raise CoreError('panel_identity_changed')
    if progress.get('repair_needed') or current.get('is_banned'):
        synced = await vpn_api.sync_key_to_panel_state(key_id)
        if not synced.get('ok') or synced.get('errors'):
            raise CoreError('panel_provisioning_pending', retryable=True)
        progress['repair_needed'] = False
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
    if progress['old']['server_id'] and progress['old_active'] and progress['old_managed']:
        old_client = await vpn_api.get_client(progress['old']['server_id'])
        if not await old_client.delete_client(progress['old']['email']):
            raise CoreError('panel_cleanup_pending', retryable=True)
    from bot.services.key_lifecycle import emit_key_lifecycle_event_safe
    if progress['phase'] != 'completed':
        await emit_key_lifecycle_event_safe('key_replaced', {
            'key_id': key_id, 'user_id': account.account_id, 'telegram_id': account.telegram_id,
            'old_key': progress['old_key'], 'new_key': current, 'old_server_id': progress['old']['server_id'],
            'new_server_id': target['server_id'], 'traffic_used': progress['traffic_used'],
            'traffic_limit': progress['traffic_limit'], 'remaining_bytes': progress['remaining_bytes']})
    progress['phase'] = 'completed'
    db.save_key_operation_progress(account.account_id, operation['id'], progress)
    return {'key_id': key_id, 'state': 'completed'}


async def abort_unswitched_candidate(operation):
    """Release a rejected receipt only after its own partial candidate is absent."""
    from bot.services.vpn_api import get_client
    from core.panel_identity import physical_panel_key
    progress = operation['request'].get('progress') or {}
    if progress.get('phase') != 'prepared' or not progress.get('candidate_started'):
        return False
    current = db.get_vpn_key_by_id(operation['request']['inputs']['key_id'])
    if not current or _binding(current) != progress['old']:
        return False
    target = progress['target']
    server = db.get_server_by_id(target['server_id'])
    if not server or physical_panel_key(server) != progress['target_endpoint']:
        return False
    panel = await get_client(target['server_id'])
    record = await panel._get_client_record(target['email'])
    if not record:
        return True
    if panel._split_record(record)[0].get('subId') != target['sub_id']:
        return False
    if not await panel.delete_client(target['email']):
        return False
    return not await panel._get_client_record(target['email'])
