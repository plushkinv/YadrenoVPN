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
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


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
}


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
    """Кнопка оплаты картой (Telegram Payments)."""
    from database.requests import is_cards_enabled

    if not is_cards_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_cards:{order_id}" if order_id else "pay_cards"
    return {"callback_data": cb}


def _resolve_pay_qr(ctx: dict) -> Optional[dict]:
    """Кнопка QR-оплаты (ЮКасса)."""
    from database.requests import is_yookassa_qr_configured

    if not is_yookassa_qr_configured():
        return None

    return {"callback_data": "pay_qr"}


def _resolve_pay_wata(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты через WATA (карта/СБП)."""
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
    """Кнопка оплаты через Cardlink (Карта/СБП)."""
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


def _get_renew_key_id(ctx: dict) -> Optional[str]:
    """Возвращает key_id для кнопок продления или скрывает кнопку без контекста."""
    key_id = ctx.get('key_id')
    if key_id is None or key_id == '':
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
    return bool(ctx.get('key_active'))


def _key_details_is_unconfigured(ctx: dict) -> bool:
    return bool(ctx.get('is_unconfigured'))


def _key_details_traffic_exhausted(ctx: dict) -> bool:
    return bool(ctx.get('traffic_exhausted'))


def _resolve_key_show_key(ctx: dict) -> Optional[dict]:
    """Кнопка показа обычного VPN-ключа."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx):
        return None
    if _key_details_is_unconfigured(ctx) or _key_details_traffic_exhausted(ctx):
        return None
    if ctx.get('has_sub_id'):
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
    if not ctx.get('has_sub_id'):
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
    "btn_renew_pay_crypto": _resolve_renew_pay_crypto,
    "btn_renew_pay_stars": _resolve_renew_pay_stars,
    "btn_renew_pay_cards": _resolve_renew_pay_cards,
    "btn_renew_pay_qr": _resolve_renew_pay_qr,
    "btn_renew_pay_wata": _resolve_renew_pay_wata,
    "btn_renew_pay_platega": _resolve_renew_pay_platega,
    "btn_renew_pay_cardlink": _resolve_renew_pay_cardlink,
    "btn_renew_pay_demo": _resolve_renew_pay_demo,
    "btn_renew_pay_balance": _resolve_renew_pay_balance,
    "btn_renew_back": _resolve_renew_back,
    "btn_key_show_key": _resolve_key_show_key,
    "btn_key_show_subscription": _resolve_key_show_subscription,
    "btn_key_configure": _resolve_key_configure,
    "btn_key_renew": _resolve_key_renew,
    "btn_key_replace": _resolve_key_replace,
    "btn_key_delete": _resolve_key_delete,
    "btn_key_rename": _resolve_key_rename,
}
