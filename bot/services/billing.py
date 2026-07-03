"""
Сервис биллинга — обработка платежей.

Проверка подписей, создание/продление ключей после оплаты.
Создание QR-платежей через ЮКасса REST API.
Реферальные начисления.
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
from typing import Optional, Dict, Any, Tuple

from database.requests import (
    find_order_by_order_id, complete_order, is_order_already_paid,
    get_setting,
    get_yookassa_credentials, get_wata_token, get_platega_credentials,
    get_cardlink_credentials,
    is_referral_enabled, get_referral_reward_type, get_active_referral_levels,
    get_user_referrer, get_user_referral_coefficient, get_user_balance,
    add_to_balance, deduct_from_balance, add_days_to_first_active_key,
    update_referral_stat
)
from bot.services.exchange_rate import get_usd_rub_rate

logger = logging.getLogger(__name__)

STAR_TO_USD = 0.013
USDT_TO_USD = 1.0

YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
WATA_API_URL = "https://api.wata.pro/api/h2h"
PLATEGA_API_URL = "https://app.platega.io"
CARDLINK_API_URL = "https://cardlink.link"
_payment_order_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Алфавит для Base62 кодирования
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def build_payment_return_url(bot_name: str, provider: str, order_id: str) -> str:
    """
    Формирует deep-link возврата из внешней платёжной формы в бота.

    Формат start-параметра единый для QR-провайдеров:
    pay_{provider}_{order_id}
    """
    if not bot_name:
        return "https://t.me"

    provider_code = str(provider or '').strip().lower()
    order_code = str(order_id or '').strip()
    if not provider_code or not order_code:
        return f"https://t.me/{bot_name}"

    return f"https://t.me/{bot_name}?start=pay_{provider_code}_{order_code}"




def encode_base62(data: bytes) -> str:
    """
    Кодирует бинарные данные в Base62.
    
    Используется для формирования подписи callback от Ya.Seller.
    
    Args:
        data: Бинарные данные
        
    Returns:
        Строка в формате Base62
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
    Проверяет подпись callback от криптопроцессинга Ya.Seller.
    
    Подпись = Base62(HMAC-SHA256(data_part, secret_key)[:11]).
    
    Алгоритм согласно документации https://yadreno.ru/seller/integration.php:
    1. Вычисляем HMAC-SHA256 от data_part с секретным ключом
    2. Берем первые 11 байт бинарного результата
    3. Кодируем в Base62
    
    Args:
        data_part: Все сегменты кроме последнего (например bill1-aZ1-bY-1-_-1000)
        received_signature: Полученная подпись (последний сегмент)
        secret_key: Секретный ключ продавца
        
    Returns:
        True если подпись валидна
    """
    # Вычисляем HMAC-SHA256
    h = hmac.new(
        secret_key.encode('utf-8'),
        data_part.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Берем первые 11 байт и кодируем в Base62
    truncated = h[:11]
    expected = encode_base62(truncated)
    
    # Сравниваем подписи
    is_valid = hmac.compare_digest(expected, received_signature)
    
    if not is_valid:
        logger.warning(f"Неверная подпись! expected={expected}, received={received_signature}")
    
    return is_valid


def parse_crypto_callback(start_param: str) -> Optional[Dict[str, Any]]:
    """
    Парсит параметр start из callback криптопроцессинга.
    
    Формат: bill1-ORDER_ID-ITEM_ID-TARIFF-PROMO-PRICE-SIGNATURE
    
    Args:
        start_param: Значение параметра start из deep link
        
    Returns:
        Словарь с полями: order_id, item_id, tariff, promo, price, signature, data_part
        или None если формат неверный
    """
    if not start_param or not start_param.startswith('bill'):
        return None
    
    parts = start_param.split('-')
    
    # Минимум: bill1-ORDER_ID-ITEM_ID-TARIFF-PROMO-PRICE-SIGNATURE (7 частей)
    if len(parts) < 7:
        logger.warning(f"Неверный формат callback: {start_param} (частей: {len(parts)})")
        return None
    
    try:
        # Последняя часть — подпись
        signature = parts[-1]
        # Остальное — данные для проверки подписи
        data_part = start_param.rsplit('-', 1)[0]
        
        return {
            'prefix': parts[0],        # bill1 или bill0
            'order_id': parts[1],      # наш invoice_id
            'item_id': parts[2],       # ID товара в Ya.Seller
            'tariff': parts[3],        # номер тарифа (1-9) или '_'
            'promo': parts[4],         # промокод или '_'
            'price': int(parts[5]) if parts[5] != '_' else 0,  # цена в центах
            'signature': signature,
            'data_part': data_part
        }
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка парсинга callback: {e}")
        return None


def _get_order_payment_action(order: Optional[Dict[str, Any]]) -> str:
    """Определяет тип операции по состоянию ордера до обработки оплаты."""
    if order and order.get('vpn_key_id'):
        return 'renewal'
    return 'new_key'


def _mark_order_runtime_flags(
    order: Optional[Dict[str, Any]],
    *,
    processed_now: bool,
    payment_action: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Добавляет служебные флаги, которые не сохраняются в БД."""
    if order is not None:
        order['_payment_processed_now'] = processed_now
        order['_payment_action'] = payment_action or _get_order_payment_action(order)
    return order


