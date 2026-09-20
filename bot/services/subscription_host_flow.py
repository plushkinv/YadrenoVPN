"""The stock zero/one/many host decision without Telegram presentation."""
from collections.abc import Mapping


def host_id(host):
    value = host.get('id', host.get('key_id'))
    if value is None or isinstance(value, bool):
        return None
    try:
        value = int(value)
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def load_default_hosts(component_key_id):
    from bot.services.subscription_composition import get_default_subscription_hosts
    values = get_default_subscription_hosts(component_key_id=int(component_key_id))
    if values is None:
        return []
    if isinstance(values, (str, bytes, Mapping)):
        raise TypeError('subscription host candidates must be a list')
    return [dict(value) for value in values if isinstance(value, Mapping) and host_id(value) is not None]


async def bind_default_host(*, host_key_id, component_key_id):
    from bot.services.subscription_composition import bind_key_subscription, CORE_GROUP_PARENT_SOURCE
    result = await bind_key_subscription(host_key_id=int(host_key_id), component_key_id=int(component_key_id),
                                         source_namespace=CORE_GROUP_PARENT_SOURCE)
    return dict(result) if isinstance(result, Mapping) else {'ok': False, 'status': 'invalid_result'}


async def resolve_default_host(component_key_id, *, _load=None, _bind=None):
    """Return the same decision to every channel, retaining candidates for its UI."""
    try:
        hosts = (_load or load_default_hosts)(component_key_id)
    except Exception:
        return {'ok': False, 'status': 'host_lookup_failed'}, []
    if not hosts:
        return {'ok': True, 'status': 'standalone'}, []
    if len(hosts) > 1:
        return {'ok': True, 'status': 'selection_required', 'host_count': len(hosts)}, hosts
    selected = host_id(hosts[0])
    if selected is None:
        return {'ok': False, 'status': 'invalid_host'}, hosts
    try:
        result = await (_bind or bind_default_host)(host_key_id=selected, component_key_id=component_key_id)
    except Exception:
        result = {'ok': False, 'status': 'bind_failed'}
    if result.get('error_code') == 'component_already_bound':
        result = {'ok': True, 'status': 'already_bound', 'applied': False, 'already_applied': True}
    return result, hosts
