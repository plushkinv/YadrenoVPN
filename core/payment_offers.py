"""Owner-checked offers using the existing pricing and payment-intent services."""
from __future__ import annotations

import re
import time

from bot.services.payment_intents import (
    PaymentIntent, default_payment_navigation, load_payment_intent, quote_payment_intent,
)
from bot.services.user_locks import user_locks
from core.accounts import owned_key, require_account
from core.context import AccountContext, bind_account_context
from core.results import CoreError
from database import requests as db

_ACTIONS = {'key_purchase': 'key.purchase.start', 'key_renewal': 'key.renew.start',
            'balance_topup': 'balance.topup.start'}
_PAYLOAD_FIELDS = {'purpose', 'tariff_id', 'key_id', 'nominal_amount_minor', 'payment_type', 'use_balance'}


def balance_spending_enabled() -> bool:
    return db.is_referral_enabled() and db.get_referral_reward_type() == 'balance'


def _positive(value, field):
    if type(value) is not int or value <= 0:
        raise CoreError('invalid_request', details={'field': field})
    return value


def validate_purchase(account, payload):
    require_account(account)
    if not isinstance(payload, dict) or set(payload) - _PAYLOAD_FIELDS or payload.get('purpose') not in _ACTIONS:
        raise CoreError('invalid_request')
    purpose = payload['purpose']
    if not isinstance(payload.get('payment_type'), str) or len(payload['payment_type']) > 128:
        raise CoreError('invalid_request', details={'field': 'payment_type'})
    if type(payload.get('use_balance', False)) is not bool:
        raise CoreError('invalid_request', details={'field': 'use_balance'})
    if purpose == 'balance_topup':
        if payload.get('tariff_id') is not None or payload.get('key_id') is not None or payload.get('use_balance'):
            raise CoreError('invalid_request')
        nominal = _positive(payload.get('nominal_amount_minor'), 'nominal_amount_minor')
        # Reuse the existing feature gate; the stock web UI keeps this route hidden.
        if not balance_spending_enabled():
            raise CoreError('action_unavailable')
        return {'id': None, 'price_minor': nominal, 'duration_days': 0, 'name': ''}
    if payload.get('nominal_amount_minor') is not None:
        raise CoreError('invalid_request')
    tariff_id = _positive(payload.get('tariff_id'), 'tariff_id')
    key_id = payload.get('key_id')
    if purpose == 'key_renewal':
        owned_key(account, _positive(key_id, 'key_id'), mutation=True)
    elif key_id is not None:
        raise CoreError('invalid_request')
    tariff = db.get_tariff_by_id(tariff_id)
    if not tariff or not db.is_tariff_payment_target_allowed(
            user_id=account.account_id, tariff_id=tariff_id, vpn_key_id=key_id):
        raise CoreError('tariff_unavailable')
    _positive(tariff.get('price_minor'), 'tariff.price_minor')
    return tariff


async def purchase_policy(account, payload, phase):
    from bot.utils.action_policy import run_account_action_policies
    purpose = payload['purpose']
    params = ({'tariff_id': payload['tariff_id']} if purpose == 'key_purchase' else
              {'key_id': payload['key_id']} if purpose == 'key_renewal' else {})
    decision = await run_account_action_policies(_ACTIONS[purpose], params, account=account, phase=phase)
    if decision['decision'] != 'continue':
        raise CoreError('action_unavailable', details={'decision': decision['decision']})


def offer_intent(account, payload, tariff):
    return PaymentIntent(order_id='', user_id=account.account_id, purpose=payload['purpose'],
                         purpose_data={k: payload[k] for k in ('tariff_id', 'key_id') if payload.get(k) is not None},
                         base_currency=db.get_base_currency(), nominal_amount_minor=tariff['price_minor'],
                         payable_amount_minor=tariff['price_minor'], balance_deduct_minor=0, description='',
                         navigation=default_payment_navigation(payload['purpose']), status='pending',
                         fulfillment_status='pending', tariff_id=payload.get('tariff_id'), vpn_key_id=payload.get('key_id'))


