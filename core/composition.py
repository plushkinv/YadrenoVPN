"""Authenticated transport for the existing stock group-parent composition."""
from dataclasses import replace

from core.accounts import owned_key
from core.context import bind_account_context
from core.operations import operation_fingerprint, positive_id
from core.results import CoreError
from database import requests as db


def candidates(account, key_id):
    owned_key(account, positive_id(key_id, 'key_id'))
    from bot.services.subscription_host_flow import load_default_hosts
    return [{key: host.get(key) for key in ('id', 'custom_name', 'tariff_name', 'server_name', 'expires_at')}
            for host in load_default_hosts(key_id)]


async def bind(account, key_id, host_id, idempotency_key):
    from bot.services.panel_sync_coordinator import panel_sync_coordinator
    from bot.services.user_locks import user_locks
    from bot.services.subscription_composition import bind_key_subscription, CORE_GROUP_PARENT_SOURCE
    inputs = {'key_id': positive_id(key_id, 'key_id'), 'host_id': positive_id(host_id, 'host_id')}
    fingerprint = operation_fingerprint(idempotency_key, inputs)
    async with panel_sync_coordinator.regular(), user_locks[account.account_id]:
        owned_key(account, key_id, mutation=True)
        owned_key(account, host_id, mutation=True)
        operation = db.begin_module_operation(account.account_id, 'key.host', idempotency_key, fingerprint,
                                              {'source': account.source, 'inputs': inputs})
        if operation['result'] is not None:
            return {'operation_id': operation['id'], **operation['result']}
        with bind_account_context(replace(account, operation_id=operation['id'])):
            result = await bind_key_subscription(host_key_id=host_id, component_key_id=key_id,
                                                source_namespace=CORE_GROUP_PARENT_SOURCE, owner_user_id=account.account_id)
            db.finish_module_operation(account.account_id, operation['id'], result)
            return {'operation_id': operation['id'], **result}
