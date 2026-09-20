"""Own-account profile, existing histories, and native referral statistics."""
from urllib.parse import quote

from core.accounts import require_account
from core.subscriptions import pagination
from database import requests as db


def profile(account):
    user = require_account(account)
    credentials = db.get_account_credentials(account.account_id)
    return {'account_id': user['id'], 'telegram_id': user['telegram_id'], 'username': user.get('username'),
            'first_name': user.get('first_name'), 'last_name': user.get('last_name'), 'created_at': user.get('created_at'),
            'credentials': {'present': credentials is not None, 'phone': credentials['phone'] if credentials else None,
                            'phone_verified': bool(credentials and credentials['phone_verified'])}}


def balance(account, *, limit=50, offset=0):
    from core.payment_offers import balance_spending_enabled
    require_account(account)
    paging = pagination(limit, offset)
    return {'amount_minor': db.get_user_balance(account.account_id), 'currency': db.get_base_currency(),
            'spending_enabled': balance_spending_enabled(),
            'history': db.get_account_balance_history(account.account_id, **paging), **paging}


def payments(account, *, limit=50, offset=0):
    require_account(account)
    paging = pagination(limit, offset)
    return {'items': db.get_account_payment_history(account.account_id, **paging), **paging}


def referrals(account):
    from core.content import safe_html
    user = require_account(account)
    if not db.is_referral_enabled():
        return {'enabled': False}
    code = user.get('referral_code') or db.ensure_user_referral_code(account.account_id)
    origin = str(db.get_setting('web_public_origin', '') or '').rstrip('/')
    username = db.get_setting('web_bot_username', '')
    from bot.utils.telegram_links import build_telegram_link
    return {'enabled': True, 'code': code, 'reward_type': db.get_referral_reward_type(),
            'site_url': origin + '/?ref=' + quote(code, safe='') if origin else None,
            'telegram_url': build_telegram_link(username, 'ref_' + code) if username else None,
            'levels': db.get_referral_levels(), 'statistics': db.get_referral_stats(account.account_id),
            'coefficient': db.get_user_referral_coefficient(account.account_id),
            'conditions_html': safe_html(db.get_referral_conditions_text() or '')}


def attribute_referral(account, code):
    from runtime.readiness import require_active
    from core.results import CoreError
    from bot.services.referral_attribution import attribute_start_referral
    require_active()
    user = require_account(account)
    if not isinstance(code, str) or not 1 <= len(code) <= 128 or not code.isascii() or not code.isalnum():
        raise CoreError('invalid_request', details={'field': 'code'})
    if attribute_start_referral(user, is_new=False, args='ref_' + code):
        from runtime.delivery import notify_new_referral
        notify_new_referral(user['id'])
    return {'attributed': db.get_user_referrer(account.account_id) is not None}
