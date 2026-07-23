"""Internal provider adapters for the common payment-intent lifecycle."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from database.requests import (
    find_order_by_order_id,
    get_payment_provider_order,
    get_setting,
    is_cardlink_configured,
    is_cards_configured,
    is_crypto_configured,
    is_demo_payment_enabled,
    is_platega_configured,
    is_stars_enabled,
    is_wata_configured,
    is_yookassa_qr_configured,
    save_payment_provider_order,
    schedule_payment_auto_check,
    update_payment_provider_order_status,
)

from bot.services.payment_intents import PaymentIntent, PaymentQuote


@dataclass(frozen=True)
class PaymentProviderAdapter:
    provider_id: str
    payment_type: str
    title: str
    label: str
    presentation: str
    supported_purposes: frozenset[str]
    custom: bool = False


@dataclass(frozen=True)
class ProviderInvoice:
    order_id: str
    provider_id: str
    payment_type: str
    presentation: str
    status: str = 'pending'
    payment_url: str | None = None
    provider_payment_id: str | None = None
    qr_image_data: bytes | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


_ALL_PURPOSES = frozenset({'key_purchase', 'key_renewal', 'balance_topup'})
_BUILTIN_ADAPTERS: Mapping[str, PaymentProviderAdapter] = MappingProxyType({
    'crypto': PaymentProviderAdapter('crypto', 'crypto', 'USDT', '🪙 USDT', 'link', _ALL_PURPOSES),
    'stars': PaymentProviderAdapter('stars', 'stars', 'Telegram Stars', '⭐ Telegram Stars', 'telegram_invoice', _ALL_PURPOSES),
    'cards': PaymentProviderAdapter('cards', 'cards', 'TG Payments', '💳 TG Payments', 'telegram_invoice', _ALL_PURPOSES),
    'yookassa_qr': PaymentProviderAdapter('yookassa_qr', 'yookassa_qr', 'ЮКасса', '📱 ЮКасса', 'link', _ALL_PURPOSES),
    'wata': PaymentProviderAdapter('wata', 'wata', 'WATA', '🌊 WATA', 'link', _ALL_PURPOSES),
    'platega': PaymentProviderAdapter('platega', 'platega', 'Platega', '💸 Platega', 'link', _ALL_PURPOSES),
    'cardlink': PaymentProviderAdapter('cardlink', 'cardlink', 'Cardlink', '🔗 Cardlink', 'link', _ALL_PURPOSES),
    'demo': PaymentProviderAdapter(
        'demo',
        'demo',
        'Демо оплата',
        '🧪 Демо оплата',
        'placeholder',
        frozenset({'key_purchase', 'key_renewal'}),
    ),
})


def list_payment_provider_adapters(
    intent: PaymentIntent,
    *,
    telegram_id: int,
) -> list[PaymentProviderAdapter]:
    """Returns configured built-ins and purpose-opted-in extension providers."""
    availability = {
        'crypto': is_crypto_configured(),
        'stars': is_stars_enabled(),
        'cards': is_cards_configured(),
        'yookassa_qr': is_yookassa_qr_configured(),
        'wata': is_wata_configured(),
        'platega': is_platega_configured(),
        'cardlink': is_cardlink_configured(),
        'demo': is_demo_payment_enabled(),
    }
    result = [
        adapter
        for provider_id, adapter in _BUILTIN_ADAPTERS.items()
        if availability.get(provider_id) and intent.purpose in adapter.supported_purposes
    ]

    from bot.utils.payment_provider_registry import (
        is_payment_provider_enabled,
        list_payment_providers,
    )

    context = {
        'user_id': intent.user_id,
        'telegram_id': telegram_id,
        'purpose': intent.purpose,
        'base_currency': intent.base_currency,
        'nominal_amount_minor': intent.nominal_amount_minor,
        'nominal_amount_cents': intent.nominal_amount_cents,
    }
    for provider in list_payment_providers():
        if intent.purpose not in provider.supported_purposes:
            continue
        if not is_payment_provider_enabled(provider.provider_id, context):
            continue
        result.append(PaymentProviderAdapter(
            provider_id=provider.provider_id,
            payment_type=provider.payment_type,
            title=provider.title,
            label=provider.label,
            presentation='link',
            supported_purposes=provider.supported_purposes,
            custom=True,
        ))
    from bot.services.money import payment_type_currency
    from database.requests import get_currency_rate

    available: list[PaymentProviderAdapter] = []
    for adapter in result:
        charge_currency = payment_type_currency(
            adapter.payment_type,
            base_currency=intent.base_currency,
        )
        if charge_currency == intent.base_currency or get_currency_rate(
            charge_currency,
            base_currency=intent.base_currency,
        ) is not None:
            available.append(adapter)
    return available


def get_payment_provider_adapter(provider_id: str) -> PaymentProviderAdapter | None:
    """Resolves a built-in or extension adapter without exposing fulfillment."""
    normalized = str(provider_id or '').strip().casefold()
    builtin = _BUILTIN_ADAPTERS.get(normalized)
    if builtin:
        return builtin
    try:
        from bot.utils.payment_provider_registry import get_payment_provider

        provider = get_payment_provider(normalized)
    except ValueError:
        provider = None
    if not provider:
        return None
    return PaymentProviderAdapter(
        provider_id=provider.provider_id,
        payment_type=provider.payment_type,
        title=provider.title,
        label=provider.label,
        presentation='link',
        supported_purposes=provider.supported_purposes,
        custom=True,
    )


async def create_provider_invoice(
    intent: PaymentIntent,
    quote: PaymentQuote,
    *,
    telegram_id: int,
    bot_username: str,
) -> ProviderInvoice:
    """Creates one provider order after purpose and quote validation."""
    adapter = get_payment_provider_adapter(quote.payment_type.removeprefix('ext_'))
    if adapter is None or adapter.payment_type != quote.payment_type:
        adapter = get_payment_provider_adapter(quote.payment_type)
    if adapter is None:
        raise ValueError('Payment provider is not registered')
    if intent.purpose not in adapter.supported_purposes:
        raise ValueError('Payment provider does not support this purpose')
    if adapter.presentation == 'placeholder':
        raise ValueError('Placeholder provider does not create invoices')

    if adapter.custom:
        result = await _create_custom_invoice(
            adapter,
            intent,
            quote,
            telegram_id=telegram_id,
            bot_username=bot_username,
        )
    elif adapter.presentation == 'telegram_invoice':
        result = {
            'provider_payment_id': intent.order_id,
            'payment_url': None,
            'status': 'pending',
            'metadata': {},
        }
    else:
        result = await _create_builtin_link_invoice(
            adapter,
            intent,
            quote,
            telegram_id=telegram_id,
            bot_username=bot_username,
        )

    external_id = str(result.get('provider_payment_id') or '') or None
    payment_url = str(result.get('payment_url') or '') or None
    metadata = dict(result.get('metadata') or {})
    if result.get('qr_image_data'):
        metadata['has_runtime_qr'] = True
    provider_status = _normalize_status(result.get('status'))
    if not save_payment_provider_order(
        order_id=intent.order_id,
        provider_id=adapter.provider_id,
        payment_type=adapter.payment_type,
        provider_payment_id=external_id,
        payment_url=payment_url,
        status=provider_status,
        metadata=metadata,
        purpose=intent.purpose,
        charge_amount=_decimal_text(quote.charge_amount),
        charge_currency=quote.charge_currency,
    ):
        raise RuntimeError('Provider order could not be saved')
    if provider_status == 'succeeded':
        update_payment_provider_order_status(intent.order_id, 'succeeded')

    if adapter.presentation == 'link' and adapter.provider_id != 'crypto':
        delay = 120
        if adapter.custom:
            from bot.utils.payment_provider_registry import get_payment_provider

            custom_provider = get_payment_provider(adapter.provider_id)
            if custom_provider and custom_provider.auto_check_interval_seconds:
                delay = max(120, min(1800, int(custom_provider.auto_check_interval_seconds)))
            elif custom_provider:
                delay = 0
        if delay:
            schedule_payment_auto_check(intent.order_id, adapter.provider_id, first_delay_seconds=delay)

    return ProviderInvoice(
        order_id=intent.order_id,
        provider_id=adapter.provider_id,
        payment_type=adapter.payment_type,
        presentation=adapter.presentation,
        status=provider_status,
        payment_url=payment_url,
        provider_payment_id=external_id,
        qr_image_data=result.get('qr_image_data'),
        metadata=MappingProxyType(metadata),
    )


async def check_provider_invoice(intent: PaymentIntent) -> str:
    """Checks one persisted provider order through its registered adapter."""
    provider_order = get_payment_provider_order(intent.order_id)
    if not provider_order:
        raise ValueError('Provider order does not exist')
    provider_id = str(provider_order.get('provider_id') or '')
    adapter = get_payment_provider_adapter(provider_id)
    if not adapter or intent.purpose not in adapter.supported_purposes:
        raise ValueError('Provider is not allowed for this payment purpose')
    external_id = str(provider_order.get('provider_payment_id') or '')

    if adapter.custom:
        from bot.services.custom_payments import check_custom_payment_order

        order = find_order_by_order_id(intent.order_id) or {}
        order.update({
            'order_id': intent.order_id,
            'payment_type': intent.payment_type,
            'purpose': intent.purpose,
            'purpose_data': dict(intent.purpose_data),
            'base_currency': intent.base_currency,
            'nominal_amount_minor': intent.nominal_amount_minor,
            'payable_amount_minor': intent.payable_amount_minor,
            'nominal_amount_cents': intent.nominal_amount_cents,
            'payable_amount_cents': intent.payable_amount_cents,
            'charge_amount': _decimal_text(intent.charge_amount or Decimal('0')),
            'charge_currency': intent.charge_currency,
            'description': intent.description,
            'rate_snapshot': dict(intent.rate_snapshot),
        })
        result = await check_custom_payment_order(
            provider_id,
            order,
        )
        status = _normalize_status(result.get('status'))
    else:
        status = await _check_builtin_status(adapter, external_id, intent.order_id)
    update_payment_provider_order_status(intent.order_id, status)
    return status


async def _create_builtin_link_invoice(
    adapter: PaymentProviderAdapter,
    intent: PaymentIntent,
    quote: PaymentQuote,
    *,
    telegram_id: int,
    bot_username: str,
) -> dict[str, Any]:
    from bot.services.billing import (
        build_crypto_payment_url,
        create_cardlink_payment,
        create_platega_payment,
        create_wata_payment,
        create_yookassa_qr_payment,
        extract_item_id_from_url,
    )

    amount_rub = quote.charge_amount
    common = {
        'amount_rub': amount_rub,
        'order_id': intent.order_id,
        'description': intent.description,
        'bot_name': bot_username,
    }
    if adapter.provider_id == 'crypto':
        item_id = extract_item_id_from_url(get_setting('crypto_item_url', ''))
        if not item_id:
            raise ValueError('Crypto payment item is not configured')
        return {
            'provider_payment_id': intent.order_id,
            'payment_url': build_crypto_payment_url(
                item_id=item_id,
                invoice_id=intent.order_id,
                price_cents=int(quote.raw.get('final_amount') or 0),
            ),
            'status': 'pending',
            'metadata': {'push_confirmation': True},
        }
    if adapter.provider_id == 'yookassa_qr':
        raw = await create_yookassa_qr_payment(**common)
        return _link_result(raw, 'yookassa_payment_id')
    if adapter.provider_id == 'wata':
        raw = await create_wata_payment(**common)
        return _link_result(raw, 'wata_link_id')
    if adapter.provider_id == 'platega':
        raw = await create_platega_payment(**common, user_telegram_id=telegram_id)
        return _link_result(raw, 'platega_transaction_id')
    if adapter.provider_id == 'cardlink':
        raw = await create_cardlink_payment(**common)
        return _link_result(raw, 'cardlink_bill_id')
    raise ValueError('Unsupported built-in link provider')


async def _create_custom_invoice(
    adapter: PaymentProviderAdapter,
    intent: PaymentIntent,
    quote: PaymentQuote,
    *,
    telegram_id: int,
    bot_username: str,
) -> dict[str, Any]:
    from bot.utils.payment_provider_registry import create_payment

    return await create_payment(adapter.provider_id, {
        'provider_id': adapter.provider_id,
        'payment_type': adapter.payment_type,
        'order_id': intent.order_id,
        'user_id': intent.user_id,
        'telegram_id': telegram_id,
        'purpose': intent.purpose,
        'purpose_data': dict(intent.purpose_data),
        'base_currency': intent.base_currency,
        'nominal_amount_minor': intent.nominal_amount_minor,
        'payable_amount_minor': intent.payable_amount_minor,
        'nominal_amount_cents': intent.nominal_amount_cents,
        'payable_amount_cents': intent.payable_amount_cents,
        'amount_cents': int(quote.raw.get('final_amount') or 0),
        'charge_amount': _decimal_text(quote.charge_amount),
        'charge_currency': quote.charge_currency,
        'currency': quote.charge_currency,
        'rate_snapshot': dict(quote.rate_snapshot),
        'description': intent.description,
        'quote': dict(quote.raw),
        'bot_username': bot_username,
    })


async def _check_builtin_status(
    adapter: PaymentProviderAdapter,
    external_id: str,
    order_id: str,
) -> str:
    from bot.services.billing import (
        check_cardlink_payment_status,
        check_platega_payment_status,
        check_wata_payment_status,
        check_yookassa_payment_status,
    )

    if adapter.provider_id == 'crypto':
        return 'pending'
    if adapter.provider_id in {'stars', 'cards'}:
        return 'pending'
    if not external_id:
        raise ValueError('Provider payment id is missing')
    checkers = {
        'yookassa_qr': check_yookassa_payment_status,
        'wata': check_wata_payment_status,
        'platega': check_platega_payment_status,
        'cardlink': check_cardlink_payment_status,
    }
    checker = checkers.get(adapter.provider_id)
    if checker is None:
        raise ValueError('Provider status checker is missing')
    return _normalize_status(await checker(external_id, order_id=order_id))


def _link_result(raw: Mapping[str, Any], external_key: str) -> dict[str, Any]:
    return {
        'provider_payment_id': str(raw[external_key]),
        'payment_url': str(raw.get('qr_url') or ''),
        'qr_image_data': raw.get('qr_image_data'),
        'status': raw.get('status') or 'pending',
        'metadata': {},
    }


def _normalize_status(value: Any) -> str:
    normalized = str(value or 'pending').strip().casefold()
    if normalized in {'succeeded', 'success', 'paid', 'confirmed', 'closed', 'overpaid'}:
        return 'succeeded'
    if normalized in {'canceled', 'cancelled', 'failed', 'fail', 'expired', 'declined'}:
        return 'canceled'
    return 'pending'


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


__all__ = [
    'PaymentProviderAdapter',
    'ProviderInvoice',
    'check_provider_invoice',
    'create_provider_invoice',
    'get_payment_provider_adapter',
    'list_payment_provider_adapters',
]
