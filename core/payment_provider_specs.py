"""Built-in provider configuration facts, independent of runtime and storage."""

INTERNATIONAL_PROVIDERS = {
    'nowpayments': ('NOWPayments', '🪙', ('api_key', 'email', 'password')),
    'stripe': ('Stripe', '💳', ('secret_key',)),
    'paypal': ('PayPal', '💳', ('client_id', 'client_secret', 'environment')),
    'paytabs': ('PayTabs', '💳', ('profile_id', 'server_key', 'region')),
    'myfatoorah': ('MyFatoorah', '💳', ('api_token', 'region', 'currency')),
}
BUILTIN_PROVIDER_IDS = (
    'stars', 'cryptobot', 'crypto', 'cards', 'yookassa_qr', 'wata', 'platega',
    'cardlink', 'demo', *INTERNATIONAL_PROVIDERS,
)
REGIONAL_CURRENCY_EXPONENTS = {
    'AED': 2, 'SAR': 2, 'QAR': 2, 'EGP': 2,
    'BHD': 3, 'KWD': 3, 'OMR': 3, 'JOD': 3,
}
PAYTABS_ENDPOINTS = {
    'AE': 'https://secure.paytabs.com', 'SA': 'https://secure.paytabs.sa',
    'EG': 'https://secure-egypt.paytabs.com', 'OM': 'https://secure-oman.paytabs.com',
    'JO': 'https://secure-jordan.paytabs.com', 'KW': 'https://secure-kuwait.paytabs.com',
    'IQ': 'https://secure-iraq.paytabs.com', 'MA': 'https://secure-morocco.paytabs.com',
    'QA': 'https://secure-doha.paytabs.com', 'GLOBAL': 'https://secure-global.paytabs.com',
}
MYFATOORAH_ENDPOINTS = {
    'KW': 'https://api.myfatoorah.com', 'BH': 'https://api.myfatoorah.com',
    'JO': 'https://api.myfatoorah.com', 'OM': 'https://api.myfatoorah.com',
    'AE': 'https://api-ae.myfatoorah.com', 'SA': 'https://api-sa.myfatoorah.com',
    'QA': 'https://api-qa.myfatoorah.com', 'EG': 'https://api-eg.myfatoorah.com',
    'TEST': 'https://apitest.myfatoorah.com',
}
MYFATOORAH_CURRENCIES = {
    'KW': 'KWD', 'BH': 'BHD', 'JO': 'JOD', 'OM': 'OMR',
    'AE': 'AED', 'SA': 'SAR', 'QA': 'QAR', 'EG': 'EGP', 'TEST': 'KWD',
}
PROVIDER_FIELD_CHOICES = {
    ('paypal', 'environment'): ('live', 'sandbox'),
    ('paytabs', 'region'): tuple(PAYTABS_ENDPOINTS),
    ('myfatoorah', 'region'): tuple(MYFATOORAH_ENDPOINTS),
    ('myfatoorah', 'currency'): tuple(REGIONAL_CURRENCY_EXPONENTS),
}


def admin_visibility_key(provider_id: str) -> str:
    if provider_id not in BUILTIN_PROVIDER_IDS:
        raise ValueError('Unknown built-in payment provider')
    return f'payment_admin_visible_{provider_id}'


def default_admin_visibility(provider_id: str, language: str) -> bool:
    if provider_id == 'crypto':
        return False
    if provider_id in INTERNATIONAL_PROVIDERS:
        return language != 'ru'
    return provider_id != 'yookassa_qr' or language == 'ru'


def payment_setting_defaults(language: str) -> dict[str, str]:
    values = {admin_visibility_key(pid): str(int(default_admin_visibility(pid, language)))
              for pid in BUILTIN_PROVIDER_IDS}
    values.update(cards_currency='RUB', cards_yookassa_receipt='1')
    for pid, (_, _, fields) in INTERNATIONAL_PROVIDERS.items():
        values[f'{pid}_enabled'] = '0'
        values.update({f'{pid}_{field}': '' for field in fields})
    values['paypal_environment'] = 'live'
    return values
