"""
Реестр действий кнопок страниц.

Содержит:
- ACTION_REGISTRY: маппинг action_value → callback_data для internal-кнопок
- SYSTEM_BUTTONS: маппинг button_id → handler(context) для system-кнопок

Правила:
- action_value — контракт, НЕЛЬЗЯ менять после релиза
- button_id — контракт, НЕЛЬЗЯ менять после релиза
"""
import logging
from typing import Optional, Dict, Any, Callable, Mapping

logger = logging.getLogger(__name__)

MAX_CALLBACK_DATA_BYTES = 64


# =============================================================================
# ACTION_REGISTRY: internal-кнопки
# Ключ = action_value из buttons_default, Значение = callback_data для Telegram
# =============================================================================

ACTION_REGISTRY: Dict[str, str] = {
    "cmd_buy":            "buy_key",
    "cmd_my_keys":        "my_keys",
    "cmd_help":           "help",
    "cmd_back_main":      "start",
    "cmd_trial":          "trial_subscription",
    "cmd_referral":       "referral_system",
    "cmd_activate_trial": "trial_activate",
    "cmd_support":        "support_start",
    "cmd_show_profile":   "route:profile",
    "cmd_show_id":        "show_id",
}


def register_action_handler(action_value: str, callback_data: str, *, replace: bool = False) -> None:
    """Регистрирует callback для internal-кнопки расширения."""
    action_key = _require_text(action_value, 'action_value').strip()
    callback = normalize_callback_data(callback_data)
    _require_bool(replace, 'replace')

    if not action_key:
        raise ValueError("action_value не может быть пустым")
    if action_key in ACTION_REGISTRY and not replace:
        raise ValueError(f"action_value '{action_key}' уже зарегистрирован")

    ACTION_REGISTRY[action_key] = callback


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} должен быть bool")
    return value


def normalize_callback_data(value: Any, field: str = 'callback_data') -> str:
    """Проверяет callback_data по контракту Telegram InlineKeyboardButton."""
    callback = _require_text(value, field).strip()
    if not callback:
        raise ValueError(f"{field} не может быть пустым")
    if len(callback.encode('utf-8')) > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(f"{field} не может быть длиннее {MAX_CALLBACK_DATA_BYTES} байт")
    return callback


# =============================================================================
# SYSTEM_BUTTONS: system-кнопки
#
# Каждый handler получает context: dict и возвращает:
# - dict с ключами: callback_data, url, label, hidden (все опциональные)
# - None — кнопка полностью скрывается
#
# context содержит данные, переданные хендлером в render_page:
# - order_id, telegram_id, и другие параметры
# =============================================================================


