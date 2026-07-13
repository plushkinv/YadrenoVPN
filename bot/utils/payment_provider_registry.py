"""Registry of custom payment providers for custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from hmac import compare_digest
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PROVIDER_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
_ALLOWED_URL_SCHEMES = {'https', 'http', 'tg'}
_RESERVED_PROVIDER_IDS = {
    'balance',
    'cards',
    'cardlink',
    'crypto',
    'demo',
    'platega',
    'promo_free',
    'stars',
    'trial',
    'wata',
    'yookassa_qr',
}
_STATUS_ALIASES = {
    'paid': 'succeeded',
    'success': 'succeeded',
    'succeeded': 'succeeded',
    'completed': 'succeeded',
    'ok': 'succeeded',
    'pending': 'pending',
    'new': 'pending',
    'waiting': 'pending',
    'created': 'pending',
    'processing': 'pending',
    'failed': 'canceled',
    'fail': 'canceled',
    'canceled': 'canceled',
    'cancelled': 'canceled',
    'expired': 'canceled',
}

ProviderCreate = Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
ProviderCheck = Callable[[Mapping[str, Any]], Mapping[str, Any] | str | Awaitable[Mapping[str, Any] | str]]
ProviderWebhook = Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
ProviderEnabled = bool | Callable[[Mapping[str, Any]], bool]


@dataclass
class PaymentProvider:
    """Description of the custom provider registered by the extension."""

    provider_id: str
    payment_type: str
    title: str
    label: str
    create_payment: ProviderCreate
    check_payment: ProviderCheck
    webhook_handler: ProviderWebhook | None = None
    webhook_secret: str | None = None
    minimum_amount_cents: int = 0
    is_enabled: ProviderEnabled = True
    auto_check_interval_seconds: int | None = 300
    metadata: dict[str, Any] = field(default_factory=dict)


PAYMENT_PROVIDERS: dict[str, PaymentProvider] = {}


def register_payment_provider(
    provider_id: str,
    *,
    create_payment: ProviderCreate,
    check_payment: ProviderCheck,
    webhook_handler: ProviderWebhook | None = None,
    webhook_secret: str | None = None,
    title: str | None = None,
    label: str | None = None,
    minimum_amount_cents: int = 0,
    is_enabled: ProviderEnabled = True,
    auto_check_interval_seconds: int | None = 300,
    metadata: Mapping[str, Any] | None = None,
    replace: bool = False,
) -> PaymentProvider:
    """Registers a custom payment provider extension."""
    pid = normalize_payment_provider_id(provider_id)
    _require_bool_option(replace, 'replace')
    if not callable(create_payment):
        raise ValueError('create_payment должен быть callable')
    if not callable(check_payment):
        raise ValueError('check_payment должен быть callable')
    if webhook_handler is not None and not callable(webhook_handler):
        raise ValueError('webhook_handler должен быть callable или None')
    if callable(is_enabled) is False and not isinstance(is_enabled, bool):
        raise ValueError('is_enabled должен быть bool или callable')
    if pid in PAYMENT_PROVIDERS and not replace:
        raise ValueError(f"payment provider '{pid}' уже зарегистрирован")

    provider = PaymentProvider(
        provider_id=pid,
        payment_type=provider_payment_type(pid),
        title=_normalize_display_text(title if title is not None else label if label is not None else pid, 'title'),
        label=_normalize_display_text(label if label is not None else title if title is not None else pid, 'label'),
        create_payment=create_payment,
        check_payment=check_payment,
        webhook_handler=webhook_handler,
        webhook_secret=_optional_config_text(webhook_secret, 'webhook_secret'),
        minimum_amount_cents=_normalize_amount(minimum_amount_cents, 'minimum_amount_cents'),
        is_enabled=is_enabled,
        auto_check_interval_seconds=_normalize_auto_check_interval(auto_check_interval_seconds),
        metadata=_normalize_config_metadata(metadata),
    )
    if not provider.title:
        raise ValueError('title не может быть пустым')
    if not provider.label:
        raise ValueError('label не может быть пустым')

    PAYMENT_PROVIDERS[pid] = provider
    return provider


def normalize_payment_provider_id(provider_id: str) -> str:
    """Normalizes and validates provider_id for the payment provider API."""
    if not isinstance(provider_id, str):
        raise ValueError("provider_id должен быть строкой")
    value = provider_id.strip().casefold()
    if not _PROVIDER_ID_RE.fullmatch(value):
        raise ValueError("provider_id должен соответствовать ^[a-z][a-z0-9_]{0,31}$")
    if value.startswith('ext_') or value in _RESERVED_PROVIDER_IDS:
        raise ValueError('provider_id зарезервирован ядром')
    return value


def provider_payment_type(provider_id: str) -> str:
    """Returns the internal payment_type for a custom provider."""
    return f"ext_{normalize_payment_provider_id(provider_id)}"


def is_custom_payment_type(payment_type: str | None) -> bool:
    """Checks whether the payment_type refers to a registered custom provider."""
    if not isinstance(payment_type, str):
        return False
    value = payment_type.strip().casefold()
    return value.startswith('ext_') and value[4:] in PAYMENT_PROVIDERS


def get_payment_provider(provider_id: str) -> PaymentProvider | None:
    """Returns the registered provider by provider_id."""
    return PAYMENT_PROVIDERS.get(normalize_payment_provider_id(provider_id))


def get_payment_provider_by_type(payment_type: str) -> PaymentProvider | None:
    """Returns the provider by internal payment_type of the form ext_<id>."""
    if not isinstance(payment_type, str):
        return None
    value = payment_type.strip().casefold()
    if not value.startswith('ext_'):
        return None
    return PAYMENT_PROVIDERS.get(value[4:])


def list_payment_providers(*, enabled_only: bool = False, context: Mapping[str, Any] | None = None) -> list[PaymentProvider]:
    """Returns registered payment providers."""
    providers = list(PAYMENT_PROVIDERS.values())
    if enabled_only:
        enabled_context = _normalize_optional_context(context)
        providers = [
            provider
            for provider in providers
            if is_payment_provider_enabled(provider.provider_id, enabled_context)
        ]
    return providers


def is_payment_provider_enabled(provider_id: str, context: Mapping[str, Any] | None = None) -> bool:
    """Checks whether the provider is available in the current context."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        return False
    enabled_context = _normalize_optional_context(context)
    enabled = provider.is_enabled
    if isinstance(enabled, bool):
        return enabled
    try:
        result = enabled(dict(enabled_context))
        if inspect.isawaitable(result):
            raise ValueError('is_enabled должен быть синхронным')
        if not isinstance(result, bool):
            raise ValueError('is_enabled должен вернуть bool')
        return result
    except Exception as e:
        logger.warning("Payment provider '%s' скрыт из-за ошибки is_enabled: %s", provider.provider_id, e)
        return False


