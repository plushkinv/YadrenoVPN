"""
Register of page button actions.

Contains:
- ACTION_REGISTRY: mapping action_value → callback_data for internal buttons
- SYSTEM_BUTTONS: mapping button_id → handler(context) for system buttons

Rules:
- action_value — contract, CANNOT be changed after release
- button_id — contract, CANNOT be changed after release
"""
import logging
from typing import Optional, Dict, Any, Callable, Mapping

logger = logging.getLogger(__name__)

MAX_CALLBACK_DATA_BYTES = 64


# =============================================================================
# ACTION_REGISTRY: internal buttons
# Key = action_value from buttons_default, Value = callback_data for Telegram
# =============================================================================

ACTION_REGISTRY: Dict[str, str] = {
    "cmd_buy":            "buy_key",
    "cmd_balance_topup":  "balance_topup",
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
    """Registers a callback for the extension's internal button."""
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
    """Checks callback_data against the Telegram InlineKeyboardButton contract."""
    callback = _require_text(value, field).strip()
    if not callback:
        raise ValueError(f"{field} не может быть пустым")
    if len(callback.encode('utf-8')) > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(f"{field} не может быть длиннее {MAX_CALLBACK_DATA_BYTES} байт")
    return callback


# =============================================================================
# SYSTEM_BUTTONS: system buttons
#
# Each handler receives a context: dict and returns:
# - dict with keys: callback_data, url, label, hidden (all optional)
# - None - the button is completely hidden
#
# context contains the data passed by the handler to render_page:
# - order_id, telegram_id, and other parameters
# =============================================================================


def _resolve_pay_crypto(ctx: dict) -> Optional[dict]:
    """Crypto payment button (USDT). Determines visibility and generates action."""
    from database.requests import is_crypto_configured

    if not is_crypto_configured():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_crypto:{order_id}" if order_id else "pay_crypto"
    return {"callback_data": cb}


def _resolve_pay_stars(ctx: dict) -> Optional[dict]:
    """Star payment button."""
    from database.requests import is_stars_enabled

    if not is_stars_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_stars:{order_id}" if order_id else "pay_stars"
    return {"callback_data": cb}


def _resolve_pay_cards(ctx: dict) -> Optional[dict]:
    """TG payments button (historical internal name cards)."""
    from database.requests import is_cards_enabled

    if not is_cards_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_cards:{order_id}" if order_id else "pay_cards"
    return {"callback_data": cb}


def _resolve_pay_qr(ctx: dict) -> Optional[dict]:
    """Payment button via YuKassa."""
    from database.requests import is_yookassa_qr_configured

    if not is_yookassa_qr_configured():
        return None

    return {"callback_data": "pay_qr"}


def _resolve_pay_wata(ctx: dict) -> Optional[dict]:
    """Payment button via WATA."""
    from database.requests import is_wata_configured

    if not is_wata_configured():
        return None

    return {"callback_data": "pay_wata"}


def _resolve_pay_platega(ctx: dict) -> Optional[dict]:
    """Payment button via Platega."""
    from database.requests import is_platega_configured

    if not is_platega_configured():
        return None

    return {"callback_data": "pay_platega"}


def _resolve_pay_cardlink(ctx: dict) -> Optional[dict]:
    """Payment button via Cardlink."""
    from database.requests import is_cardlink_configured

    if not is_cardlink_configured():
        return None

    return {"callback_data": "pay_cardlink"}


def _resolve_pay_demo(ctx: dict) -> Optional[dict]:
    """Demo payment button (RF card)."""
    from database.requests import is_demo_payment_enabled

    if not is_demo_payment_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"demo_tariffs:{order_id}" if order_id else "demo_tariffs"
    return {"callback_data": cb}


def _resolve_pay_balance(ctx: dict) -> Optional[dict]:
    """“Use balance” button. Visible only when referral + balance > 0."""
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
    """Button to enter a promotional code on the purchase page."""
    from database.requests import has_available_promo_codes

    if not has_available_promo_codes():
        return None

    return {"callback_data": "promo_enter"}


def _get_renew_key_id(ctx: dict) -> Optional[str]:
    """Returns the key_id for renewal buttons or hides the button without context."""
    key_id = ctx.get('key_id')
    if key_id is None or key_id == '':
        return None
    if isinstance(key_id, bool) or not isinstance(key_id, (int, str)):
        logger.warning("Некорректный key_id для system-кнопки: %r", key_id)
        return None
    return str(key_id)


def _resolve_renew_pay_crypto(ctx: dict) -> Optional[dict]:
    """Renewal button via crypto payment (USDT)."""
    from database.requests import is_crypto_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_crypto_configured():
        return None

    return {"callback_data": f"renew_crypto_tariff:{key_id}"}


def _resolve_renew_pay_stars(ctx: dict) -> Optional[dict]:
    """Renewal button via Telegram Stars."""
    from database.requests import is_stars_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_stars_enabled():
        return None

    return {"callback_data": f"renew_stars_tariff:{key_id}"}


def _resolve_renew_pay_cards(ctx: dict) -> Optional[dict]:
    """Renewal button via Telegram Payments."""
    from database.requests import is_cards_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_cards_enabled():
        return None

    return {"callback_data": f"renew_cards_tariff:{key_id}"}


def _resolve_renew_pay_qr(ctx: dict) -> Optional[dict]:
    """Renewal button via YuKassa QR payment."""
    from database.requests import is_yookassa_qr_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_yookassa_qr_configured():
        return None

    return {"callback_data": f"renew_qr_tariff:{key_id}"}


def _resolve_renew_pay_wata(ctx: dict) -> Optional[dict]:
    """Renewal button via WATA."""
    from database.requests import is_wata_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_wata_configured():
        return None

    return {"callback_data": f"renew_wata_tariff:{key_id}"}


def _resolve_renew_pay_platega(ctx: dict) -> Optional[dict]:
    """Renewal button via Platega."""
    from database.requests import is_platega_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_platega_configured():
        return None

    return {"callback_data": f"renew_platega_tariff:{key_id}"}


def _resolve_renew_pay_cardlink(ctx: dict) -> Optional[dict]:
    """Renewal button via Cardlink."""
    from database.requests import is_cardlink_configured

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_cardlink_configured():
        return None

    return {"callback_data": f"renew_cardlink_tariff:{key_id}"}


def _resolve_renew_pay_demo(ctx: dict) -> Optional[dict]:
    """Renewal button via demo payment."""
    from database.requests import is_demo_payment_enabled

    key_id = _get_renew_key_id(ctx)
    if not key_id or not is_demo_payment_enabled():
        return None

    return {"callback_data": f"renew_demo_tariffs:{key_id}"}


def _resolve_renew_pay_balance(ctx: dict) -> Optional[dict]:
    """Extension button from internal balance."""
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
    """Button to enter a promotional code on the renewal page."""
    from database.requests import has_available_promo_codes

    key_id = _get_renew_key_id(ctx)
    if not key_id or not has_available_promo_codes():
        return None

    return {"callback_data": f"promo_enter:{key_id}"}


def _resolve_renew_back(ctx: dict) -> Optional[dict]:
    """Return button from the renewal payment selection page to the key."""
    key_id = _get_renew_key_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key:{key_id}"}


def _get_key_details_id(ctx: dict) -> Optional[str]:
    """Returns the key_id for key card buttons."""
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
    """Button to show regular VPN key."""
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
    """Button to show subscription link."""
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
    """Button for setting up a key that has not yet been created on the server."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx) or not _key_details_is_unconfigured(ctx):
        return None

    return {"callback_data": f"key_replace:{key_id}"}


def _resolve_key_renew(ctx: dict) -> Optional[dict]:
    """Key renewal button."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key_renew:{key_id}"}


