"""Shared internal-account checks for authenticated service adapters."""
from core.context import AccountContext
from core.results import CoreError
from database import requests as db


def require_account(account: AccountContext, *, _settlement: bool = False) -> dict:
    if not isinstance(account, AccountContext):
        raise TypeError('trusted AccountContext is required')
    user = db.get_user_by_id(account.account_id)
    if not user or (user.get('is_banned') and not _settlement):
        raise CoreError('access_denied')
    if account.telegram_id is not None and user.get('telegram_id') != account.telegram_id:
        raise CoreError('authentication_required')
    return user


def owned_key(account: AccountContext, key_id: int, *, mutation: bool = False, _settlement: bool = False) -> dict:
    require_account(account, _settlement=_settlement)
    key = db.get_vpn_key_by_id(key_id)
    if not key or key['user_id'] != account.account_id:
        raise CoreError('subscription_not_found')
    if mutation:
        db.assert_key_mutation_ready(key_id)
    if mutation and key.get('server_id') and key.get('panel_email'):
        server = db.get_server_by_id(key['server_id'])
        if server is None:
            raise CoreError('panel_unavailable', retryable=True)
        db.assert_panel_identity_ready(server, key['panel_email'])
    return key