def available_methods(account, intent):
    from bot.services.payment_provider_adapters import list_payment_provider_adapters
    result = list_payment_provider_adapters(intent, telegram_id=account.telegram_id)
    if account.source == 'site':
        result = [adapter for adapter in result if adapter.presentation != 'telegram_invoice']
    methods = [{'id': item.payment_type, 'presentation': item.presentation} for item in result]
    if intent.purpose != 'balance_topup' and balance_spending_enabled():
        methods.append({'id': 'balance', 'presentation': 'balance'})
    return methods


def require_method(account, intent, method):
    if method not in {item['id'] for item in available_methods(account, intent)}:
        raise CoreError('payment_method_unavailable')


def comparable_price(pricing):
    fields = ('base_currency', 'nominal_amount_minor', 'payable_amount_minor', 'final_amount',
              'charge_currency', 'discount_percent', 'pricing_policies', 'is_free', 'rate_snapshot', 'module_rewards', 'module_events')
    return {**{key: pricing.get(key) for key in fields},
            'promo': {key: (pricing.get('promo') or {}).get(key) for key in ('id', 'code', 'discount_percent')}}


def preview_provider_quote(account, payload, tariff, *, rate_snapshot=None, order_id=None):
    from bot.services.promotions import build_quote
    from bot.services.exchange_rate import provider_amount_from_base_minor
    from bot.utils.action_policy import _action_policy_context
    intent = offer_intent(account, payload, tariff)
    method = payload['payment_type']
    require_method(account, intent, method)
    # Demo is a presentation-only placeholder, as in the Telegram adapter.
    pricing_method = 'balance' if method == 'demo' else method
    with bind_account_context(account), _action_policy_context('price_preview'):
        pricing = build_quote(user_id=account.account_id, tariff=tariff, payment_type=pricing_method, order_id=order_id,
                              purpose=payload['purpose'], nominal_amount_minor=tariff['price_minor'],
                              _phase='preview', _key_id=payload.get('key_id'), rate_snapshot=rate_snapshot)
    if not pricing.get('ok'):
        raise CoreError('quote_unavailable', details={'reason': pricing.get('unavailable_code')})
    deduction = 0
    if payload.get('use_balance') or method == 'balance':
        if intent.purpose == 'balance_topup' or not balance_spending_enabled():
            raise CoreError('payment_method_unavailable')
        deduction = min(db.get_user_balance(account.account_id), pricing['payable_amount_minor'])
        if method == 'balance' and deduction < pricing['payable_amount_minor']:
            raise CoreError('balance_insufficient')
        if method != 'balance':
            pricing['payable_amount_minor'] -= deduction
            pricing['payable_amount_cents'] = pricing['payable_amount_minor']
            pricing['final_amount'], _ = provider_amount_from_base_minor(
                pricing['payable_amount_minor'], pricing_method, pricing['rate_snapshot'])
    return {'tariff': dict(tariff), 'pricing': pricing, 'balance_deduct_minor': deduction}


def public_offer(offer):
    quote, payload = offer['quote'], offer['payload']
    pricing = quote['pricing']
    return {'quote_id': offer['id'], 'expires_at': offer['expires_at'], 'version': 1,
            'purpose': payload['purpose'], 'tariff_id': payload.get('tariff_id'), 'key_id': payload.get('key_id'),
            'payment_type': payload['payment_type'], 'base_currency': pricing['base_currency'],
            'nominal_amount_minor': pricing['nominal_amount_minor'],
            'payable_amount_minor': pricing['payable_amount_minor'], 'charge_minor': pricing['final_amount'],
            'charge_currency': pricing['charge_currency'], 'balance_deduct_minor': quote['balance_deduct_minor'],
            'duration_days': quote['tariff'].get('duration_days'),
            'traffic_limit_gb': quote['tariff'].get('traffic_limit_gb'), 'max_ips': quote['tariff'].get('max_ips')}


async def create_offer(account: AccountContext, payload: dict) -> dict:
    from runtime.readiness import require_active
    require_active()
    tariff = validate_purchase(account, payload)
    await purchase_policy(account, payload, 'preview')
    quote = preview_provider_quote(account, payload, tariff)
    return public_offer(db.save_payment_offer(account.account_id, payload, quote))


