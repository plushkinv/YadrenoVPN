"""Safe account subscription views and explicit access-secret retrieval."""
from core.accounts import owned_key, require_account
from core.context import bind_account_context
from core.operations import positive_id
from core.results import CoreError
from database import requests as db


def pagination(limit=50, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
        raise CoreError('invalid_request')
    return {'limit': limit, 'offset': offset}


async def summary(account, key_id):
    from bot.utils.action_policy import run_account_action_policies
    from bot.utils.panel_version import panel_version_at_least
    from bot.services.order_terms import key_servers
    key = owned_key(account, positive_id(key_id, 'key_id'))
    configured = bool(key.get('server_id') and key.get('sub_id') and key.get('panel_email'))
    active, exhausted = db.is_key_active(key), db.is_traffic_exhausted(key)
    pending = db.get_account_pending_key_operations(account.account_id, key_id)
    identity = db.get_pending_panel_identity(key_id)
    imported = db.get_imported_key_binding(key_id)
    observation = imported['snapshot'].get('observation', {}) if imported else {}
    first_use = bool(imported and imported['preserve_terms'] and
                     int(imported['snapshot'].get('control', {}).get('expiry_ms',
                         observation.get('expiry_ms', imported['snapshot']['record'].get('expiryTime') or 0))) < 0)
    entitlement = db.get_key_entitlement(key_id)
    panel_enabled = (imported['snapshot'].get('control', {}).get('ban_restore_enable',
                     observation.get('enabled', imported['snapshot']['record'].get('enable', True))) if imported else True)
    access = ('unconfigured' if not configured else 'exhausted' if exhausted else 'expired' if not active
              else 'disabled' if imported and imported['preserve_terms'] and not panel_enabled
              else 'first_use' if first_use else 'active')
    readiness = 'pending' if entitlement and not entitlement['panel_applied'] else 'ready' if configured else 'unconfigured'
    eligible = {'key.rename.start': True, 'key.renew.start': True, 'key.delete': not active,
                'key.configure.start': not configured and active and not exhausted,
                'key.replace.start': configured and active and not exhausted}
    actions = {}
    with bind_account_context(account):
        for name, allowed in eligible.items():
            reason = None if allowed else 'action_unavailable'
            if allowed and (pending or identity):
                allowed, reason = False, 'operation_pending'
            if allowed:
                decision = await run_account_action_policies(name, {'key_id': key_id}, account=account, phase='preview')
                if decision['decision'] != 'continue':
                    allowed, reason = False, 'action_unavailable'
            actions[name] = {'allowed': allowed, 'reason': reason}
    return {'id': key_id, 'name': key.get('custom_name'), 'tariff_id': key.get('tariff_id'),
            'tariff_name': key.get('tariff_name'), 'tariff_known': key.get('tariff_id') is not None,
            'server_id': key.get('server_id'), 'server_name': key.get('server_name'),
            'expires_at': key.get('expires_at'), 'created_at': key.get('created_at'),
            'state': access, 'access_status': readiness, 'imported': bool(imported),
            'traffic': {'used_bytes': key.get('traffic_used'), 'limit_bytes': key.get('traffic_limit'),
                        'known': key.get('traffic_updated_at') is not None, 'updated_at': key.get('traffic_updated_at'),
                        'source': 'panel' if key.get('traffic_updated_at') is not None else 'unknown'},
            'devices_available': configured and db.get_device_limit_mode() == db.DEVICE_LIMIT_MODE_HWID
                                 and panel_version_at_least(key.get('panel_version'), '3.7.0'),
            'actions': actions, 'pending_operations': pending,
            'servers': [{'id': server['id'], 'name': server['name']} for server in key_servers(key)]}


async def list_subscriptions(account, *, limit=50, offset=0):
    require_account(account)
    paging = pagination(limit, offset)
    return {'items': [await summary(account, key_id) for key_id in db.get_account_subscription_ids(account.account_id, **paging)],
            **paging}


async def access(account, key_id):
    from bot.services.vpn_api import get_subscription_url_for_key
    key = owned_key(account, positive_id(key_id, 'key_id'))
    if not key.get('server_id') or not key.get('panel_email') or not key.get('sub_id'):
        raise CoreError('subscription_unconfigured')
    try:
        url = await get_subscription_url_for_key(key, suppress_errors=False)
    except Exception:
        raise CoreError('panel_unavailable', retryable=True) from None
    if not url:
        raise CoreError('panel_unavailable', retryable=True)
    return {'subscription_id': key_id, 'url': url}


def history(account, key_id, *, limit=50, offset=0):
    owned_key(account, positive_id(key_id, 'key_id'))
    paging = pagination(limit, offset)
    return {'items': db.get_account_key_history(account.account_id, key_id, **paging), **paging}


def operation(account, operation_id):
    require_account(account)
    value = db.get_account_key_operation(account.account_id, operation_id)
    if value is None:
        raise CoreError('operation_not_found')
    result = value['result']
    return {'operation_id': value['id'], 'kind': value['kind'], 'key_id': value['request']['inputs']['key_id'],
            'state': 'pending' if result is None else result.get('state', 'completed'), 'result': result}
