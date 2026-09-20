"""Expired-key removal retains remote cleanup progress after the local delete."""
from core.results import CoreError
from database import requests as db


async def delete_key(account, operation, key):
    from bot.services.subscription_composition import list_key_subscription_reconcile_host_ids, schedule_subscription_host_reconciles
    from bot.utils.panel_email import is_managed_key
    progress = operation['request'].get('progress')
    key_id = operation['request']['inputs']['key_id']
    if progress is None:
        from core.panel_identity import physical_panel_key
        server = db.get_server_by_id(key['server_id']) if key.get('server_id') else None
        progress = {'key': dict(key), 'managed': is_managed_key(key),
                    'endpoint': physical_panel_key(server) if server else None,
                    'host_ids': list(list_key_subscription_reconcile_host_ids(key_id=key_id, include_self=False))}
        db.save_key_operation_progress(account.account_id, operation['id'], progress)
    saved = progress['key']
    panel_pending = False
    if saved.get('server_id') and progress['managed']:
        from core.panel_identity import physical_panel_key
        current_server = db.get_server_by_id(saved['server_id'])
        if not current_server or physical_panel_key(current_server) != progress['endpoint']:
            raise CoreError('panel_identity_changed', retryable=True)
        try:
            from bot.services.vpn_api import get_client
            panel_pending = not await (await get_client(saved['server_id'])).delete_client(saved['panel_email'])
        except Exception:
            panel_pending = True
    if db.get_vpn_key_by_id(key_id):
        db.delete_vpn_key(key_id)
    progress['deleted'] = True
    db.save_key_operation_progress(account.account_id, operation['id'], progress)
    if progress['host_ids']:
        schedule_subscription_host_reconciles(host_key_ids=tuple(progress['host_ids']))
    if panel_pending:
        raise CoreError('panel_cleanup_pending', retryable=True)
    return {'key_id': key_id, 'state': 'completed', 'deleted': True}