async def create_order(account: AccountContext, quote_id: str, idempotency_key: str) -> str:
    from runtime.readiness import require_active
    from bot.services.payment_intents import payment_invoice_description
    require_active()
    require_account(account)
    if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency_key):
        raise CoreError('invalid_request', details={'field': 'idempotency_key'})
    if not isinstance(quote_id, str) or len(quote_id) > 128:
        raise CoreError('invalid_request', details={'field': 'quote_id'})
    async with user_locks[account.account_id]:
        previous = db.get_account_payment_operation(account.account_id, idempotency_key=idempotency_key)
        if previous and previous['fingerprint'] != quote_id:
            raise CoreError('idempotency_conflict')
        offer = db.get_payment_offer(quote_id, account.account_id)
        if not offer:
            raise CoreError('quote_not_found')
        operation = previous or (db.get_account_payment_operation(account.account_id, order_id=offer['order_id'])
                                 if offer['order_id'] else None)
        if operation and operation.get('result'):
            db.remember_payment_operation_alias(account.account_id, operation['order_id'], quote_id, idempotency_key)
            _raise_failed(operation)
            return operation['order_id']
        if offer['expires_at'] <= time.time():
            db.expire_unprepared_payment_offers()
            raise CoreError('quote_expired')
        payload, quoted = offer['payload'], offer['quote']
        tariff = validate_purchase(account, payload)
        await purchase_policy(account, payload, 'execute')
        fresh = preview_provider_quote(account, payload, tariff, rate_snapshot=quoted['pricing']['rate_snapshot'],
                                       order_id=operation['order_id'] if operation else None)
        if tariff != quoted['tariff'] or comparable_price(fresh['pricing']) != comparable_price(quoted['pricing']) or fresh['balance_deduct_minor'] != quoted['balance_deduct_minor']:
            raise CoreError('quote_changed')
        description = payment_invoice_description(payload['purpose'], tariff=tariff, key_id=payload.get('key_id'),
                                                  nominal=tariff['price_minor'], base_currency=quoted['pricing']['base_currency'])
        navigation = default_payment_navigation(payload['purpose'])
        with bind_account_context(account):
            operation = db.begin_offered_order(account.account_id, quote_id, idempotency_key, description=description,
                                               success_target=navigation.success_target.as_dict(),
                                               cancel_target=navigation.cancel_target.as_dict())
            if not operation.get('result'):
                _prepare_order(account, operation, offer)
        operation = db.get_account_payment_operation(account.account_id, order_id=operation['order_id'])
        _raise_failed(operation)
        return operation['order_id']


def _raise_failed(operation):
    result = operation.get('result') or {}
    if result.get('error'):
        raise CoreError(result['error'], operation_id=operation['id'])


def _prepare_order(account, operation, offer):
    """Finish a recoverable preparation before exposing a payable provider order."""
    order_id, quoted, payload = operation['order_id'], offer['quote'], offer['payload']
    method = 'balance' if payload['payment_type'] == 'demo' else payload['payment_type']
    deduction = quoted['balance_deduct_minor'] if method != 'balance' else 0
    if deduction:
        db.save_payment_balance_deduction(order_id, deduction)
    try:
        from bot.services.money import minor_to_decimal
        pricing = quoted['pricing']
        db.update_payment_intent_quote(order_id, payment_type=method,
                                       payable_amount_minor=pricing['payable_amount_minor'],
                                       charge_amount=str(minor_to_decimal(pricing['final_amount'], pricing['charge_currency'])),
                                       charge_currency=pricing['charge_currency'], rate_snapshot=pricing['rate_snapshot'])
        price = quote_payment_intent(order_id, method)
        if price.unavailable_reason or comparable_price(price.raw) != comparable_price(quoted['pricing']):
            db.cancel_unconfirmed_payment_for_method_change(order_id, user_id=account.account_id)
            db.finish_account_payment_operation(account.account_id, order_id, {'error': 'quote_changed'})
            return
        db.finish_account_payment_operation(account.account_id, order_id,
                                            {'state': 'prepared', 'quote': dict(price.raw),
                                             'balance_deduct_minor': quoted['balance_deduct_minor'],
                                             'payment_type': payload['payment_type']})
    except Exception:
        # An interrupted preparation keeps its durable id; no invoice exists yet.
        raise CoreError('payment_preparation_pending', retryable=True, operation_id=operation['id']) from None