async def process_payment_order(
    order_id: str,
    bot: Optional[Any] = None,
    process_referrals: bool = True,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    async with _payment_order_locks[order_id]:
        return await _process_payment_order_unlocked(
            order_id,
            bot=bot,
            process_referrals=process_referrals,
        )


async def _process_payment_order_unlocked(
    order_id: str,
    bot: Optional[Any] = None,
    process_referrals: bool = True,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Универсальная обработка успешного ордера (Crypto или Stars).
    Закрывает ордер, продлевает ключ или создаёт черновик.
    
    Returns:
        (success, message_text, order_data)
    """
    from database.requests import (
        is_order_already_paid, find_order_by_order_id, complete_order, 
        create_initial_vpn_key, update_payment_key_id
    )
    
    # 1. Проверка на дубликат (на всякий случай, если вызывающий не проверил)
    if is_order_already_paid(order_id):
        # Получаем ордер чтобы вернуть контекст
        order = find_order_by_order_id(order_id)
        return True, "✅ Этот платёж уже был обработан ранее.", _mark_order_runtime_flags(
            order,
            processed_now=False,
        )

    # 2. Поиск ордера
    order = find_order_by_order_id(order_id)
    if not order:
        logger.warning(f"Ордер не найден: {order_id}")
        return False, "⚠️ Ордер не найден. Обратитесь в поддержку.", None
    payment_action = _get_order_payment_action(order)
    
    # 3. Закрываем ордер
    if not complete_order(order_id):
        # Если параллельный обработчик уже успел закрыть ордер, повторно побочные действия не выполняем.
        fresh_order = find_order_by_order_id(order_id)
        if fresh_order and fresh_order.get('status') == 'paid':
            return True, "✅ Этот платёж уже был обработан ранее.", _mark_order_runtime_flags(
                fresh_order,
                processed_now=False,
            )
        return False, "❌ Ошибка обновления статуса платежа.", order
    _mark_order_runtime_flags(order, processed_now=True, payment_action=payment_action)
    
    logger.info(f"Order {order_id} processed (paid)")

    user_internal_id = order['user_id']
    days = order.get('period_days') or order.get('duration_days') or 30

    if order['vpn_key_id']:
        from bot.services.key_lifecycle import renew_key_access
        renew_result = await renew_key_access(
            order['vpn_key_id'],
            days,
            reset_traffic=True,
            tariff_id=order.get('tariff_id'),
        )
        if days and renew_result['db_updated']:
            logger.info(f"Ключ {order['vpn_key_id']} продлён на {days} дней (order={order_id})")
            if not renew_result['panel_synced']:
                logger.warning(
                    f"Ключ {order['vpn_key_id']} продлён в БД, но панель синхронизирована "
                    f"не полностью: {renew_result.get('sync_stats')}"
                )

            if process_referrals and order.get('payment_type') == 'crypto':
                await process_referral_reward(
                    user_internal_id, days, order.get('amount_cents', 0), 'crypto',
                    bot=bot, order=order
                )
            
            return True, f"✅ Оплата прошла успешно!\n\nВаш ключ продлён на {days} дней.", order
        else:
            logger.error(f"Не удалось продлить ключ {order['vpn_key_id']} после оплаты!")
            return True, "✅ Оплата принята!\n\n⚠️ Возникла проблема с продлением. Мы разберёмся.", order
    else:
        if not order.get('tariff_id'):
            logger.error(f"Ордер {order_id}: тариф не найден или неактивен в БД (received tariff_id could not be resolved).")
            from bot.errors import TariffNotFoundError
            raise TariffNotFoundError()
        
        try:
            days = order.get('period_days') or order.get('duration_days') or 30
            # Получаем лимит трафика из тарифа
            from database.requests import get_tariff_by_id as _get_tariff
            _tariff = _get_tariff(order['tariff_id'])
            traffic_limit_bytes = (_tariff.get('traffic_limit_gb', 0) or 0) * (1024**3) if _tariff else 0
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days, traffic_limit=traffic_limit_bytes)
            
            update_payment_key_id(order_id, key_id)
            order['vpn_key_id'] = key_id
            
            logger.info(f"Создан черновик ключа {key_id} для заказа {order_id}")
            
            if process_referrals and order.get('payment_type') == 'crypto':
                await process_referral_reward(
                    user_internal_id, days, order.get('amount_cents', 0), 'crypto',
                    bot=bot, order=order
                )
            
            return True, "✅ Оплата прошла успешно!", order
            
        except Exception as e:
            logger.error(f"Ошибка создания черновика ключа: {e}")
            return True, "✅ Оплата принята, но произошла ошибка при создании ключа. Обратитесь в поддержку.", order


async def process_crypto_payment(
    start_param: str,
    user_id: Optional[int] = None,
    bot: Optional[Any] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Обрабатывает платёж от криптопроцессинга (parse + verify + confirm).
    """
    # Парсим callback
    parsed = parse_crypto_callback(start_param)
    if not parsed:
        return False, "❌ Неверный формат платёжных данных", None
    
    # Получаем секретный ключ
    secret_key = get_setting('crypto_secret_key')
    if not secret_key:
        logger.error("Секретный ключ криптопроцессинга не настроен!")
        return False, "❌ Ошибка конфигурации. Обратитесь в поддержку.", None
    
    # Проверяем подпись
    if not verify_crypto_signature(parsed['data_part'], parsed['signature'], secret_key):
        return False, "❌ Неверная подпись платежа. Попробуйте снова.", None
    
    order_id = parsed['order_id']
    
    # --- ЛОГИКА ОБРАБОТКИ ОРДЕРОВ (Внешние/Внутренние) ---
    is_internal_order = order_id.startswith("00")
    order = find_order_by_order_id(order_id)
    
    if order:
        # Сверяем сумму платежа с тарифом
        from database.requests import get_tariff_by_id
        order_tariff = get_tariff_by_id(order['tariff_id'])
        if order_tariff:
            expected_cents = order_tariff['price_cents']
            received_cents = parsed.get('price', 0)
            if received_cents < expected_cents:
                logger.error(f"Ордер {order_id}: Сумма платежа недостаточна. Ожидалось {expected_cents}, получено {received_cents}")
                return False, "❌ Сумма платежа не совпадает с тарифом.", None
    
    if not order:
        if is_internal_order:
             return False, "❌ Ордер не найден в системе.", None
        
        # Внешний ордер -> Создаем PAID order в базе ПЕРЕД обработкой
        if not user_id:
             return False, "⚠️ Ошибка обработки внешнего заказа (нет user_id).", None
        
        logger.info(f"Новый внешний ордер: {order_id}")
        
        # Внешний ордер без тарифа — ошибка
        logger.error(f"Внешний ордер {order_id} без привязки к тарифу!")
        from bot.errors import TariffNotFoundError
        raise TariffNotFoundError()
    
    # Delegate to unified logic
    return await process_payment_order(order_id, bot=bot)


def build_crypto_payment_url(
    item_id: str,
    invoice_id: str,
    price_cents: Optional[int] = None
) -> str:
    """
    Формирует ссылку на криптопроцессинг с нашим invoice.
    
    Формат: https://t.me/Ya_SellerBot?start=item-{item_id}-{ref}-{promo}-{invoice}-{price}
    
    Args:
        item_id: ID товара в Ya.Seller (из настроек)
        invoice_id: Наш уникальный invoice (макс 8 символов)
        price_cents: Цена в центах (если нужно переопределить)
        
    Returns:
        URL для перехода в криптопроцессинг
    """
    # Формат: item-{item_id}-{ref_code}-{promo}-{invoice}-{price}
    # Пустые параметры заменяем прочерками
    
    ref_code = ""  # Реффералку не используем
    promo = ""     # Промокод не используем
    
    parts = [
        "item",
        item_id,
        ref_code,
        promo,
        invoice_id
    ]
    
    # Добавляем цену если нужно зафиксировать
    if price_cents:
        parts.append(str(price_cents))
    
    start_param = "-".join(parts)
    
    return f"https://t.me/Ya_SellerBot?start={start_param}"


def extract_item_id_from_url(crypto_item_url: str) -> Optional[str]:
    """
    Извлекает item_id из ссылки на товар в Ya.Seller.
    
    Формат ссылки: https://t.me/Ya_SellerBot?start=item-{item_id}...
    
    Args:
        crypto_item_url: Полная ссылка на товар
        
    Returns:
        item_id или None
    """
    if not crypto_item_url:
        return None
    
    # Ищем start= параметр
    if '?start=' in crypto_item_url:
        start_param = crypto_item_url.split('?start=')[1]
        parts = start_param.split('-')
        if len(parts) >= 2 and parts[0] == 'item':
            return parts[1]
    
    return None


# ============================================================================
# ЮКАССА QR-ОПЛАТА (прямой REST API без Telegram Payments)
# ============================================================================

async def create_yookassa_qr_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Создаёт платёж в ЮКасса REST API с подтверждением через QR-код.

    Возвращает изображение QR-кода (PNG) по ссылке, которую можно
    отправить пользователю прямо в Telegram как фото.

    Args:
        amount_rub: Сумма в рублях (например, 299.00)
        order_id: Наш внутренний ордер (для metadata)
        description: Описание платежа (показывается в форме оплаты)
        metadata: Дополнительные метаданные (необязательно)

    Returns:
        Словарь с ключами:
            - yookassa_payment_id: ID платежа в системе ЮКасса
            - qr_image_url: URL изображения QR-кода (PNG)
            - qr_url: Ссылка, зашитая в QR (для открытия в браузере)

    Raises:
        ValueError: Если учётные данные не настроены
        aiohttp.ClientError: Если API недоступен
        RuntimeError: Если API вернул ошибку
    """
    shop_id, secret_key = get_yookassa_credentials()
    if not shop_id or not secret_key:
        raise ValueError("ЮКасса: не настроены shop_id или secret_key")

    # Заголовок Basic Auth: base64(shop_id:secret_key)
    credentials = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()

    # Ключ идемпотентности — уникальный для этого ордера
    idempotence_key = f"qr-{order_id}-{uuid.uuid4().hex[:8]}"
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

    async with aiohttp.ClientSession() as session:
        async with session.post(
            YOOKASSA_API_URL,
            json=payload,
            headers=headers
        ) as response:
            data = await response.json()

            if response.status not in (200, 201):
                error_desc = data.get('description', 'Неизвестная ошибка')
                logger.error(f"ЮКасса API ошибка {response.status}: {error_desc} | payload={payload}")
                raise RuntimeError(f"ЮКасса API ошибка: {error_desc}")

            confirmation = data.get('confirmation', {})
            qr_url = confirmation.get('confirmation_url', '')
            
            if not qr_url:
                logger.error(f"ЮКасса API не вернул confirmation_url: {data}")
                raise RuntimeError("ЮКасса API не вернул данные для QR-кода")

            # Генерируем QR-код из строки оплаты через локальную библиотеку qrcode
            
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
                f"ЮКасса QR создан: payment_id={data['id']}, order_id={order_id}, "
                f"amount={amount_rub} RUB"
            )

            return {
                'yookassa_payment_id': data['id'],
                'qr_image_data': qr_image_data,
                'qr_url': qr_url,
                'status': data.get('status', 'pending')
            }


async def check_yookassa_payment_status(yookassa_payment_id: str) -> str:
    """
    Проверяет статус платежа в ЮКасса REST API.

    Args:
        yookassa_payment_id: ID платежа в системе ЮКасса

    Returns:
        Строка статуса: 'pending', 'waiting_for_capture', 'succeeded', 'canceled'

    Raises:
        ValueError: Если учётные данные не настроены
        aiohttp.ClientError: Если API недоступен
        RuntimeError: Если API вернул ошибку
    """
    shop_id, secret_key = get_yookassa_credentials()
    if not shop_id or not secret_key:
        raise ValueError("ЮКасса: не настроены shop_id или secret_key")

    credentials = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json"
    }

    url = f"{YOOKASSA_API_URL}/{yookassa_payment_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            data = await response.json()

            if response.status != 200:
                error_desc = data.get('description', 'Неизвестная ошибка')
                logger.error(f"ЮКасса статус ошибка {response.status}: {error_desc}")
                raise RuntimeError(f"ЮКасса API ошибка: {error_desc}")

            status = data.get('status', 'pending')
            logger.debug(f"ЮКасса payment {yookassa_payment_id}: status={status}")
            return status


# ============================================================================
# WATA — оплата картой/СБП через REST API (https://wata.pro/api)
# ============================================================================

async def create_wata_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str
) -> Dict[str, Any]:
    """
    Создаёт платёжную ссылку в WATA через H2H API.

    POST https://api.wata.pro/api/h2h/links/

    Args:
        amount_rub: Сумма в рублях
        order_id: Наш внутренний order_id
        description: Описание платежа
        bot_name: Username бота (для построения successRedirectUrl)

    Returns:
        Словарь с ключами:
            - wata_link_id: ID ссылки в системе WATA
            - qr_image_data: PNG-байты QR-кода
            - qr_url: Ссылка для оплаты (карты/СБП)
            - status: Статус платежа

    Raises:
        ValueError: Если JWT-токен не настроен
        RuntimeError: Если API вернул ошибку
    """
    token = get_wata_token()
    if not token:
        raise ValueError("WATA: JWT-токен не настроен")

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

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            try:
                data = await response.json()
            except Exception:
                text = await response.text()
                logger.error(f"WATA API: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("WATA API вернул некорректный ответ")

            if response.status not in (200, 201):
                error_desc = data.get('error') or data.get('message') or data.get('description') or 'Неизвестная ошибка'
                logger.error(f"WATA API ошибка {response.status}: {error_desc} | payload={payload}")
                raise RuntimeError(f"WATA API ошибка: {error_desc}")

            wata_link_id = data.get('id') or data.get('linkId') or data.get('uuid')
            qr_url = data.get('url') or data.get('paymentUrl')

            if not wata_link_id or not qr_url:
                logger.error(f"WATA API не вернул id/url: {data}")
                raise RuntimeError("WATA API не вернул данные платёжной ссылки")

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
                f"WATA ссылка создана: link_id={wata_link_id}, order_id={order_id}, "
                f"amount={amount_rub} RUB"
            )

            return {
                'wata_link_id': str(wata_link_id),
                'qr_image_data': qr_image_data,
                'qr_url': qr_url,
                'status': str(data.get('status', 'Created')).lower(),
            }


async def check_wata_payment_status(wata_link_id: str) -> str:
    """
    Проверяет статус платёжной ссылки WATA по её ID.

    GET https://api.wata.pro/api/h2h/links/{wata_link_id}

    Эндпоинт /transactions/?orderId= не работает (404).
    Вместо этого проверяем статус самой ссылки через /links/{id}.

    WATA имеет лимит — не чаще одного запроса в 30 секунд.
    Контроль частоты запросов выполняется на стороне обработчика.

    Args:
        wata_link_id: ID ссылки в системе WATA (UUID)

    Returns:
        Нормализованный статус: 'pending' | 'succeeded' | 'canceled'

    Raises:
        ValueError: Если JWT-токен не настроен
        RuntimeError: Если API вернул ошибку
    """
    token = get_wata_token()
    if not token:
        raise ValueError("WATA: JWT-токен не настроен")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = f"{WATA_API_URL}/links/{wata_link_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                logger.error(f"WATA статус: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("WATA API вернул некорректный ответ")

            if response.status == 429:
                # Rate-limit — не ошибка, просто слишком частые запросы
                logger.warning(f"WATA rate-limit (429) при проверке {wata_link_id}")
                return 'pending'

            if response.status != 200:
                error_desc = (
                    data.get('error') or data.get('message') or
                    data.get('description') or 'Неизвестная ошибка'
                ) if isinstance(data, dict) else f'HTTP {response.status}'
                logger.error(f"WATA статус ошибка {response.status}: {error_desc}")
                raise RuntimeError(f"WATA API ошибка: {error_desc}")

            # Статус ссылки: Created, Closed, Expired и т.д.
            status = str(data.get('status', '')).lower()
            logger.debug(f"WATA link {wata_link_id}: status={status}")

            # Closed = ссылка закрыта после успешной оплаты
            if status in ('closed', 'paid'):
                return 'succeeded'
            if status in ('declined', 'expired', 'canceled', 'cancelled'):
                return 'canceled'

            return 'pending'


# ============================================================================
# PLATEGA — платёжная форма без заданного метода (https://app.platega.io)
# ============================================================================

async def create_platega_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str,
    user_telegram_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Создаёт транзакцию в Platega API.

    POST https://app.platega.io/v2/transaction/process

    Args:
        amount_rub: Сумма в рублях
        order_id: Наш внутренний order_id
        description: Описание платежа
        bot_name: Username бота (для построения return)
        user_telegram_id: Telegram ID плательщика для metadata.userId

    Returns:
        Словарь с ключами:
            - platega_transaction_id: ID транзакции в системе Platega
            - qr_image_data: PNG-байты QR-кода
            - qr_url: Ссылка для оплаты
            - status: Статус платежа

    Raises:
        ValueError: Если учётные данные не настроены
        RuntimeError: Если API вернул ошибку
    """
    merchant_id, secret = get_platega_credentials()
    if not merchant_id or not secret:
        raise ValueError("Platega: не настроены merchant_id или secret")

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

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            try:
                data = await response.json()
            except Exception:
                text = await response.text()
                logger.error(f"Platega API: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("Platega API вернул некорректный ответ")

            if response.status not in (200, 201):
                error_desc = (
                    data.get('error') or data.get('message') or
                    data.get('description') or 'Неизвестная ошибка'
                )
                logger.error(f"Platega API ошибка {response.status}: {error_desc} | payload={payload}")
                raise RuntimeError(f"Platega API ошибка: {error_desc}")

            transaction_id = data.get('id') or data.get('transactionId') or data.get('uuid')
            qr_url = (
                data.get('redirect') or data.get('redirectUrl') or
                data.get('url') or data.get('paymentUrl')
            )

            if not transaction_id or not qr_url:
                logger.error(f"Platega API не вернул id/url: {data}")
                raise RuntimeError("Platega API не вернул данные платёжной ссылки")

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
                f"Platega транзакция создана: id={transaction_id}, order_id={order_id}, "
                f"amount={amount_rub} RUB"
            )

            return {
                'platega_transaction_id': str(transaction_id),
                'qr_image_data': qr_image_data,
                'qr_url': qr_url,
                'status': str(data.get('status', 'PENDING')).upper(),
            }


async def check_platega_payment_status(transaction_id: str) -> str:
    """
    Проверяет статус транзакции Platega.

    GET https://app.platega.io/transaction/{transaction_id}

    Статусы Platega:
        - PENDING: в процессе оплаты
        - CONFIRMED: успешно оплачена
        - CANCELED: отменена
        - CHARGEBACKED: возвратная

    Args:
        transaction_id: ID транзакции в системе Platega

    Returns:
        Нормализованный статус: 'pending' | 'succeeded' | 'canceled'

    Raises:
        ValueError: Если учётные данные не настроены
        RuntimeError: Если API вернул ошибку
    """
    merchant_id, secret = get_platega_credentials()
    if not merchant_id or not secret:
        raise ValueError("Platega: не настроены merchant_id или secret")

    headers = {
        "X-MerchantId": merchant_id,
        "X-Secret": secret,
        "Accept": "application/json",
    }

    url = f"{PLATEGA_API_URL}/transaction/{transaction_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            try:
                data = await response.json()
            except Exception:
                text = await response.text()
                logger.error(f"Platega статус: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("Platega API вернул некорректный ответ")

            if response.status != 200:
                error_desc = (
                    data.get('error') or data.get('message') or
                    data.get('description') or 'Неизвестная ошибка'
                )
                logger.error(f"Platega статус ошибка {response.status}: {error_desc}")
                raise RuntimeError(f"Platega API ошибка: {error_desc}")

            status = str(data.get('status', '')).upper()
            logger.debug(f"Platega transaction {transaction_id}: status={status}")

            if status == 'CONFIRMED':
                return 'succeeded'
            if status in ('CANCELED', 'CANCELLED', 'CHARGEBACKED'):
                return 'canceled'
            return 'pending'


# ============================================================================
# CARDLINK — оплата Картой/СБП через REST API (https://cardlink.link)
# ============================================================================

async def create_cardlink_payment(
    amount_rub: float,
    order_id: str,
    description: str,
    bot_name: str
) -> Dict[str, Any]:
    """
    Создаёт счёт (bill) в Cardlink API.

    POST https://cardlink.link/api/v1/bill/create

    Тело передаётся как application/x-www-form-urlencoded.
    Авторизация через Bearer token.

    Отличительная особенность: вместо webhook-а пользователь после оплаты
    возвращается в бота по deep-link `https://t.me/{bot}?start=cl_Success`
    (или cl_Fail / cl_Result), что триггерит ту же проверку, что и
    кнопка «✅ Я оплатил».

    Args:
        amount_rub: Сумма в рублях
        order_id: Наш внутренний order_id
        description: Описание платежа (не используется API, но логируется)
        bot_name: Username бота (для построения success_url/fail_url)

    Returns:
        Словарь с ключами:
            - cardlink_bill_id: ID счёта в системе Cardlink
            - qr_image_data: PNG-байты QR-кода
            - qr_url: Ссылка на страницу оплаты
            - status: Статус платежа

    Raises:
        ValueError: Если учётные данные не настроены
        RuntimeError: Если API вернул ошибку
    """
    shop_id, api_token = get_cardlink_credentials()
    if not shop_id or not api_token:
        raise ValueError("Cardlink: не настроены shop_id или api_token")

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

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=form, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                logger.error(f"Cardlink API: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("Cardlink API вернул некорректный ответ")

            if response.status not in (200, 201):
                error_desc = 'Неизвестная ошибка'
                validation_details = ''
                if isinstance(data, dict):
                    err = data.get('error')
                    if isinstance(err, dict):
                        error_desc = err.get('description') or err.get('code') or error_desc
                    elif isinstance(err, str):
                        error_desc = err
                    error_desc = (
                        data.get('message')
                        or data.get('description')
                        or error_desc
                    )
                    validation = data.get('validation') or data.get('errors')
                    if validation:
                        validation_details = f" | validation={validation}"
                logger.error(
                    f"Cardlink API ошибка {response.status}: {error_desc} "
                    f"| order_id={order_id} | full_response={data}{validation_details}"
                )
                raise RuntimeError(f"Cardlink API ошибка: {error_desc}")

            # Ответ может быть вложен в поле 'success' (dict) или лежать в корне.
            # Если 'success' — это флаг (строка/bool), используем сам data.
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
                logger.error(f"Cardlink API не вернул bill_id/url: {data}")
                raise RuntimeError("Cardlink API не вернул данные платёжной ссылки")

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
                f"Cardlink счёт создан: bill_id={bill_id}, order_id={order_id}, "
                f"amount={amount_rub} RUB"
            )

            return {
                'cardlink_bill_id': str(bill_id),
                'qr_image_data': qr_image_data,
                'qr_url': qr_url,
                'status': str(payload.get('status', 'NEW')).upper() if isinstance(payload, dict) else 'NEW',
            }


async def check_cardlink_payment_status(bill_id: str) -> str:
    """
    Проверяет статус счёта Cardlink.

    GET https://cardlink.link/api/v1/bill/status?id={bill_id}

    Статусы Cardlink:
        - NEW / PROCESS / UNDERPAID: в процессе
        - SUCCESS / OVERPAID: успешно оплачено
        - FAIL: отменён / неуспешный

    Args:
        bill_id: ID счёта в системе Cardlink

    Returns:
        Нормализованный статус: 'pending' | 'succeeded' | 'canceled'

    Raises:
        ValueError: Если учётные данные не настроены
        RuntimeError: Если API вернул ошибку
    """
    shop_id, api_token = get_cardlink_credentials()
    if not shop_id or not api_token:
        raise ValueError("Cardlink: не настроены shop_id или api_token")

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }

    url = f"{CARDLINK_API_URL}/api/v1/bill/status"
    params = {"id": bill_id}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                logger.error(f"Cardlink статус: невозможно разобрать ответ ({response.status}): {text}")
                raise RuntimeError("Cardlink API вернул некорректный ответ")

            if response.status != 200:
                error_desc = (
                    (data.get('message') if isinstance(data, dict) else None) or
                    (data.get('error') if isinstance(data, dict) else None) or
                    'Неизвестная ошибка'
                )
                logger.error(f"Cardlink статус ошибка {response.status}: {error_desc}")
                raise RuntimeError(f"Cardlink API ошибка: {error_desc}")

            # Ответ может быть вложен в поле 'success' (dict) или лежать в корне.
            # Если 'success' — это флаг (строка/bool), используем сам data.
            nested = data.get('success') if isinstance(data, dict) else None
            payload = nested if isinstance(nested, dict) else data
            status = ''
            if isinstance(payload, dict):
                status = str(payload.get('status', '')).upper()

            logger.debug(f"Cardlink bill {bill_id}: status={status}")

            if status in ('SUCCESS', 'OVERPAID'):
                return 'succeeded'
            if status == 'FAIL':
                return 'canceled'
            return 'pending'


def convert_to_rub_cents(amount_raw: int, payment_type: str, usd_rub_rate: int) -> int:
    """
    Конвертировать сырую сумму в копейки рублей.

    Args:
        amount_raw: сырая сумма (звёзды/центы USDT/копейки рублей)
        payment_type: тип платежа ('stars', 'crypto', 'cards', 'yookassa_qr', 'wata', 'platega')
        usd_rub_rate: курс USD/RUB в копейках

    Returns:
        Сумма в копейках рублей
    """
    if payment_type == 'stars':
        usd_cents = int(amount_raw * STAR_TO_USD * 100)
        return usd_cents * usd_rub_rate // 100
    elif payment_type == 'crypto':
        usd_cents = amount_raw
        return usd_cents * usd_rub_rate // 100
    else:
        return amount_raw


async def process_referral_reward(
    payer_id: int,
    period_days: int,
    amount_raw: int,
    payment_type: str,
    bot: Optional[Any] = None,
    order: Optional[Dict[str, Any]] = None,
) -> list[Dict[str, Any]]:
    """
    Обработка реферального вознаграждения при оплате.
    Вызывается ПОСЛЕ успешной обработки платежа.
    
    Args:
        payer_id: Внутренний ID пользователя, который оплатил
        period_days: Сколько дней купил реферал
        amount_raw: СЫРАЯ сумма:
            - 'stars': количество звёзд (int)
            - 'crypto': центы USDT (int)
            - 'cards': копейки рублей (int)
            - 'yookassa_qr': копейки рублей (int)
        payment_type: Тип платежа ('stars', 'crypto', 'cards', 'yookassa_qr')
    
    Note:
        При оплате балансом реферальные вознаграждения НЕ начисляются,
        поэтому эта функция не вызывается для платежей балансом.
    """
    if not is_referral_enabled():
        return []
    
    reward_type = get_referral_reward_type()
    active_levels = dict(get_active_referral_levels())
    
    if not active_levels:
        return []
    
    usd_rub_rate = await get_usd_rub_rate()
    amount_rub_cents = convert_to_rub_cents(amount_raw, payment_type, usd_rub_rate)
    
    current_user_id = payer_id
    events = []
    
    from bot.services.user_locks import user_locks
    
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
            base_reward = amount_rub_cents * (percent / 100)
            final_reward = int(base_reward * coefficient)
            final_reward = round(final_reward / 100) * 100
            
            if final_reward > 0:
                async with user_locks[referrer_id]:
                    add_to_balance(referrer_id, final_reward)
            
            reward_days = 0
        else:
            base_days = period_days * (percent / 100)
            final_days = base_days * coefficient
            reward_days = math.ceil(final_days)
            
            if reward_days > 0:
                if not add_days_to_first_active_key(referrer_id, reward_days):
                    reward_days = 0
            
            final_reward = 0
        
        update_referral_stat(
            referrer_id, payer_id, level_num,
            final_reward, reward_days
        )

        events.append({
            'referrer_id': referrer_id,
            'payer_id': payer_id,
            'level': level_num,
            'reward_type': reward_type,
            'reward_cents': final_reward,
            'reward_days': reward_days,
            'period_days': period_days,
            'amount_raw': amount_raw,
            'amount_rub_cents': amount_rub_cents,
            'payment_type': payment_type,
        })
        
        current_user_id = referrer_id

    if bot is not None and order is not None and events:
        try:
            from bot.services.notifications import notify_referrers_purchase
            await notify_referrers_purchase(bot, order, events)
        except Exception as notify_err:
            logger.warning(f'Ошибка уведомления рефоводов о покупке: {notify_err}')

    return events


def calculate_balance_discount(user_id: int, tariff_price_cents: int) -> tuple[int, int]:
    """
    Рассчитать скидку с баланса. БЕЗ списания!
    
    Args:
        user_id: Внутренний ID пользователя
        tariff_price_cents: Цена тарифа в копейках
    
    Returns:
        Кортеж (remaining_to_pay_cents, to_deduct_cents):
        - remaining_to_pay_cents: сколько нужно оплатить внешним способом
        - to_deduct_cents: сколько будет списано с баланса ПРИ УСПЕШНОЙ оплате
    """
    balance = get_user_balance(user_id)
    
    if balance >= tariff_price_cents:
        return 0, tariff_price_cents
    else:
        return tariff_price_cents - balance, balance


async def complete_payment_flow(
    order_id: str,
    message,
    state,
    telegram_id: int,
    payment_type: str,
    referral_amount: int
) -> None:
    """
    Единый post-payment поток после подтверждения оплаты.
    
    Выполняет:
    1. Обработку ордера (process_payment_order)
    2. Списание баланса (если частичная оплата)
    3. Начисление реферального вознаграждения
    4. Финализацию UI (выдача ключа / показ результата)
    
    Вызывается из:
    - successful_payment_handler (Stars/TG payments) — base.py
    - check_yookassa_payment (ЮКасса) — yookassa.py
    
    Args:
        order_id: ID ордера
        message: Сообщение для ответа пользователю
        state: FSM-контекст (для баланса и очистки)
        telegram_id: Telegram ID пользователя
        payment_type: Тип платежа ('stars', 'cards', 'yookassa_qr')
        referral_amount: Сырая сумма для реферального вознаграждения:
            - 'stars': количество звёзд
            - 'cards': копейки рублей
            - 'yookassa_qr': копейки рублей
    """
    from bot.handlers.user.payments.base import finalize_payment_ui
    from bot.keyboards.admin import home_only_kb
    from bot.services.user_locks import user_locks
    
    state_data = await state.get_data()
    balance_to_deduct = state_data.get('balance_to_deduct', 0)
    
    try:
        (success, text, order) = await process_payment_order(
            order_id,
            bot=message.bot,
            process_referrals=False,
        )
        
        if success and order:
            user_internal_id = order['user_id']
            days = order.get('period_days') or order.get('duration_days') or 30
            processed_now = order.get('_payment_processed_now', True)

            if processed_now:
                # Списание баланса при частичной оплате
                if balance_to_deduct > 0:
                    async with user_locks[user_internal_id]:
                        current_balance = get_user_balance(user_internal_id)
                        actual_deduct = min(balance_to_deduct, current_balance)
                        if actual_deduct > 0:
                            deduct_from_balance(user_internal_id, actual_deduct)
                            logger.info(
                                f'Списано {actual_deduct} коп с баланса user '
                                f'{user_internal_id} при частичной оплате ({payment_type})'
                            )

                # Реферальное вознаграждение
                await process_referral_reward(
                    user_internal_id, days, referral_amount, payment_type,
                    bot=message.bot, order=order
                )

                # Уведомление администраторов об оплате
                try:
                    from bot.services.notifications import notify_admins_payment
                    await notify_admins_payment(message.bot, order)
                except Exception as notify_err:
                    logger.warning(f'Ошибка уведомления об оплате: {notify_err}')
            else:
                logger.info(f'Повторная обработка платежа {order_id}: побочные действия пропущены')

            # Очистка FSM данных о балансе
            await state.update_data(balance_to_deduct=0, remaining_cents=0)
            
            # Финализация UI
            await finalize_payment_ui(message, state, text, order, user_id=telegram_id)
        else:
            await message.answer(text, reply_markup=home_only_kb(), parse_mode='HTML')
    
    except Exception as e:
        from bot.errors import TariffNotFoundError
        if isinstance(e, TariffNotFoundError):
            from bot.keyboards.user import support_kb
            support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
            await message.answer(str(e), reply_markup=support_kb(support_link), parse_mode='HTML')
        else:
            logger.exception(f'Ошибка обработки {payment_type} платежа: {e}')
            await message.answer('❌ Произошла ошибка при обработке платежа.', parse_mode='HTML')

