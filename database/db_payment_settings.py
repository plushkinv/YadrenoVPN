"""Configuration and administrative visibility of built-in payment providers."""
from core.payment_provider_specs import (
    INTERNATIONAL_PROVIDERS, MYFATOORAH_CURRENCIES, PROVIDER_FIELD_CHOICES,
    admin_visibility_key, default_admin_visibility,
)
from .db_settings import get_setting
from .db_language import get_installation_language

__all__ = ['is_payment_admin_visible', 'get_gateway_settings', 'is_gateway_enabled',
           'is_gateway_configured', 'get_cards_currency', 'get_myfatoorah_currency']


def is_payment_admin_visible(provider_id: str) -> bool:
    default = default_admin_visibility(provider_id, get_installation_language())
    return get_setting(admin_visibility_key(provider_id), str(int(default))) == '1'


def get_gateway_settings(provider_id: str) -> dict[str, str]:
    fields = INTERNATIONAL_PROVIDERS[provider_id][2]
    values = {field: str(get_setting(f'{provider_id}_{field}', '') or '') for field in fields}
    return {field: value if field == 'password' else value.strip() for field, value in values.items()}


def is_gateway_enabled(provider_id: str) -> bool:
    return provider_id in INTERNATIONAL_PROVIDERS and get_setting(f'{provider_id}_enabled', '0') == '1'


def is_gateway_configured(provider_id: str, *, require_enabled: bool = True) -> bool:
    if require_enabled and not is_gateway_enabled(provider_id):
        return False
    values = get_gateway_settings(provider_id)
    if not all(values.values()):
        return False
    for field, value in values.items():
        choices = PROVIDER_FIELD_CHOICES.get((provider_id, field))
        if choices and value not in choices:
            return False
    if provider_id == 'paytabs' and (not values['profile_id'].isascii()
                                   or not values['profile_id'].isdecimal()
                                   or int(values['profile_id']) <= 0):
        return False
    if provider_id == 'myfatoorah' and values['currency'] != MYFATOORAH_CURRENCIES.get(values['region']):
        return False
    return True


def get_cards_currency() -> str:
    value = get_setting('cards_currency', 'RUB')
    return value if value in {'RUB', 'USD'} else 'RUB'


def get_myfatoorah_currency() -> str:
    return str(get_setting('myfatoorah_currency', '') or '').upper()
