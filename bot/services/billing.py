"""
Billing service - payment processing.

Verification of signatures, creation/renewal of keys after payment.
Creating QR payments via YuKassa REST API.
Referral accruals.
"""
import hmac
import hashlib
import logging
import uuid
import base64
import aiohttp
import qrcode
import io
import math
import asyncio
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, Tuple

from database.requests import (
    find_order_by_order_id,
    get_setting,
    get_yookassa_credentials, get_wata_token, get_platega_credentials,
    get_cardlink_credentials,
    is_referral_enabled, get_referral_reward_type, get_active_referral_levels,
    get_user_referrer, get_user_referral_coefficient,
)
from bot.services.payment_api import (
    PaymentApiRateLimitError,
    PaymentApiResponseError,
    PaymentApiTransientError,
    payment_client_timeout,
    run_payment_api_operation,
)
from bot.utils.telegram_links import build_telegram_link

logger = logging.getLogger(__name__)

YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
WATA_API_URL = "https://api.wata.pro/api/h2h"
PLATEGA_API_URL = "https://app.platega.io"
CARDLINK_API_URL = "https://cardlink.link"
_payment_order_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Alphabet for Base62 encoding
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _payment_configuration_error(provider: str, details: str) -> ValueError:
    logger.error(
        "Payment API configuration error: provider=%s error=%s",
        provider,
        details,
    )
    return ValueError(details)


def _payment_contract_error(
    provider: str,
    operation: str,
    order_id: str,
    details: str,
) -> PaymentApiResponseError:
    logger.error(
        "Payment API contract error: provider=%s operation=%s order=%s error=%s",
        provider,
        operation,
        order_id,
        details,
    )
    return PaymentApiResponseError(details)


