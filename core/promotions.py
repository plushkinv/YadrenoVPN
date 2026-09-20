"""Use native promotion eligibility and reservations for authenticated accounts."""
from core.accounts import require_account
from core.operations import operation_fingerprint
from core.results import CoreError
from database import requests as db


def _code(value):
    if not isinstance(value, str) or len(value) > 128 or not db.is_base62_code(value.strip()):
        raise CoreError('promo_invalid')
    return value.strip()


def public_result(result):
    value = {key: result[key] for key in ('ok', 'reason', 'operation_id') if key in result}
    if result.get('promo'):
        value['promo'] = {key: result['promo'].get(key) for key in ('code', 'type', 'discount_percent', 'expires_at')}
    return value


def check(account, code):
    require_account(account)
    return public_result(db.get_promo_code_availability(_code(code), user_id=account.account_id, block_user_reservations=True))


def mutate(account, action, inputs, idempotency_key):
    from runtime.readiness import require_active
    require_active()
    require_account(account)
    if action not in ('activate', 'clear') or not isinstance(inputs, dict) or set(inputs) != ({'code'} if action == 'activate' else set()):
        raise CoreError('invalid_request')
    code = _code(inputs['code']) if action == 'activate' else None
    fingerprint = operation_fingerprint(idempotency_key, {'code': code})
    return public_result(db.apply_account_promotion_once(account.account_id, action, code, idempotency_key, fingerprint, account.source))