def _resolve_key_replace(ctx: dict) -> Optional[dict]:
    """Button to replace the active configured key."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if not _key_details_is_active(ctx):
        return None
    if _key_details_is_unconfigured(ctx) or _key_details_traffic_exhausted(ctx):
        return None

    return {"callback_data": f"key_replace:{key_id}"}


def _resolve_key_delete(ctx: dict) -> Optional[dict]:
    """Button for deleting an expired or traffic-depleted key."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None
    if _key_details_is_active(ctx) and not _key_details_traffic_exhausted(ctx):
        return None

    return {"callback_data": f"key_delete:{key_id}"}


def _resolve_key_rename(ctx: dict) -> Optional[dict]:
    """Key rename button."""
    key_id = _get_key_details_id(ctx)
    if not key_id:
        return None

    return {"callback_data": f"key_rename:{key_id}"}


def _resolve_support_reply(ctx: dict) -> Optional[dict]:
    """Builds the user reply action for an existing support thread."""
    thread_id = ctx.get('support_thread_id')
    if isinstance(thread_id, bool) or not isinstance(thread_id, (int, str)):
        logger.warning("Invalid support_thread_id for system button: %r", thread_id)
        return None

    normalized = str(thread_id).strip()
    if not normalized.isdigit() or int(normalized) <= 0:
        logger.warning("Invalid support_thread_id for system button: %r", thread_id)
        return None

    return {"callback_data": f"support_reply:{normalized}"}