async def _payment_api_json_request(
    *,
    provider: str,
    operation: str,
    order_id: str,
    method: str,
    url: str,
    expected_statuses: tuple[int, ...],
    retry: bool,
    headers: Optional[Dict[str, str]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    data: Any = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Executes one JSON payment API request under the shared reliability policy."""

    async def _request_once() -> Any:
        async with aiohttp.ClientSession(timeout=payment_client_timeout()) as session:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_payload,
                data=data,
                params=params,
            ) as response:
                if response.status >= 500:
                    raise PaymentApiTransientError(f'HTTP {response.status}')
                if response.status == 429:
                    raise PaymentApiRateLimitError('HTTP 429')
                try:
                    response_data = await response.json(content_type=None)
                except Exception as error:
                    raise PaymentApiResponseError(
                        f'Некорректный JSON-ответ HTTP {response.status}'
                    ) from error
                if response.status not in expected_statuses:
                    error_text = 'Неизвестная ошибка'
                    if isinstance(response_data, dict):
                        raw_error = response_data.get('error')
                        if isinstance(raw_error, dict):
                            raw_error = raw_error.get('description') or raw_error.get('code')
                        error_text = str(
                            response_data.get('message')
                            or response_data.get('description')
                            or raw_error
                            or error_text
                        )
                    raise PaymentApiResponseError(
                        f'HTTP {response.status}: {error_text}'
                    )
                return response_data

    return await run_payment_api_operation(
        provider=provider,
        operation=operation,
        order_id=order_id,
        call=_request_once,
        retry=retry,
    )


def build_payment_return_url(bot_name: str, provider: str, order_id: str) -> str:
    """
    Generates a deep-link return from an external payment form to the bot.

    The start parameter format is the same for QR providers:
    pay_{provider}_{order_id}
    """
    from database.requests import get_payment_order_terms
    terms = get_payment_order_terms(order_id)
    if terms and terms['source'] in {'site', 'mini_app'}:
        from urllib.parse import quote
        from web_api.settings import get_web_settings
        origin = get_web_settings().public_origin
        if not origin:
            raise ValueError('Web payment return origin is not configured')
        return f'{origin}/orders/{quote(str(order_id), safe="")}'
    if not bot_name:
        return build_telegram_link()

    provider_code = str(provider or '').strip().lower()
    order_code = str(order_id or '').strip()
    if not provider_code or not order_code:
        return build_telegram_link(bot_name)

    return build_telegram_link(bot_name, f"pay_{provider_code}_{order_code}")




def encode_base62(data: bytes) -> str:
    """
    Encodes binary data in Base62.
    
    Used to generate a callback signature from Ya.Seller.
    
    Args:
        data: Binary data
        
    Returns:
        Base62 string
    """
    if not data:
        return ""
    
    num = int.from_bytes(data, 'big')
    if num == 0:
        return "0"
    
    res = []
    while num > 0:
        num, rem = divmod(num, 62)
        res.append(ALPHABET[rem])
    
    return "".join(reversed(res))


def verify_crypto_signature(data_part: str, received_signature: str, secret_key: str) -> bool:
    """
    Verifies the callback signature from Ya.Seller crypto processing.
    
    Signature = Base62(HMAC-SHA256(data_part, secret_key)[:11]).
    
    Algorithm according to documentation https://yadreno.ru/seller/integration.php:
    1. Calculate HMAC-SHA256 from data_part with secret key
    2. Take the first 11 bytes of the binary result
    3. Encode in Base62
    
    Args:
        data_part: All segments except the last one (for example bill1-aZ1-bY-1-_-1000)
        received_signature: Received signature (last segment)
        secret_key: Seller's secret key
        
    Returns:
        True if the signature is valid
    """
    # Calculating HMAC-SHA256
    h = hmac.new(
        secret_key.encode('utf-8'),
        data_part.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Take the first 11 bytes and encode them in Base62
    truncated = h[:11]
    expected = encode_base62(truncated)
    
    # Comparing signatures
    is_valid = hmac.compare_digest(expected, received_signature)
    
    if not is_valid:
        logger.warning(f"Неверная подпись! expected={expected}, received={received_signature}")
    
    return is_valid


def parse_crypto_callback(start_param: str) -> Optional[Dict[str, Any]]:
    """
    Parses the start parameter from the cryptoprocessing callback.
    
    Format: bill1-ORDER_ID-ITEM_ID-TARIFF-PROMO-PRICE-SIGNATURE
    
    Args:
        start_param: The value of the start parameter from the deep link
        
    Returns:
        Dictionary with fields: order_id, item_id, tariff, promo, price, signature, data_part
        or None if the format is invalid
    """
    if not start_param or not start_param.startswith('bill1-'):
        return None
    
    parts = start_param.split('-')
    
    # Minimum: bill1-ORDER_ID-ITEM_ID-TARIFF-PROMO-PRICE-SIGNATURE (7 parts)
    if len(parts) < 7:
        logger.warning(f"Неверный формат callback: {start_param} (частей: {len(parts)})")
        return None
    
    try:
        # The last part is the signature
        signature = parts[-1]
        # The rest is data for verifying the signature
        data_part = start_param.rsplit('-', 1)[0]
        
        return {
            'prefix': parts[0],        # Payment Intent v1
            'order_id': parts[1],      # our invoice_id
            'item_id': parts[2],       # Product ID in Ya.Seller
            'tariff': parts[3],        # tariff number (1-9) or '_'
            'promo': parts[4],         # promotional code or '_'
            'price': int(parts[5]) if parts[5] != '_' else 0,  # price in cents
            'signature': signature,
            'data_part': data_part
        }
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка парсинга callback: {e}")
        return None


def _mark_order_runtime_flags(
    order: Optional[Dict[str, Any]],
    *,
    processed_now: bool,
) -> Optional[Dict[str, Any]]:
    """Adds service flags that are not saved in the database."""
    if order is not None:
        order['_payment_processed_now'] = processed_now
    return order


async def process_payment_order(
    order_id: str,
    bot: Optional[Any] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    async with _payment_order_locks[order_id]:
        return await _process_payment_order_unlocked(
            order_id,
            bot=bot,
        )


async def _process_payment_order_unlocked(
    order_id: str,
    bot: Optional[Any] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Fulfill one provider-confirmed Payment Intent v1."""
    order = find_order_by_order_id(order_id)
    if not order:
        logger.warning(f"Ордер не найден: {order_id}")
        return False, "order_not_found", None
    if int(order.get('intent_version') or 0) != 1:
        logger.warning('Rejected non-v1 payment completion order=%s', order_id)
        return False, 'payment_intent_required', order

    from bot.services.payment_fulfillment import fulfill_payment_intent

    result = await fulfill_payment_intent(
        order_id,
        bot=bot,
        process_referrals=True,
    )
    fresh_order = find_order_by_order_id(order_id) or order
    _mark_order_runtime_flags(
        fresh_order,
        processed_now=bool(result.completed and not result.already_completed),
    )
    return bool(result.completed), result.message, fresh_order


async def process_crypto_payment(
    start_param: str,
    user_id: Optional[int] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Processes payment from cryptoprocessing (parse + verify + confirm).
    """
    # Parse callback
    parsed = parse_crypto_callback(start_param)
    if not parsed:
        return False, "invalid_payload", None
    
    # Getting the secret key
    secret_key = get_setting('crypto_secret_key')
    if not secret_key:
        logger.error("Секретный ключ криптопроцессинга не настроен!")
        return False, "configuration_error", None
    
    # Checking the signature
    if not verify_crypto_signature(parsed['data_part'], parsed['signature'], secret_key):
        return False, "invalid_signature", None
    
    order_id = parsed['order_id']
    
    order = find_order_by_order_id(order_id)
    if not order or int(order.get('intent_version') or 0) != 1:
        return False, "order_not_found", None
    if user_id is not None and int(order.get('user_id') or 0) != int(user_id):
        return False, "wrong_owner", None

    from database.requests import (
        get_payment_provider_order,
        update_payment_provider_order_status,
    )

    provider_order = get_payment_provider_order(order_id)
    if not provider_order or str(provider_order.get('provider_id') or '') != 'crypto':
        return False, 'provider_mismatch', None
    try:
        expected_cents = int(
            (Decimal(str(order.get('charge_amount') or '0')) * Decimal('100'))
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
    except (ArithmeticError, ValueError):
        return False, 'amount_snapshot_missing', None
    received_cents = int(parsed.get('price') or 0)
    if expected_cents <= 0 or received_cents < expected_cents:
        logger.error(
            "Crypto payment amount mismatch order=%s expected=%s received=%s",
            order_id,
            expected_cents,
            received_cents,
        )
        return False, "amount_mismatch", None
    update_payment_provider_order_status(order_id, 'succeeded')
    
    # Signature/provider confirmation ends here. Closing the order and every
    # continuation is owned by complete_confirmed_payment at the entry point.
    return True, "payment_confirmed", order


def build_crypto_payment_url(
    item_id: str,
    invoice_id: str,
    price_cents: Optional[int] = None
) -> str:
    """
    Generates a link to cryptoprocessing with our invoice.
    
    Format: https://<telegram_link_domain>/Ya_SellerBot?start=item-{item_id}-{ref}-{promo}-{invoice}-{price}
    
    Args:
        item_id: Product ID in Ya.Seller (from settings)
        invoice_id: Our unique invoice (max 8 characters)
        price_cents: Price in cents (if needed to be overridden)
        
    Returns:
        URL to go to crypto processing
    """
    # Format: item-{item_id}-{ref_code}-{promo}-{invoice}-{price}
    # Replace empty parameters with dashes
    
    ref_code = ""  # We don’t use referrals
    promo = ""     # We do not use a promotional code
    
    parts = [
        "item",
        item_id,
        ref_code,
        promo,
        invoice_id
    ]
    
    # We add a price if you need to fix it
    if price_cents:
        parts.append(str(price_cents))
    
    start_param = "-".join(parts)
    
    return build_telegram_link("Ya_SellerBot", start_param)


def extract_item_id_from_url(crypto_item_url: str) -> Optional[str]:
    """
    Retrieves item_id from a product link in Ya.Seller.
    
    Link format: https://<telegram_link_domain>/Ya_SellerBot?start=item-{item_id}...
    
    Args:
        crypto_item_url: Full link to the product
        
    Returns:
        item_id or None
    """
    if not crypto_item_url:
        return None
    
    # We are looking for the start= parameter
    if '?start=' in crypto_item_url:
        start_param = crypto_item_url.split('?start=')[1]
        parts = start_param.split('-')
        if len(parts) >= 2 and parts[0] == 'item':
            return parts[1]
    
    return None


# ============================================================================
# YUKASSA QR-PAYMENT (direct REST API without Telegram Payments)
# ============================================================================

async def create_yookassa_qr_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Creates a payment in YuKassa REST API with confirmation via a QR code.

    Returns a QR code (PNG) image from a link that can be
    send to the user directly in Telegram as a photo.

    Args:
        amount_rub: Amount in rubles (for example, 299.00)
        order_id: Our internal order (for metadata)
        description: Description of the payment (shown in the payment form)
        metadata: Additional metadata (optional)

    Returns:
        Dictionary with keys:
            - yookassa_payment_id: Payment ID in the Yookassa system
            - qr_image_url: QR code image URL (PNG)
            - qr_url: Link embedded in QR (to be opened in a browser)

    Raises:
        ValueError: If credentials are not configured
        aiohttp.ClientError: If the API is not available
        RuntimeError: If the API returned an error
    """
    shop_id, secret_key = get_yookassa_credentials()
    if not shop_id or not secret_key:
        raise _payment_configuration_error(
            'yookassa',
            'ЮКасса: не настроены shop_id или secret_key',
        )

    # Basic Auth header: base64(shop_id:secret_key)
    credentials = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()

    # Idempotency key - unique for this order
    idempotence_key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'yadrenovpn-yookassa:' + order_id))
    return_url = build_payment_return_url(bot_name, 'yookassa', order_id)

    payload = {
        "amount": {
            "value": f"{amount_rub:.2f}",
            "currency": "RUB"
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url
        },
        "description": description,
        "receipt": {
            "customer": {
                "email": f"user_{order_id}@t.me"
            },
            "items": [
                {
                    "description": description[:128],
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{amount_rub:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",
                    "payment_subject": "service"
                }
            ]
        },
        "metadata": {
            "order_id": order_id,
            **(metadata or {})
        }
    }

    headers = {
        "Authorization": f"Basic {credentials}",
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json"
    }

    data = await _payment_api_json_request(
        provider='yookassa',
        operation='create',
        order_id=order_id,
        method='POST',
        url=YOOKASSA_API_URL,
        expected_statuses=(200, 201),
        retry=True,
        headers=headers,
        json_payload=payload,
    )
    confirmation = data.get('confirmation', {}) if isinstance(data, dict) else {}
    qr_url = confirmation.get('confirmation_url', '')
    payment_id = data.get('id') if isinstance(data, dict) else None
    if not payment_id or not qr_url:
        raise _payment_contract_error(
            'yookassa',
            'create',
            order_id,
            'ЮКасса API не вернул id или confirmation_url',
        )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    qr_image_data = bio.getvalue()

    logger.info(
        "ЮКасса QR создан: payment_id=%s, order_id=%s, amount=%s RUB",
        payment_id,
        order_id,
        amount_rub,
    )
    return {
        'yookassa_payment_id': payment_id,
        'qr_image_data': qr_image_data,
        'qr_url': qr_url,
        'status': data.get('status', 'pending'),
    }


async def check_yookassa_payment_status(
    yookassa_payment_id: str,
    *,
    order_id: Optional[str] = None,
) -> str:
    """
    Checks the payment status in YuKassa REST API.

    Args:
        yookassa_payment_id: Payment ID in the Yookassa system

    Returns:
        Status line: 'pending', 'waiting_for_capture', 'succeeded', 'canceled'

    Raises:
        ValueError: If credentials are not configured
        aiohttp.ClientError: If the API is not available
        RuntimeError: If the API returned an error
    """
    shop_id, secret_key = get_yookassa_credentials()
    if not shop_id or not secret_key:
        raise _payment_configuration_error(
            'yookassa',
            'ЮКасса: не настроены shop_id или secret_key',
        )

    credentials = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json"
    }

    url = f"{YOOKASSA_API_URL}/{yookassa_payment_id}"

    data = await _payment_api_json_request(
        provider='yookassa',
        operation='check',
        order_id=order_id or yookassa_payment_id,
        method='GET',
        url=url,
        expected_statuses=(200,),
        retry=True,
        headers=headers,
    )
    if not isinstance(data, dict) or not data.get('status'):
        raise _payment_contract_error(
            'yookassa',
            'check',
            order_id or yookassa_payment_id,
            'ЮКасса API не вернул статус платежа',
        )
    status = data['status']
    logger.debug("ЮКасса payment %s: status=%s", yookassa_payment_id, status)
    return status


# ============================================================================
# WATA - payment by card/SBP via REST API (https://wata.pro/api)
# ============================================================================

async def create_wata_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str
) -> Dict[str, Any]:
    """
    Creates a payment link in WATA via the H2H API.

    POST https://api.wata.pro/api/h2h/links/

    Args:
        amount_rub: Amount in rubles
        order_id: Our internal order_id
        description: Description of the payment
        bot_name: Username of the bot (for building successRedirectUrl)

    Returns:
        Dictionary with keys:
            - wata_link_id: Link ID in the WATA system
            - qr_image_data: PNG bytes of the QR code
            - qr_url: Link for payment (cards/SBP)
            - status: Payment status

    Raises:
        ValueError: If the JWT token is not configured
        RuntimeError: If the API returned an error
    """
    token = get_wata_token()
    if not token:
        raise _payment_configuration_error('wata', 'WATA: JWT-токен не настроен')

    return_url = build_payment_return_url(bot_name, 'wata', order_id)

    payload = {
        "amount": round(float(amount_rub), 2),
        "currency": "RUB",
        "description": description[:255],
        "orderId": order_id,
        "successRedirectUrl": return_url,
        "failRedirectUrl": return_url,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{WATA_API_URL}/links/"

    data = await _payment_api_json_request(
        provider='wata',
        operation='create',
        order_id=order_id,
        method='POST',
        url=url,
        expected_statuses=(200, 201),
        retry=False,
        headers=headers,
        json_payload=payload,
    )
    if not isinstance(data, dict):
        raise _payment_contract_error(
            'wata', 'create', order_id,
            'WATA API вернул ответ неверного типа',
        )
    wata_link_id = data.get('id') or data.get('linkId') or data.get('uuid')
    qr_url = data.get('url') or data.get('paymentUrl')
    if not wata_link_id or not qr_url:
        raise _payment_contract_error(
            'wata', 'create', order_id,
            'WATA API не вернул id или URL платёжной ссылки',
        )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    qr_image_data = bio.getvalue()
    logger.info(
        "WATA ссылка создана: link_id=%s, order_id=%s, amount=%s RUB",
        wata_link_id,
        order_id,
        amount_rub,
    )
    return {
        'wata_link_id': str(wata_link_id),
        'qr_image_data': qr_image_data,
        'qr_url': qr_url,
        'status': str(data.get('status', 'Created')).lower(),
    }


async def check_wata_payment_status(
    wata_link_id: str,
    *,
    order_id: Optional[str] = None,
) -> str:
    """
    Checks the status of the WATA payment link by its ID.

    GET https://api.wata.pro/api/h2h/links/{wata_link_id}

    Endpoint /transactions/?orderId= does not work (404).
    Instead, we check the status of the link itself via /links/{id}.

    WATA has a limit - no more than one request per 30 seconds.
    Request rate control is performed on the handler side.

    Args:
        wata_link_id: WATA link ID (UUID)

    Returns:
        Normalized status: 'pending' | 'succeeded' | 'cancelled'

    Raises:
        ValueError: If the JWT token is not configured
        RuntimeError: If the API returned an error
    """
    token = get_wata_token()
    if not token:
        raise _payment_configuration_error('wata', 'WATA: JWT-токен не настроен')

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = f"{WATA_API_URL}/links/{wata_link_id}"

    data = await _payment_api_json_request(
        provider='wata',
        operation='check',
        order_id=order_id or wata_link_id,
        method='GET',
        url=url,
        expected_statuses=(200,),
        retry=True,
        headers=headers,
    )
    if not isinstance(data, dict) or not data.get('status'):
        raise _payment_contract_error(
            'wata', 'check', order_id or wata_link_id,
            'WATA API не вернул статус платежа',
        )
    status = str(data['status']).lower()
    logger.debug("WATA link %s: status=%s", wata_link_id, status)
    if status in ('closed', 'paid'):
        return 'succeeded'
    if status in ('declined', 'expired', 'canceled', 'cancelled'):
        return 'canceled'
    return 'pending'


# ============================================================================
# PLATEGA - payment form without a specified method (https://app.platega.io)
# ============================================================================

async def create_platega_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str,
    user_telegram_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Creates a transaction in the Platega API.

    POST https://app.platega.io/v2/transaction/process

    Args:
        amount_rub: Amount in rubles
        order_id: Our internal order_id
        description: Description of the payment
        bot_name: Username of the bot (for building return)
        user_telegram_id: Telegram payer ID for metadata.userId

    Returns:
        Dictionary with keys:
            - platega_transaction_id: Transaction ID in the Platega system
            - qr_image_data: PNG bytes of the QR code
            - qr_url: Payment link
            - status: Payment status

    Raises:
        ValueError: If credentials are not configured
        RuntimeError: If the API returned an error
    """
    merchant_id, secret = get_platega_credentials()
    if not merchant_id or not secret:
        raise _payment_configuration_error(
            'platega',
            'Platega: не настроены merchant_id или secret',
        )

    return_url = build_payment_return_url(bot_name, 'platega', order_id)
    fail_url = return_url

    payload = {
        "paymentDetails": {
            "amount": round(float(amount_rub), 2),
            "currency": "RUB",
        },
        "description": description[:255],
        "return": return_url,
        "failedUrl": fail_url,
        "payload": order_id,
    }
    if user_telegram_id is not None:
        payload["metadata"] = {
            "userId": str(user_telegram_id),
        }

    headers = {
        "X-MerchantId": merchant_id,
        "X-Secret": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{PLATEGA_API_URL}/v2/transaction/process"

    data = await _payment_api_json_request(
        provider='platega',
        operation='create',
        order_id=order_id,
        method='POST',
        url=url,
        expected_statuses=(200, 201),
        retry=False,
        headers=headers,
        json_payload=payload,
    )
    if not isinstance(data, dict):
        raise _payment_contract_error(
            'platega', 'create', order_id,
            'Platega API вернул ответ неверного типа',
        )
    transaction_id = data.get('id') or data.get('transactionId') or data.get('uuid')
    qr_url = (
        data.get('redirect') or data.get('redirectUrl') or
        data.get('url') or data.get('paymentUrl')
    )
    if not transaction_id or not qr_url:
        raise _payment_contract_error(
            'platega', 'create', order_id,
            'Platega API не вернул id или URL платёжной ссылки',
        )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    qr_image_data = bio.getvalue()
    logger.info(
        "Platega транзакция создана: id=%s, order_id=%s, amount=%s RUB",
        transaction_id,
        order_id,
        amount_rub,
    )
    return {
        'platega_transaction_id': str(transaction_id),
        'qr_image_data': qr_image_data,
        'qr_url': qr_url,
        'status': str(data.get('status', 'PENDING')).upper(),
    }


async def check_platega_payment_status(
    transaction_id: str,
    *,
    order_id: Optional[str] = None,
) -> str:
    """
    Checks the status of a Platega transaction.

    GET https://app.platega.io/transaction/{transaction_id}

    Platega statuses:
        - PENDING: in the process of payment
        - CONFIRMED: successfully paid
        - CANCELED: canceled
        - CHARGEBACKED: returnable

    Args:
        transaction_id: Transaction ID in the Platega system

    Returns:
        Normalized status: 'pending' | 'succeeded' | 'cancelled'

    Raises:
        ValueError: If credentials are not configured
        RuntimeError: If the API returned an error
    """
    merchant_id, secret = get_platega_credentials()
    if not merchant_id or not secret:
        raise _payment_configuration_error(
            'platega',
            'Platega: не настроены merchant_id или secret',
        )

    headers = {
        "X-MerchantId": merchant_id,
        "X-Secret": secret,
        "Accept": "application/json",
    }

    url = f"{PLATEGA_API_URL}/transaction/{transaction_id}"

    data = await _payment_api_json_request(
        provider='platega',
        operation='check',
        order_id=order_id or transaction_id,
        method='GET',
        url=url,
        expected_statuses=(200,),
        retry=True,
        headers=headers,
    )
    if not isinstance(data, dict) or not data.get('status'):
        raise _payment_contract_error(
            'platega', 'check', order_id or transaction_id,
            'Platega API не вернул статус платежа',
        )
    status = str(data['status']).upper()
    logger.debug("Platega transaction %s: status=%s", transaction_id, status)
    if status == 'CONFIRMED':
        return 'succeeded'
    if status in ('CANCELED', 'CANCELLED', 'CHARGEBACKED'):
        return 'canceled'
    return 'pending'


# ============================================================================
# CARDLINK - payment by Card/SBP via REST API (https://cardlink.link)
# ============================================================================

async def create_cardlink_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str
) -> Dict[str, Any]:
    """
    Creates a bill in the Cardlink API.

    POST https://cardlink.link/api/v1/bill/create

    The body is sent as application/x-www-form-urlencoded.
    Authorization via Bearer token.

    Instead of a webhook, every invoice receives an order-bound Payment
    Intent v1 return link. The returned order is checked through the same
    provider adapter as the manual status action.

    Args:
        amount_rub: Amount in rubles
        order_id: Our internal order_id
        description: Payment description (not used by API, but logged)
        bot_name: Username of the bot (to build success_url/fail_url)

    Returns:
        Dictionary with keys:
            - cardlink_bill_id: Account ID in the Cardlink system
            - qr_image_data: PNG bytes of the QR code
            - qr_url: Link to the payment page
            - status: Payment status

    Raises:
        ValueError: If credentials are not configured
        RuntimeError: If the API returned an error
    """
    shop_id, api_token = get_cardlink_credentials()
    if not shop_id or not api_token:
        raise _payment_configuration_error(
            'cardlink',
            'Cardlink: не настроены shop_id или api_token',
        )

    form = aiohttp.FormData()
    form.add_field("shop_id", shop_id)
    form.add_field("amount", f"{float(amount_rub):.2f}")
    form.add_field("order_id", order_id)
    form.add_field("currency_in", "RUB")
    form.add_field("type", "normal")
    form.add_field("description", description[:255])
    form.add_field("name", description[:100])
    return_url = build_payment_return_url(bot_name, 'cardlink', order_id)
    form.add_field("return_url", return_url)
    form.add_field("success_url", return_url)
    form.add_field("fail_url", return_url)
    form.add_field("partner_uuid", "6e7e8f22-3410-4224-8b9c-e61430705963")

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }

    url = f"{CARDLINK_API_URL}/api/v1/bill/create"

    data = await _payment_api_json_request(
        provider='cardlink',
        operation='create',
        order_id=order_id,
        method='POST',
        url=url,
        expected_statuses=(200, 201),
        retry=False,
        headers=headers,
        data=form,
    )
    nested = data.get('success') if isinstance(data, dict) else None
    payload = nested if isinstance(nested, dict) else data
    bill_id = (
        payload.get('bill_id') or payload.get('id') or payload.get('uuid')
        if isinstance(payload, dict) else None
    )
    qr_url = (
        payload.get('link_page_url') or payload.get('url') or payload.get('payment_url')
        if isinstance(payload, dict) else None
    )
    if not bill_id or not qr_url:
        raise _payment_contract_error(
            'cardlink', 'create', order_id,
            'Cardlink API не вернул bill_id или URL',
        )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    qr_image_data = bio.getvalue()
    logger.info(
        "Cardlink счёт создан: bill_id=%s, order_id=%s, amount=%s RUB",
        bill_id,
        order_id,
        amount_rub,
    )
    return {
        'cardlink_bill_id': str(bill_id),
        'qr_image_data': qr_image_data,
        'qr_url': qr_url,
        'status': str(payload.get('status', 'NEW')).upper() if isinstance(payload, dict) else 'NEW',
    }


async def check_cardlink_payment_status(
    bill_id: str,
    *,
    order_id: Optional[str] = None,
) -> str:
    """
    Checks Cardlink account status.

    GET https://cardlink.link/api/v1/bill/status?id={bill_id}

    Cardlink statuses:
        - NEW / PROCESS / UNDERPAID: in progress
        - SUCCESS / OVERPAID: successfully paid
        - FAIL: canceled / unsuccessful

    Args:
        bill_id: Account ID in the Cardlink system

    Returns:
        Normalized status: 'pending' | 'succeeded' | 'cancelled'

    Raises:
        ValueError: If credentials are not configured
        RuntimeError: If the API returned an error
    """
    shop_id, api_token = get_cardlink_credentials()
    if not shop_id or not api_token:
        raise _payment_configuration_error(
            'cardlink',
            'Cardlink: не настроены shop_id или api_token',
        )

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }

    url = f"{CARDLINK_API_URL}/api/v1/bill/status"
    params = {"id": bill_id}

    data = await _payment_api_json_request(
        provider='cardlink',
        operation='check',
        order_id=order_id or bill_id,
        method='GET',
        url=url,
        expected_statuses=(200,),
        retry=True,
        headers=headers,
        params=params,
    )
    nested = data.get('success') if isinstance(data, dict) else None
    payload = nested if isinstance(nested, dict) else data
    if not isinstance(payload, dict) or not payload.get('status'):
        raise _payment_contract_error(
            'cardlink', 'check', order_id or bill_id,
            'Cardlink API не вернул статус платежа',
        )
    status = str(payload['status']).upper()
    logger.debug("Cardlink bill %s: status=%s", bill_id, status)
    if status in ('SUCCESS', 'OVERPAID'):
        return 'succeeded'
    if status == 'FAIL':
        return 'canceled'
    return 'pending'


async def process_referral_reward(
    payer_id: int,
    period_days: int,
    amount_minor: int,
    payment_type: str,
    *,
    order: Dict[str, Any],
    bot: Optional[Any] = None,
) -> list[Dict[str, Any]]:
    """Apply referral rewards for one paid Payment Intent v1."""
    if int(order.get('intent_version') or 0) != 1:
        raise ValueError('Referral rewards require Payment Intent v1')
    if payment_type in ('balance', 'trial', 'promo_free') or amount_minor <= 0:
        return []

    if not is_referral_enabled():
        return []
    
    reward_type = get_referral_reward_type()
    active_levels = dict(get_active_referral_levels())
    
    if not active_levels:
        return []
    
    amount_base_minor = int(order.get('payable_amount_minor') or 0)
    base_currency = str(order.get('base_currency') or 'RUB').upper()
    
    current_user_id = payer_id
    events = []
    payment_order_id = str(order.get('order_id') or '')
    if not payment_order_id:
        raise ValueError('Payment Intent order_id is required')
    
    for level_num in (1, 2, 3):
        referrer_id = get_user_referrer(current_user_id)
        if not referrer_id:
            break

        percent = active_levels.get(level_num)
        if percent is None:
            current_user_id = referrer_id
            continue
        
        coefficient = get_user_referral_coefficient(referrer_id)
        
        if reward_type == 'balance':
            base_reward = (
                Decimal(amount_base_minor)
                * Decimal(str(percent))
                / Decimal('100')
            )
            final_reward = int(
                (base_reward * Decimal(str(coefficient))).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
            reward_days = 0
        else:
            base_days = period_days * (percent / 100)
            final_days = base_days * coefficient
            reward_days = math.ceil(final_days)
            final_reward = 0

        try:
            from bot.utils.policy_registry import apply_referral_reward_policies

            reward_decision = apply_referral_reward_policies(
                {
                    'reward_cents': final_reward,
                    'reward_days': reward_days,
                },
                {
                    'payer_id': payer_id,
                    'referrer_id': referrer_id,
                    'level': level_num,
                    'reward_type': reward_type,
                    'period_days': period_days,
                    'amount_raw': amount_minor,
                    'base_currency': base_currency,
                    'amount_base_minor': amount_base_minor,
                    'amount_rub_cents': amount_base_minor,
                    'payment_type': payment_type,
                    'percent': percent,
                    'coefficient': coefficient,
                    'order': dict(order),
                },
            )
            final_reward = int(reward_decision.get('reward_cents') or 0)
            reward_days = int(reward_decision.get('reward_days') or 0)
            reward_policies = reward_decision.get('reward_policies') or []
            reward_policy = reward_decision.get('reward_policy')
        except Exception as policy_err:
            logger.warning(f'Ошибка referral reward policy для user {payer_id}, level {level_num}: {policy_err}')
            reward_policies = []
            reward_policy = None

        if final_reward > 0:
            from bot.services.balance import credit_user_balance

            reference_type = 'payment_referral'
            reference_id = f'{payment_order_id}:{level_num}'

            balance_result = await credit_user_balance(
                referrer_id,
                final_reward,
                source='referral_reward',
                reason=f'Реферальное вознаграждение, уровень {level_num}',
                reference_type=reference_type,
                reference_id=reference_id,
                metadata={
                    'payer_id': payer_id,
                    'level': level_num,
                    'payment_type': payment_type,
                    'reward_policy': reward_policy,
                },
            )
            if not balance_result.get('ok'):
                final_reward = 0

        if reward_days > 0:
            from bot.services.rewards import grant_days_to_first_active_key

            reference_type = 'payment_referral'
            reference_id = f'{payment_order_id}:{level_num}'

            days_result = await grant_days_to_first_active_key(
                referrer_id,
                reward_days,
                source='referral_reward',
                reason=f'Реферальное вознаграждение, уровень {level_num}',
                reference_type=reference_type,
                reference_id=reference_id,
                metadata={
                    'payer_id': payer_id,
                    'level': level_num,
                    'payment_type': payment_type,
                    'reward_policy': reward_policy,
                },
            )
            if not days_result.get('ok'):
                reward_days = 0

        applied_reward_type = 'balance' if final_reward > 0 else ('days' if reward_days > 0 else reward_type)
        
        from database.requests import record_payment_referral_stat_once

        record_payment_referral_stat_once(
            payment_order_id,
            level=level_num,
            referrer_id=referrer_id,
            payer_id=payer_id,
            reward_minor=final_reward,
            reward_days=reward_days,
            reward_currency=base_currency,
        )

        events.append({
            'referrer_id': referrer_id,
            'payer_id': payer_id,
            'level': level_num,
            'reward_type': applied_reward_type,
            'reward_cents': final_reward,
            'reward_minor': final_reward,
            'reward_currency': base_currency,
            'reward_days': reward_days,
            'reward_policy': reward_policy,
            'reward_policies': reward_policies,
            'period_days': period_days,
            'amount_raw': amount_minor,
            'amount_base_minor': amount_base_minor,
            'base_currency': base_currency,
            'amount_rub_cents': amount_base_minor,
            'payment_type': payment_type,
        })
        
        current_user_id = referrer_id

    if bot is not None and events:
        try:
            from bot.services.notifications import notify_referrers_purchase
            from core.context import get_account_context
            account = get_account_context()
            if account and account.source in ('site', 'mini_app'):
                from runtime.delivery import dispatch
                dispatch(lambda transport: notify_referrers_purchase(transport, order, events), bot=bot)
            else:
                await notify_referrers_purchase(bot, order, events)
        except Exception as notify_err:
            logger.warning(f'Ошибка уведомления рефоводов о покупке: {notify_err}')

    return events
