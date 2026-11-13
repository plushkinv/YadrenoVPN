"""Hosted built-in adapters for the common payment-intent lifecycle."""
from importlib import import_module
import hashlib
import json
import time
import uuid

from core.payment_provider_specs import INTERNATIONAL_PROVIDERS
from database.requests import get_gateway_settings, is_gateway_configured, get_payment_provider_order, save_payment_provider_order
from .common import ExpectedPayment


def _adapter(provider_id):
    if provider_id not in INTERNATIONAL_PROVIDERS:
        raise ValueError('Unknown hosted provider')
    return import_module(f'{__name__}.{provider_id}')


def creation_is_idempotent(provider_id, settings):
    return provider_id in {'stripe', 'paypal'} or (
        provider_id == 'myfatoorah' and settings['region'] in {'KW', 'SA'}
    )


async def create_invoice(provider_id, intent, quote, *, bot_username):
    from bot.services.billing import build_payment_return_url
    if not is_gateway_configured(provider_id):
        raise ValueError('Payment provider is disabled or not configured')
    settings = get_gateway_settings(provider_id)
    fingerprint = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    binding = get_payment_provider_order(intent.order_id)
    if binding:
        metadata = dict(binding.get('metadata') or {})
        # Provider idempotency windows: Stripe >=24h, PayPal 6h, MyFatoorah 250min.
        window = {'stripe': 23 * 3600, 'paypal': 5 * 3600, 'myfatoorah': 240 * 60}.get(provider_id, 0)
        if (not creation_is_idempotent(provider_id, settings) or not metadata.get('creation_key')
                or metadata.get('creation_account') != fingerprint
                or not 0 <= time.time() - float(metadata.get('creation_started_at', 0)) < window):
            raise ValueError('Provider creation outcome is unknown; creation cannot be repeated safely')
    else:
        metadata = {'creation_key': str(uuid.uuid4()), 'creation_started_at': time.time(),
                    'creation_account': fingerprint,
                    'creation_return_url': ('' if provider_id == 'paytabs' else
                        build_payment_return_url(bot_username, provider_id, intent.order_id))}
        if not save_payment_provider_order(order_id=intent.order_id, provider_id=provider_id,
            payment_type=quote.payment_type, status='pending', metadata=metadata, purpose=intent.purpose,
            charge_amount=format(quote.charge_amount.normalize(), 'f'), charge_currency=quote.charge_currency):
            raise RuntimeError('Provider creation attempt could not be saved')
    expected = ExpectedPayment(intent.order_id, '', quote.charge_amount, quote.charge_currency, metadata['creation_key'])
    result = await _adapter(provider_id).create(expected, settings, intent.description, metadata['creation_return_url'])
    result['metadata'] = {**metadata, **result.get('metadata', {})}
    return result


async def check_invoice(provider_id, intent, binding):
    expected = ExpectedPayment.from_binding(intent, binding)
    # Region/profile are invoice terms. Current settings only supply authentication.
    settings = get_gateway_settings(provider_id)
    settings.update({key: value for key, value in (binding.get('metadata') or {}).items()
                     if key in {'region', 'environment', 'profile_id'}})
    return await _adapter(provider_id).check(expected, settings, binding.get('metadata') or {})