def _intent_order_id(ctx: dict) -> str | None:
    value = ctx.get("order_id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _resolve_intent_provider(ctx: dict, provider_id: str) -> Optional[dict]:
    """Builds a Payment Intent provider callback while keeping its label page-owned."""
    order_id = _intent_order_id(ctx)
    provider_ids = ctx.get("payment_provider_ids")
    if not order_id or not isinstance(provider_ids, (list, tuple, set, frozenset)):
        return None
    if provider_id not in {str(value) for value in provider_ids}:
        return None
    return {"callback_data": f"payment_intent_provider:{order_id}:{provider_id}"}


def _resolve_intent_balance(ctx: dict) -> Optional[dict]:
    order_id = _intent_order_id(ctx)
    if not order_id or ctx.get("payment_allow_balance") is not True:
        return None
    return {"callback_data": f"payment_intent_balance:{order_id}"}


def _resolve_intent_promo(ctx: dict) -> Optional[dict]:
    order_id = _intent_order_id(ctx)
    if not order_id:
        return None
    return {"callback_data": f"promo_enter_order:{order_id}"}


def _resolve_intent_cancel(ctx: dict) -> Optional[dict]:
    override = ctx.get("payment_cancel_callback")
    if isinstance(override, str) and override.strip():
        return {"callback_data": override.strip()}
    order_id = _intent_order_id(ctx)
    if not order_id:
        return None
    return {"callback_data": f"payment_intent_cancel:{order_id}"}


def _resolve_intent_methods(ctx: dict) -> Optional[dict]:
    override = ctx.get("payment_methods_callback")
    if isinstance(override, str) and override.strip():
        return {"callback_data": override.strip()}
    order_id = _intent_order_id(ctx)
    if not order_id:
        return None
    return {"callback_data": f"payment_intent_methods:{order_id}"}


def _resolve_intent_open(ctx: dict) -> Optional[dict]:
    value = ctx.get("payment_url")
    if not isinstance(value, str) or not value.strip():
        return None
    return {"url": value.strip()}


def _resolve_intent_check(ctx: dict) -> Optional[dict]:
    override = ctx.get("payment_check_callback")
    if isinstance(override, str) and override.strip():
        return {"callback_data": override.strip()}
    order_id = _intent_order_id(ctx)
    if not order_id or ctx.get("payment_can_check") is not True:
        return None
    return {"callback_data": f"payment_intent_check:{order_id}"}


def _resolve_promo_return(ctx: dict) -> Optional[dict]:
    value = ctx.get("promo_return_callback")
    if not isinstance(value, str) or not value.strip():
        return None
    return {"callback_data": value.strip()}


def _resolve_context_callback(ctx: dict, context_key: str) -> Optional[dict]:
    value = ctx.get(context_key)
    if not isinstance(value, str) or not value.strip():
        return None
    return {"callback_data": value.strip()}


# Map: button_id → handler
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
    "btn_support_reply": _resolve_support_reply,
    "btn_intent_provider_crypto": lambda ctx: _resolve_intent_provider(ctx, "crypto"),
    "btn_intent_provider_stars": lambda ctx: _resolve_intent_provider(ctx, "stars"),
    "btn_intent_provider_cards": lambda ctx: _resolve_intent_provider(ctx, "cards"),
    "btn_intent_provider_yookassa_qr": lambda ctx: _resolve_intent_provider(ctx, "yookassa_qr"),
    "btn_intent_provider_wata": lambda ctx: _resolve_intent_provider(ctx, "wata"),
    "btn_intent_provider_platega": lambda ctx: _resolve_intent_provider(ctx, "platega"),
    "btn_intent_provider_cardlink": lambda ctx: _resolve_intent_provider(ctx, "cardlink"),
    "btn_intent_provider_demo": lambda ctx: _resolve_intent_provider(ctx, "demo"),
    "btn_intent_balance": _resolve_intent_balance,
    "btn_intent_promo": _resolve_intent_promo,
    "btn_intent_cancel": _resolve_intent_cancel,
    "btn_intent_methods": _resolve_intent_methods,
    "btn_intent_open": _resolve_intent_open,
    "btn_intent_check": _resolve_intent_check,
    "btn_promo_return": _resolve_promo_return,
    "btn_tariff_back": lambda ctx: _resolve_context_callback(ctx, "tariff_back_callback"),
    "btn_key_flow_back": lambda ctx: _resolve_context_callback(ctx, "key_flow_back_callback"),
    "btn_key_flow_confirm": lambda ctx: _resolve_context_callback(ctx, "key_flow_confirm_callback"),
}


