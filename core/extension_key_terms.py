"""Validated access terms available only to trusted extension commands."""
from core.results import CoreError
from database import requests as db


def require_lifecycle_lock_available(account):
    """A hook inside a key mutation must not await its caller's user lock."""
    from bot.utils.custom_extensions import _get_current_extension_invocation_kind
    from bot.services.user_locks import user_locks
    if (_get_current_extension_invocation_kind() == 'lifecycle_hook'
            and user_locks[account.account_id].locked()):
        raise CoreError('key_operation_pending', retryable=True)


def integer(value, field, *, minimum=0, maximum=99999):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CoreError('invalid_request', details={'field': field})
    return value


def normalize_terms(tariff_id=None, custom_terms=None, *, paid=False):
    """Keep the caller's intent separate from the resolved catalog snapshot."""
    if (tariff_id is None) == (custom_terms is None):
        raise CoreError('invalid_request', details={'field': 'tariff_id/custom_terms'})
    if tariff_id is not None:
        return {'tariff_id': integer(tariff_id, 'tariff_id', minimum=1, maximum=2**63 - 1)}
    fields = {'group_id', 'duration_days', 'traffic_limit_gb', 'max_ips'}
    if paid:
        fields.add('price_minor')
    if not isinstance(custom_terms, dict) or set(custom_terms) != fields:
        raise CoreError('invalid_request', details={'field': 'custom_terms'})
    terms = {name: integer(custom_terms[name], name,
                          minimum=1 if name in {'group_id', 'price_minor'} else 0,
                          maximum=2**63 - 1 if name in {'group_id', 'price_minor'} else
                          999 if name == 'max_ips' else 99999)
             for name in fields}
    return {'custom_terms': terms}


def resolve_terms(selection):
    return db.get_extension_key_tariff(selection)


def select_server(tariff, server_id=None):
    """Resolve automatic provisioning before any key or financial effect exists."""
    servers = db.get_active_servers_by_group(tariff['group_id'])
    if server_id is not None:
        integer(server_id, 'server_id', minimum=1, maximum=2**63 - 1)
        if not any(server['id'] == server_id for server in servers):
            raise CoreError('server_unavailable')
        return server_id
    if len(servers) == 1:
        return servers[0]['id']
    raise CoreError('server_selection_required' if servers else 'server_unavailable')
