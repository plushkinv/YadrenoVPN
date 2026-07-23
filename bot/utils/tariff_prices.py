"""Shared base-aware tariff price formatting for Telegram UI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from bot.services.exchange_rate import get_payment_rate_snapshot
from bot.services.money import (
    base_minor_to_charge_units,
    format_money_minor,
)
from bot.utils.text import escape_html
from database.requests import get_base_currency


@dataclass(frozen=True)
class TariffPriceDisplayConfig:
    """Payment currencies and rates used for one UI render."""

    crypto_enabled: bool
    stars_enabled: bool
    rub_enabled: bool
    rate_snapshot: Mapping[str, Any]
    base_currency: str = 'RUB'
    show_base: bool = False
    extra_payment_types: tuple[str, ...] = ()


def load_tariff_price_display_config() -> TariffPriceDisplayConfig:
    """Loads the base and distinct currencies currently available to users."""
    from database.requests import (
        is_cardlink_configured,
        is_cards_enabled,
        is_crypto_configured,
        is_demo_payment_enabled,
        is_platega_configured,
        is_stars_enabled,
        is_wata_configured,
        is_yookassa_qr_configured,
    )

    crypto_enabled = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    rub_enabled = (
        is_cards_enabled()
        or is_yookassa_qr_configured()
        or is_wata_configured()
        or is_platega_configured()
        or is_cardlink_configured()
        or is_demo_payment_enabled()
    )
    try:
        from bot.utils.payment_provider_registry import list_payment_providers

        extra_payment_types = tuple(
            provider.payment_type
            for provider in list_payment_providers(enabled_only=True)
        )
    except (ImportError, ValueError):
        extra_payment_types = ()
    base = get_base_currency()
    return TariffPriceDisplayConfig(
        crypto_enabled=crypto_enabled,
        stars_enabled=stars_enabled,
        rub_enabled=rub_enabled,
        rate_snapshot=get_payment_rate_snapshot(),
        base_currency=base,
        show_base=True,
        extra_payment_types=extra_payment_types,
    )


def format_tariff_price_display(
    price: Any,
    *,
    config: TariffPriceDisplayConfig | None = None,
) -> str:
    """Formats one base price once per enabled payment currency."""
    display_config = config or load_tariff_price_display_config()
    base, price_minor = _resolve_price(price, display_config)
    if price_minor <= 0:
        from bot.utils.user_ui_texts import get_ui_text

        return escape_html(get_ui_text('tariff.price_unset'))

    payment_types: list[str] = []
    if display_config.crypto_enabled:
        payment_types.append('crypto')
    if display_config.stars_enabled:
        payment_types.append('stars')
    if display_config.rub_enabled:
        payment_types.append('cards')
    payment_types.extend(display_config.extra_payment_types)

    prices: list[str] = []
    displayed_currencies: set[str] = set()
    if display_config.show_base:
        prices.append(format_money_minor(price_minor, base))
        displayed_currencies.add(base)

    for payment_type in payment_types:
        try:
            amount, currency = base_minor_to_charge_units(
                price_minor,
                payment_type,
                display_config.rate_snapshot,
            )
        except ValueError:
            # A newly registered custom provider may not have a rate yet.
            # Its payment method remains unavailable until the admin configures it.
            continue
        if currency in displayed_currencies:
            continue
        rendered = format_money_minor(amount, currency)
        if currency == 'USDT':
            value = rendered.removesuffix(' USDT')
            rendered = f'${value}' if base != 'USD' else f'{value} USDT'
        prices.append(rendered)
        displayed_currencies.add(currency)

    if prices:
        return ' / '.join(prices)
    from bot.utils.user_ui_texts import get_ui_text

    return escape_html(get_ui_text('tariff.price_unset'))


def _resolve_price(
    price: Any,
    config: TariffPriceDisplayConfig,
) -> tuple[str, int]:
    if isinstance(price, Mapping):
        base = str(price.get('base_currency') or config.base_currency or 'RUB').upper()
        if price.get('price_minor') is not None:
            return base, max(0, int(price.get('price_minor') or 0))
        return 'RUB', max(0, int(price.get('price_rub') or 0)) * 100
    # Compatibility for old callers that pass a RUB-major scalar.
    return 'RUB', max(0, int(price or 0)) * 100


__all__ = [
    'TariffPriceDisplayConfig',
    'format_tariff_price_display',
    'load_tariff_price_display_config',
]