def _resolve_context_collection(context_key: str, context: dict) -> list[dict]:
    """Return action/data records for one page-owned repeatable button template."""
    raw_items = context.get(context_key) or []
    if not isinstance(raw_items, list):
        raise ValueError(f"context.{context_key} must be a list")
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


SYSTEM_COLLECTIONS: Dict[str, Callable[[dict], list[dict]]] = {
    "btn_tariff_items": lambda ctx: _resolve_context_collection("tariff_button_items", ctx),
    "btn_server_items": lambda ctx: _resolve_context_collection("server_button_items", ctx),
    "btn_protocol_items": lambda ctx: _resolve_context_collection("protocol_button_items", ctx),
    "btn_key_items": lambda ctx: _resolve_context_collection("key_button_items", ctx),
    "btn_tariff_group_items": lambda ctx: _resolve_context_collection("tariff_group_button_items", ctx),
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


def resolve_system_collection(button_id: str, context: Mapping[str, Any]) -> list[dict]:
    """Resolve a registered repeatable page button without supplying its label."""
    button_id = _require_text(button_id, 'button_id')
    if not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")
    handler = SYSTEM_COLLECTIONS.get(button_id)
    if handler is None:
        return []

    resolved: list[dict] = []
    for index, raw_item in enumerate(handler(dict(context))):
        allowed = {'callback_data', 'url', 'data', 'hidden', 'row', 'col'}
        unknown = set(raw_item) - allowed
        if unknown:
            raise ValueError(
                f"system collection '{button_id}' item {index} has unsupported fields: "
                f"{', '.join(sorted(unknown))}"
            )
        item = dict(raw_item)
        data = item.get('data') or {}
        if not isinstance(data, Mapping):
            raise ValueError(f"system collection '{button_id}' item {index} data must be a mapping")
        item['data'] = dict(data)
        if item.get('hidden') is not None and not isinstance(item['hidden'], bool):
            raise ValueError(f"system collection '{button_id}' item {index} hidden must be bool")
        for position in ('row', 'col'):
            if item.get(position) is not None and (
                isinstance(item[position], bool) or not isinstance(item[position], int)
            ):
                raise ValueError(
                    f"system collection '{button_id}' item {index} {position} must be int"
                )
        callback_data = item.get('callback_data')
        url = item.get('url')
        if callback_data and url:
            raise ValueError(
                f"system collection '{button_id}' item {index} cannot have callback_data and url"
            )
        if callback_data:
            item['callback_data'] = normalize_callback_data(
                str(callback_data),
                f"system collection '{button_id}' item {index} callback_data",
            )
        if url is not None and not isinstance(url, str):
            raise ValueError(f"system collection '{button_id}' item {index} url must be a string")
        resolved.append(item)
    return resolved


def _normalize_system_button_result(button_id: str, result: Any) -> Optional[dict]:
    """Checks the system handler's contract before passing it to the renderer."""
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
