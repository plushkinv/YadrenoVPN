"""Authenticated order actions; provider verification is the only external pay signal."""
from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary

from bot.services.payment_intents import (
    _payment_quote, cancel_payment_intent, confirm_internal_payment_settlement, load_payment_intent,
)
from core.accounts import owned_key, require_account
from core.context import bind_account_context
from core.payment_offers import available_methods, require_method
from core.results import CoreError
from database import requests as db

_order_locks = WeakValueDictionary()


def _order_lock(order_id):
    return _order_locks.setdefault(order_id, asyncio.Lock())


def owned_order(account, order_id):
    require_account(account)
    if not isinstance(order_id, str) or len(order_id) > 128:
        raise CoreError('invalid_request')
    intent = load_payment_intent(order_id)
    if intent is None or intent.user_id != account.account_id:
        raise CoreError('order_not_found')
    return intent


def order_status(account, order_id):
    """Select safe fields explicitly, including the separate access obligation."""
    intent = owned_order(account, order_id)
    provider = db.get_payment_provider_order(order_id)
    operation = db.get_account_payment_operation(account.account_id, order_id=order_id)
    key = db.get_vpn_key_by_id(intent.vpn_key_id) if intent.vpn_key_id else None
    if key and key['user_id'] != account.account_id:
        key = None
    access = None
    entitlement = db.get_key_entitlement(key['id']) if key else None
    if intent.purpose in {'key_purchase', 'key_renewal'}:
        access = ('ready' if key and key.get('server_id') and key.get('panel_email') and key.get('sub_id') and (not entitlement or entitlement['panel_applied'])
                  else 'pending' if intent.status == 'paid' or intent.provider_confirmed_at else 'not_purchased')
    return {'order_id': intent.order_id, 'operation_id': operation['id'] if operation else None,
            'purpose': intent.purpose, 'status': intent.status, 'fulfillment_status': intent.fulfillment_status,
            'provider_confirmed': bool(intent.provider_confirmed_at),
            'provider_status': provider['status'] if provider else None,
            'payment_type': intent.payment_type, 'base_currency': intent.base_currency,
            'nominal_amount_minor': intent.nominal_amount_minor, 'payable_amount_minor': intent.payable_amount_minor,
            'balance_deduct_minor': intent.balance_deduct_minor,
            'charge_amount': str(intent.charge_amount) if intent.charge_amount is not None else None,
            'charge_currency': intent.charge_currency, 'subscription_id': key['id'] if key else None,
            'access_status': access, 'payment_url': provider.get('payment_url') if provider else None}


async def _complete(account, order_id):
    from bot.services.payment_completion import complete_confirmed_payment
    from runtime.delivery import get_bot
    with bind_account_context(account):
        # Delivery/recovery remains a separate concern. HTTP always has a result to poll.
        return await complete_confirmed_payment(order_id, bot=get_bot(), notify_user=False, background=True)


async def select_method(account, order_id, payment_type):
    from runtime.readiness import require_active
    from bot.services.payment_provider_adapters import create_provider_invoice, get_payment_provider_adapter
    require_active()
    async with _order_lock(order_id):
        intent = owned_order(account, order_id)
        if intent.status == 'paid' or intent.provider_confirmed_at:
            await _complete(account, order_id)
            return order_status(account, order_id)
        if intent.status != 'pending':
            raise CoreError('order_unavailable')
        operation = db.get_account_payment_operation(account.account_id, order_id=order_id)
        prepared = (operation or {}).get('result') or {}
        if prepared.get('state') != 'prepared':
            raise CoreError('payment_preparation_pending', retryable=True)
        if payment_type != prepared['payment_type']:
            raise CoreError('quote_changed')
        require_method(account, intent, payment_type)
        if intent.purpose == 'key_renewal':
            owned_key(account, intent.vpn_key_id, mutation=True)
        if not db.get_payment_provider_order(order_id):
            with bind_account_context(account):
                price = _payment_quote(intent, prepared['quote'])
                use_balance_only = payment_type == 'balance' or (price.payable_amount_minor == 0 and prepared['balance_deduct_minor'] > 0)
                if payment_type == 'demo':
                    return {**order_status(account, order_id), 'presentation': 'placeholder'}
                if use_balance_only or price.is_free:
                    if not confirm_internal_payment_settlement(
                            order_id, payment_type='balance' if use_balance_only else 'promo_free',
                            balance_deduct_minor=prepared['balance_deduct_minor'], rate_snapshot=dict(price.rate_snapshot)):
                        raise CoreError('balance_insufficient' if payment_type == 'balance' else 'order_unavailable')
                else:
                    adapter = get_payment_provider_adapter(payment_type.removeprefix('ext_'))
                    if adapter is None:
                        raise CoreError('payment_method_unavailable')
                    try:
                        if adapter.presentation == 'telegram_invoice':
                            await _telegram_invoice(account, intent, price)
                        else:
                            await create_provider_invoice(intent, price, telegram_id=account.telegram_id, bot_username='')
                    except CoreError:
                        raise
                    except Exception:
                        raise CoreError('payment_provider_unavailable', retryable=True) from None
        intent = owned_order(account, order_id)
        provider = db.get_payment_provider_order(order_id)
        if intent.provider_confirmed_at or (provider and provider['status'] == 'succeeded'):
            await _complete(account, order_id)
        return order_status(account, order_id)


async def _telegram_invoice(account, intent, price):
    """Mini App invoices use the existing Telegram adapter and unchanged settlement."""
    if account.source != 'mini_app' or account.telegram_id is None:
        raise CoreError('payment_method_unavailable')
    # Implemented with the shared Telegram invoice builder in the Mini App adapter.
    from core.telegram_payments import create_invoice_link
    await create_invoice_link(account, intent, price)


async def check_order(account, order_id):
    from runtime.readiness import require_active
    from bot.services.payment_provider_adapters import check_provider_invoice
    require_active()
    async with _order_lock(order_id):
        intent = owned_order(account, order_id)
        confirmed = intent.provider_confirmed_at is not None or intent.status == 'paid'
        if intent.status == 'pending' and not confirmed:
            if not db.get_payment_provider_order(order_id):
                return order_status(account, order_id)
            try:
                with bind_account_context(account):
                    confirmed = await check_provider_invoice(intent) == 'succeeded'
            except Exception:
                raise CoreError('payment_provider_unavailable', retryable=True) from None
        if confirmed:
            await _complete(account, order_id)
        return order_status(account, order_id)


async def cancel_order(account, order_id):
    from runtime.readiness import require_active
    require_active()
    async with _order_lock(order_id):
        intent = owned_order(account, order_id)
        provider = db.get_payment_provider_order(order_id)
        if intent.provider_confirmed_at or intent.status == 'paid' or (provider and provider['status'] == 'succeeded'):
            raise CoreError('order_already_paid')
        if intent.status != 'canceled':
            # Existing cancellation service performs supported remote cancellation first.
            from bot.services.payment_provider_cancellation import cancel_payment_intent_safely
            result = await cancel_payment_intent_safely(order_id, user_id=account.account_id)
            if result.outcome == 'succeeded':
                await _complete(account, order_id)
                raise CoreError('order_already_paid')
            if result.outcome != 'canceled':
                raise CoreError('order_unavailable')
        return order_status(account, order_id)