def _resolve_pay_crypto(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты криптой (USDT). Определяет видимость и формирует action."""
    from database.requests import is_crypto_configured

    if not is_crypto_configured():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_crypto:{order_id}" if order_id else "pay_crypto"
    return {"callback_data": cb}


def _resolve_pay_stars(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты звёздами."""
    from database.requests import is_stars_enabled

    if not is_stars_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_stars:{order_id}" if order_id else "pay_stars"
    return {"callback_data": cb}


def _resolve_pay_cards(ctx: dict) -> Optional[dict]:
    """Кнопка TG payments (историческое внутреннее имя cards)."""
    from database.requests import is_cards_enabled

    if not is_cards_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_cards:{order_id}" if order_id else "pay_cards"
    return {"callback_data": cb}


def _resolve_pay_qr(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты через ЮКассу."""
    from database.requests import is_yookassa_qr_configured

    if not is_yookassa_qr_configured():
        return None

    return {"callback_data": "pay_qr"}


def _resolve_pay_wata(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты через WATA."""
    from database.requests import is_wata_configured

    if not is_wata_configured():
        return None

    return {"callback_data": "pay_wata"}


def _resolve_pay_platega(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты через Platega."""
    from database.requests import is_platega_configured

    if not is_platega_configured():
        return None

    return {"callback_data": "pay_platega"}


def _resolve_pay_cardlink(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты через Cardlink."""
    from database.requests import is_cardlink_configured

    if not is_cardlink_configured():
        return None

    return {"callback_data": "pay_cardlink"}


def _resolve_pay_demo(ctx: dict) -> Optional[dict]:
    """Кнопка демо-оплаты (РФ карта)."""
    from database.requests import is_demo_payment_enabled

    if not is_demo_payment_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"demo_tariffs:{order_id}" if order_id else "demo_tariffs"
    return {"callback_data": cb}


def _resolve_pay_balance(ctx: dict) -> Optional[dict]:
    """Кнопка «Использовать баланс». Видна только при referral + balance > 0."""
    from database.requests import (
        is_referral_enabled, get_referral_reward_type,
        get_user_balance, get_user_internal_id,
    )

    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        return None

    telegram_id = ctx.get('telegram_id')
    if not telegram_id:
        return None

    user_id = get_user_internal_id(telegram_id)
    if not user_id:
        return None

    balance_cents = get_user_balance(user_id)
    if balance_cents <= 0:
        return None

    return {"callback_data": "pay_use_balance"}


def _resolve_enter_promo(ctx: dict) -> Optional[dict]:
    """Кнопка ввода промокода на странице покупки."""
    from database.requests import has_available_promo_codes

    if not has_available_promo_codes():
        return None

    return {"callback_data": "promo_enter"}


def _get_renew_key_id(ctx: dict) -> Optional[str]:
    """Возвращает key_id для кнопок продления или скрывает кнопку без контекста."""
    key_id = ctx.get('key_id')
    if key_id is None or key_id == '':
        return None
    if isinstance(key_id, bool) or not isinstance(key_id, (int, str)):
        logger.warning("Некорректный key_id для system-кнопки: %r", key_id)
        return None
    return str(key_id)


def _resolve_renew_pay_crypto(ctx: dict) -> Optional[dict]:
    """Кнопка продления через крипто-оплату (USDT)."""
    from database.requests import is_crypto_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_crypto_configured():
        return None

    return {"callback_data": f"renew_crypto_tariff:{key_id}"}


def _resolve_renew_pay_stars(ctx: dict) -> Optional[dict]:
    """Кнопка продления через Telegram Stars."""
    from database.requests import is_stars_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_stars_enabled():
        return None

    return {"callback_data": f"renew_stars_tariff:{key_id}"}


def _resolve_renew_pay_cards(ctx: dict) -> Optional[dict]:
    """Кнопка продления через Telegram Payments."""
    from database.requests import is_cards_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_cards_enabled():
        return None

    return {"callback_data": f"renew_cards_tariff:{key_id}"}


def _resolve_renew_pay_qr(ctx: dict) -> Optional[dict]:
    """Кнопка продления через QR-оплату ЮКассы."""
    from database.requests import is_yookassa_qr_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_yookassa_qr_configured():
        return None

    return {"callback_data": f"renew_qr_tariff:{key_id}"}


def _resolve_renew_pay_wata(ctx: dict) -> Optional[dict]:
    """Кнопка продления через WATA."""
    from database.requests import is_wata_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_wata_configured():
        return None

    return {"callback_data": f"renew_wata_tariff:{key_id}"}


def _resolve_renew_pay_platega(ctx: dict) -> Optional[dict]:
    """Кнопка продления через Platega."""
    from database.requests import is_platega_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_platega_configured():
        return None

    return {"callback_data": f"renew_platega_tariff:{key_id}"}


def _resolve_renew_pay_cardlink(ctx: dict) -> Optional[dict]:
    """Кнопка продления через Cardlink."""
    from database.requests import is_cardlink_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_cardlink_configured():
        return None

    return {"callback_data": f"renew_cardlink_tariff:{key_id}"}


def _resolve_renew_pay_demo(ctx: dict) -> Optional[dict]:
    """Кнопка продления через демонстрационную оплату."""
    from database.requests import is_demo_payment_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_demo_payment_enabled():
        return None

    return {"callback_data": f"renew_demo_tariffs:{key_id}"}


def _resolve_renew_pay_balance(ctx: dict) -> Optional[dict]:
    """Кнопка продления с внутреннего баланса."""
    from database.requests import (
        is_referral_enabled, get_referral_reward_type,
        get_user_balance, get_user_internal_id,
    )

    key_id = _get_renew_key_id(ctx)
    if not key_id:
        return None

    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        return None

    telegram_id = ctx.get('telegram_id')
    if not telegram_id:
        return None

    user_id = get_user_internal_id(telegram_id)
    if not user_id:
        return None

    balance_cents = get_user_balance(user_id)
    if balance_cents <= 0:
        return None

    return {"callback_data": f"pay_use_balance:{key_id}"}


def _resolve_pay_ext(btn_id: str, ctx: dict) -> Optional[dict]:
    provider_id = btn_id.removeprefix('btn_pay_ext_')
    if not provider_id:
        return None
    from bot.utils.payment_provider_registry import get_payment_provider, is_payment_provider_enabled

    provider = get_payment_provider(provider_id)
    if provider is None or not is_payment_provider_enabled(provider.provider_id, ctx):
        return None
    return {
        "callback_data": f"pe:{provider.provider_id}",
        "label": provider.label,
    }


def _resolve_renew_pay_ext(btn_id: str, ctx: dict) -> Optional[dict]:
    provider_id = btn_id.removeprefix('btn_renew_pay_ext_')
    key_id = _get_renew_key_id(ctx)
    if not provider_id or not key_id:
        return None
    from bot.utils.payment_provider_registry import get_payment_provider, is_payment_provider_enabled

    provider = get_payment_provider(provider_id)
    if provider is None or not is_payment_provider_enabled(provider.provider_id, ctx):
        return None
    return {
        "callback_data": f"re:{provider.provider_id}:{key_id}",
        "label": provider.label,
    }


def _resolve_renew_enter_promo(ctx: dict) -> Optional[dict]:
    """Кнопка ввода промокода на странице продления."""
    from database.requests import has_available_promo_codes

    key_id = _get_renew_key_id(ctx)
    if not key_id or not has_available_promo_codes():
        return None

    return {"callback_data": f"promo_enter:{key_id}"}


def _resolve_renew_back(ctx: dict) -> Optional[dict]:
    """Кнопка возврата со страницы выбора оплаты продления к ключу."""
    key_id = _get_renew_key_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key:{key_id}"}


def _get_key_details_id(ctx: dict) -> Optional[str]:
    """Возвращает key_id для кнопок карточки ключа."""
    return _get_renew_key_id(ctx)


def _key_details_is_active(ctx: dict) -> bool:
    return ctx.get('key_active') is True


def _key_details_is_unconfigured(ctx: dict) -> bool:
    return ctx.get('is_unconfigured') is True


def _key_details_traffic_exhausted(ctx: dict) -> bool:
    return ctx.get('traffic_exhausted') is True


def _key_details_has_sub_id(ctx: dict) -> bool:
    return ctx.get('has_sub_id') is True


def _resolve_key_show_key(ctx: dict) -> Optional[dict]:
    """Кнопка показа обычного VPN-ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx):
        return None
    if _key_details_is_unconfigured(ctx) or _key_details_traffic_exhausted(ctx):
        return None
    if _key_details_has_sub_id(ctx):
        return None

    return {"callback_data": f"key_show:{key_id}"}


def _resolve_key_show_subscription(ctx: dict) -> Optional[dict]:
    """Кнопка показа subscription-ссылки."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx):
        return None
    if _key_details_is_unconfigured(ctx) or _key_details_traffic_exhausted(ctx):
        return None
    if not _key_details_has_sub_id(ctx):
        return None

    return {"callback_data": f"key_show:{key_id}"}


def _resolve_key_configure(ctx: dict) -> Optional[dict]:
    """Кнопка настройки ещё не созданного на сервере ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx) or not _key_details_is_unconfigured(ctx):
        return None

    return {"callback_data": f"key_replace:{key_id}"}


def _resolve_key_renew(ctx: dict) -> Optional[dict]:
    """Кнопка продления ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key_renew:{key_id}"}


def _resolve_key_replace(ctx: dict) -> Optional[dict]:
    """Кнопка замены активного настроенного ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx):
        return None
    if _key_details_is_unconfigured(ctx) or _key_details_traffic_exhausted(ctx):
        return None

    return {"callback_data": f"key_replace:{key_id}"}


def _resolve_key_delete(ctx: dict) -> Optional[dict]:
    """Кнопка удаления истёкшего или исчерпавшего трафик ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if _key_details_is_active(ctx) and not _key_details_traffic_exhausted(ctx):
        return None

    return {"callback_data": f"key_delete:{key_id}"}


def _resolve_key_rename(ctx: dict) -> Optional[dict]:
    """Кнопка переименования ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key_rename:{key_id}"}



# Карта: button_id → handler
SYSTEM_BUTTONS: Dict[str, Callable[[dict], Optional[dict]]] = {
    "btn_pay_crypto":  _resolve_pay_crypto,
    "btn_pay_stars":   _resolve_pay_stars,
    "btn_pay_cards":   _resolve_pay_cards,
    "btn_pay_qr":      _resolve_pay_qr,
    "btn_pay_wata":    _resolve_pay_wata,
    "btn_pay_platega": _resolve_pay_platega,
    "btn_pay_cardlink": _resolve_pay_cardlink,
    "btn_pay_demo":    _resolve_pay_demo,
    "btn_pay_balance": _resolve_pay_balance,
    "btn_enter_promo": _resolve_enter_promo,
    "btn_renew_pay_crypto": _resolve_renew_pay_crypto,
    "btn_renew_pay_stars": _resolve_renew_pay_stars,
    "btn_renew_pay_cards": _resolve_renew_pay_cards,
    "btn_renew_pay_qr": _resolve_renew_pay_qr,
    "btn_renew_pay_wata": _resolve_renew_pay_wata,
    "btn_renew_pay_platega": _resolve_renew_pay_platega,
    "btn_renew_pay_cardlink": _resolve_renew_pay_cardlink,
    "btn_renew_pay_demo": _resolve_renew_pay_demo,
    "btn_renew_pay_balance": _resolve_renew_pay_balance,
    "btn_renew_enter_promo": _resolve_renew_enter_promo,
    "btn_renew_back": _resolve_renew_back,
    "btn_key_show_key": _resolve_key_show_key,
    "btn_key_show_subscription": _resolve_key_show_subscription,
    "btn_key_configure": _resolve_key_configure,
    "btn_key_renew": _resolve_key_renew,
    "btn_key_replace": _resolve_key_replace,
    "btn_key_delete": _resolve_key_delete,
    "btn_key_rename": _resolve_key_rename,
}


def resolve_system_button(button_id: str, context: Mapping[str, Any]) -> Optional[dict]:
    button_id = _require_text(button_id, 'button_id')
    if not isinstance(context, Mapping):
        raise ValueError("context должен быть mapping")
    context = dict(context)

    handler = SYSTEM_BUTTONS.get(button_id)
    if handler is not None:
        return _normalize_system_button_result(button_id, handler(context))
    if button_id.startswith('btn_pay_ext_'):
        return _normalize_system_button_result(button_id, _resolve_pay_ext(button_id, context))
    if button_id.startswith('btn_renew_pay_ext_'):
        return _normalize_system_button_result(button_id, _resolve_renew_pay_ext(button_id, context))
    return None


def _normalize_system_button_result(button_id: str, result: Any) -> Optional[dict]:
    """Проверяет контракт system handler-а перед передачей в renderer."""
    if result is None:
        return None
    if not isinstance(result, Mapping):
        raise ValueError(f"system handler '{button_id}' должен вернуть dict или None")

    normalized = dict(result)
    allowed = {'callback_data', 'url', 'label', 'hidden'}
    unknown = set(normalized.keys()) - allowed
    if unknown:
        raise ValueError(f"system handler '{button_id}' вернул неподдерживаемые поля: {', '.join(sorted(unknown))}")

    for field in ('callback_data', 'url', 'label'):
        if field in normalized and normalized[field] is not None:
            value = normalized[field]
            if not isinstance(value, str):
                raise ValueError(f"system handler '{button_id}' field {field} должен быть строкой")
            normalized[field] = value.strip()

    if normalized.get('callback_data') and normalized.get('url'):
        raise ValueError(f"system handler '{button_id}' не может вернуть одновременно callback_data и url")

    if normalized.get('callback_data'):
        normalized['callback_data'] = normalize_callback_data(
            normalized['callback_data'],
            f"system handler '{button_id}' field callback_data",
        )

    if 'hidden' in normalized and not isinstance(normalized['hidden'], bool):
        raise ValueError(f"system handler '{button_id}' field hidden должен быть bool")

    return normalized