async def create_payment(provider_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """Calls create_payment on the registered provider and normalizes the result."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')
    raw_result = provider.create_payment(_normalize_required_context(context))
    if inspect.isawaitable(raw_result):
        raw_result = await raw_result
    return _normalize_create_result(raw_result)


async def check_payment(provider_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """Calls check_payment of the registered provider and normalizes the status."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')
    raw_result = provider.check_payment(_normalize_required_context(context))
    if inspect.isawaitable(raw_result):
        raw_result = await raw_result
    return _normalize_check_result(raw_result)


async def handle_payment_webhook(provider_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """Calls the provider's webhook_handler and normalizes the declarative result."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')
    if provider.webhook_handler is None:
        raise ValueError('payment provider не принимает webhook')
    raw_result = provider.webhook_handler(_normalize_required_context(context))
    if inspect.isawaitable(raw_result):
        raw_result = await raw_result
    return _normalize_webhook_result(raw_result)


def validate_payment_webhook_secret(provider_id: str, provided_secret: str | None) -> bool:
    """Checks a simple shared secret webhook if the provider has set it."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        return False
    expected = provider.webhook_secret
    if not expected:
        return True
    if not isinstance(provided_secret, str):
        return False
    return compare_digest(expected, provided_secret)


def _normalize_create_result(raw_result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_result, Mapping):
        raise ValueError('create_payment должен вернуть dict')
    result = dict(raw_result)
    payment_url = _required_result_text(result, 'payment_url', aliases=('url',))
    _validate_payment_url(payment_url)

    metadata = _normalize_result_metadata(result)

    return {
        'payment_url': payment_url,
        'provider_payment_id': _optional_result_text(result, 'provider_payment_id', aliases=('external_id',)),
        'status': _normalize_status(result.get('status', 'pending')),
        'metadata': dict(metadata),
    }


def _normalize_check_result(raw_result: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(raw_result, str):
        raw_result = {'status': raw_result}
    if not isinstance(raw_result, Mapping):
        raise ValueError('check_payment должен вернуть dict, str status или awaitable')
    result = dict(raw_result)
    metadata = _normalize_result_metadata(result)

    normalized = {
        'status': _normalize_status(result.get('status')),
        'provider_payment_id': _optional_result_text(result, 'provider_payment_id', aliases=('external_id',)),
        'payment_url': _optional_result_text(result, 'payment_url', aliases=('url',)),
        'reason': _optional_result_text(result, 'reason'),
        'metadata': dict(metadata),
    }
    if normalized['payment_url']:
        _validate_payment_url(normalized['payment_url'])
    return normalized


def _normalize_webhook_result(raw_result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_result, Mapping):
        raise ValueError('webhook_handler должен вернуть dict')
    result = dict(raw_result)
    metadata = _normalize_result_metadata(result)

    ignored = result.get('ignored', False)
    if not isinstance(ignored, bool):
        raise ValueError('ignored должен быть bool')
    if ignored:
        return {
            'ignored': True,
            'reason': _optional_result_text(result, 'reason'),
            'metadata': dict(metadata),
        }

    normalized = {
        'ignored': False,
        'status': _normalize_status(result.get('status')),
        'order_id': _optional_result_text(result, 'order_id', aliases=('invoice_id',)),
        'provider_payment_id': _optional_result_text(result, 'provider_payment_id', aliases=('external_id',)),
        'payment_url': _optional_result_text(result, 'payment_url', aliases=('url',)),
        'reason': _optional_result_text(result, 'reason'),
        'metadata': dict(metadata),
    }
    if not normalized['order_id'] and not normalized['provider_payment_id']:
        raise ValueError('webhook_handler должен вернуть order_id или provider_payment_id')
    if normalized['payment_url']:
        _validate_payment_url(normalized['payment_url'])
    return normalized


def _normalize_status(status: Any) -> str:
    if not isinstance(status, str):
        raise ValueError("status должен быть строкой: succeeded, pending или canceled")
    value = status.strip().casefold()
    normalized = _STATUS_ALIASES.get(value)
    if not normalized:
        raise ValueError("status должен быть succeeded, pending или canceled")
    return normalized


def _validate_payment_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError('payment_url должен использовать https://, http:// или tg://')
    if parsed.scheme in {'http', 'https'} and not parsed.netloc:
        raise ValueError('payment_url должен содержать host')
    if parsed.scheme == 'tg' and not parsed.netloc:
        raise ValueError('tg:// payment_url должен содержать host')


def _normalize_amount(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{field} должен быть целым числом')
    amount = value
    if amount < 0:
        raise ValueError(f'{field} не может быть отрицательным')
    return amount


def _normalize_auto_check_interval(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('auto_check_interval_seconds должен быть целым числом или None')
    seconds = value
    if seconds < 0:
        raise ValueError('auto_check_interval_seconds не может быть отрицательным')
    return seconds


def _normalize_display_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{field} должен быть строкой')
    return value.strip()


def _optional_config_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{field} должен быть строкой или None')
    text = value.strip()
    return text or None


def _normalize_config_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError('metadata должен быть mapping или None')
    return dict(value)


def _normalize_optional_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    return _normalize_required_context(context)


def _normalize_required_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError('context должен быть mapping')
    return dict(context)


def _normalize_result_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    if 'metadata' not in result:
        return {}
    metadata = result['metadata']
    if not isinstance(metadata, Mapping):
        raise ValueError('metadata должен быть mapping')
    return dict(metadata)


def _require_bool_option(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{field} должен быть bool')
    return value


def _required_result_text(result: Mapping[str, Any], field: str, *, aliases: tuple[str, ...] = ()) -> str:
    text = _pick_result_text(result, field, aliases=aliases, required=True)
    assert text is not None
    return text


def _optional_result_text(result: Mapping[str, Any], field: str, *, aliases: tuple[str, ...] = ()) -> str | None:
    return _pick_result_text(result, field, aliases=aliases, required=False)


def _pick_result_text(
    result: Mapping[str, Any],
    field: str,
    *,
    aliases: tuple[str, ...],
    required: bool,
) -> str | None:
    keys = (field, *aliases)
    for key in keys:
        if key not in result:
            continue
        value = result.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f'{field} должен быть строкой')
        text = value.strip()
        if text:
            return text
    if required:
        raise ValueError(f'create_payment должен вернуть {field}')
    return None


__all__ = [
    'PAYMENT_PROVIDERS',
    'PaymentProvider',
    'check_payment',
    'create_payment',
    'handle_payment_webhook',
    'get_payment_provider',
    'get_payment_provider_by_type',
    'is_custom_payment_type',
    'is_payment_provider_enabled',
    'list_payment_providers',
    'normalize_payment_provider_id',
    'provider_payment_type',
    'register_payment_provider',
    'validate_payment_webhook_secret',
]
