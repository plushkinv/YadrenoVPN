"""Build the bounded current-user snapshot exposed to local extensions."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping


CURRENT_USER_CONTRACT_VERSION = 1


def build_extension_user_snapshot(telegram_id: int) -> dict[str, Any] | None:
    """Return a safe, read-only snapshot for one Telegram user."""
    from database.requests import get_primary_trial_eligibility, get_user_snapshot_profile

    user = get_user_snapshot_profile(int(telegram_id))
    if not user:
        return None
    return _build_snapshot(user, get_primary_trial_eligibility(int(user['telegram_id'])))


def build_extension_account_snapshot(account_id: int) -> dict[str, Any] | None:
    """Keep v1 for linked users; v2 explicitly permits absent Telegram identity."""
    from database.requests import get_account_snapshot_profile, get_account_trial_eligibility

    user = get_account_snapshot_profile(int(account_id))
    if not user:
        return None
    return _build_snapshot(user, get_account_trial_eligibility(int(account_id)))


def _build_snapshot(user: Mapping[str, Any], trial: Mapping[str, Any]) -> dict[str, Any]:
    from database.requests import (
        get_base_currency,
        get_user_active_promo_snapshot,
        get_user_key_snapshot_stats,
        get_user_payment_snapshot_stats,
        get_user_referral_snapshot_stats,
    )

    user_id = int(user['id'])
    resolved_telegram_id = int(user['telegram_id']) if user['telegram_id'] is not None else None
    key_stats = get_user_key_snapshot_stats(user_id)
    payment_stats = get_user_payment_snapshot_stats(user_id)
    referral_stats = get_user_referral_snapshot_stats(user_id)
    promo = get_user_active_promo_snapshot(user_id)

    identity = {
        'user_id': user_id,
        'telegram_id': resolved_telegram_id,
        'first_name': _optional_plain_text(user.get('first_name')),
        'last_name': _optional_plain_text(user.get('last_name')),
        'username': _optional_plain_text(user.get('username')),
        'display_name': _display_name(user),
        'registered_at': _iso_utc(user.get('created_at')),
    }
    keys = {
        'total_count': int(key_stats.get('total_count') or 0),
        'active_count': int(key_stats.get('active_count') or 0),
        'expired_count': int(key_stats.get('expired_count') or 0),
        'draft_count': int(key_stats.get('draft_count') or 0),
        'traffic_exhausted_count': int(
            key_stats.get('traffic_exhausted_count') or 0
        ),
    }
    keys.update({
        'has_keys': keys['total_count'] > 0,
        'has_active_key': keys['active_count'] > 0,
        'has_expired_key': keys['expired_count'] > 0,
        'has_draft_key': keys['draft_count'] > 0,
        'has_traffic_exhausted_key': keys['traffic_exhausted_count'] > 0,
    })

    return {
        'contract_version': CURRENT_USER_CONTRACT_VERSION if resolved_telegram_id is not None else 2,
        'identity': identity,
        'account': {
            'is_banned': bool(user.get('is_banned')),
            'is_bot_blocked': bool(user.get('is_bot_blocked')),
            'balance_minor': int(user.get('personal_balance') or 0),
            'balance_currency': str(get_base_currency()),
        },
        'trial': {
            'has_used_trial': bool(user.get('used_trial')),
            'primary_trial_eligible': bool(trial.get('eligible')),
            'primary_trial_ineligibility_reason': (
                None
                if trial.get('eligible')
                else _optional_plain_text(trial.get('reason'))
            ),
            'usage_scope': str(trial.get('scope') or 'once_per_user'),
        },
        'keys': keys,
        'payments': {
            'successful_count': int(payment_stats.get('successful_count') or 0),
            'paid_key_count': int(payment_stats.get('paid_key_count') or 0),
            'has_ever_paid_key': bool(payment_stats.get('has_ever_paid_key')),
            'last_payment_at': _iso_utc(payment_stats.get('last_payment_at')),
            'amounts_by_currency': _minor_amounts(
                payment_stats.get('amounts_by_currency')
            ),
        },
        'referral': {
            'code': _optional_plain_text(user.get('referral_code')),
            'has_referrer': user.get('referred_by') is not None,
            'referrer_user_id': (
                int(user['referred_by'])
                if user.get('referred_by') is not None
                else None
            ),
            'coefficient': float(
                user['referral_coefficient']
                if user.get('referral_coefficient') is not None
                else 1.0
            ),
            'direct_count': int(referral_stats.get('direct_count') or 0),
            'total_count': int(referral_stats.get('total_count') or 0),
            'paying_count': int(referral_stats.get('paying_count') or 0),
            'reward_amounts_by_currency': _minor_amounts(
                referral_stats.get('reward_amounts_by_currency')
            ),
            'reward_days': int(referral_stats.get('reward_days') or 0),
        },
        'promo': _promo_snapshot(promo),
    }


def _optional_plain_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _display_name(user: Mapping[str, Any]) -> str:
    full_name = ' '.join(
        value
        for value in (
            _optional_plain_text(user.get('first_name')),
            _optional_plain_text(user.get('last_name')),
        )
        if value
    )
    if full_name:
        return full_name
    username = _optional_plain_text(user.get('username'))
    if username:
        return username if username.startswith('@') else f'@{username}'
    identity = user['telegram_id'] if user['telegram_id'] is not None else user['id']
    return f"ID {int(identity)}"


def _iso_utc(value: Any) -> str | None:
    if value is None or value == '':
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, time.min)
        else:
            parsed = datetime.fromisoformat(
                str(value).strip().replace('Z', '+00:00')
            )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec='seconds')
        .replace('+00:00', 'Z')
    )


def _minor_amounts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(currency).strip().upper(): int(amount or 0)
        for currency, amount in sorted(
            value.items(),
            key=lambda item: str(item[0]),
        )
        if str(currency).strip()
    }


def _promo_snapshot(promo: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not promo:
        return None
    return {
        'type': str(promo.get('type') or ''),
        'code': str(promo.get('code') or ''),
        'discount_percent': int(promo.get('discount_percent') or 0),
        'expires_at': _iso_utc(promo.get('expires_at')),
    }


__all__ = ['CURRENT_USER_CONTRACT_VERSION', 'build_extension_user_snapshot']
