"""
Сервис биллинга — обработка платежей.

Проверка подписей, создание/продление ключей после оплаты.
"""
import hmac
import hashlib
import logging
from typing import Optional, Dict, Any, Tuple

from database.requests import (
    find_order_by_order_id, complete_order, is_order_already_paid,
    get_vpn_key_by_id, extend_vpn_key, get_setting
)

logger = logging.getLogger(__name__)

# Алфавит для Base62 кодирования
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


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


def process_payment_order(order_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Универсальная обработка успешного ордера (Crypto или Stars).
    Закрывает ордер, продлевает ключ или создаёт черновик.
    
    Returns:
        (success, message_text, order_data)
    """
    from database.requests import (
        is_order_already_paid, find_order_by_order_id, complete_order, 
        extend_vpn_key, create_initial_vpn_key, update_payment_key_id
    )
    
    # 1. Проверка на дубликат (на всякий случай, если вызывающий не проверил)
    if is_order_already_paid(order_id):
        # Получаем ордер чтобы вернуть контекст
        order = find_order_by_order_id(order_id)
        return True, "✅ Этот платёж уже был обработан ранее.", order

    # 2. Поиск ордера
    order = find_order_by_order_id(order_id)
    if not order:
        logger.warning(f"Ордер не найден: {order_id}")
        return False, "⚠️ Ордер не найден. Обратитесь в поддержку.", None
    
    # 3. Закрываем ордер
    if not complete_order(order_id):
        # Если статус уже paid, process_payment_order вызван повторно - обрабатываем как успех
        if order['status'] == 'paid':
             pass
        else:
             return False, "❌ Ошибка обновления статуса платежа.", order
    
    logger.info(f"Order {order_id} processed (paid)")

    # 4. Обработка ключа (Продление или Создание)
    if order['vpn_key_id']:
        # Продление
        days = order.get('period_days') or order.get('duration_days')
        if days and extend_vpn_key(order['vpn_key_id'], days):
            logger.info(f"Ключ {order['vpn_key_id']} продлён на {days} дней (order={order_id})")
            return True, f"✅ Оплата прошла успешно!\n\nВаш ключ продлён на {days} дней.", order
        else:
            logger.error(f"Не удалось продлить ключ {order['vpn_key_id']} после оплаты!")
            return True, "✅ Оплата принята!\n\n⚠️ Возникла проблема с продлением. Мы разберёмся.", order
    else:
        # Новый ключ (Черновик)
        try:
            days = order.get('period_days') or order.get('duration_days') or 30
            # Создаем черновик
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days)
            
            # Привязываем к платежу
            update_payment_key_id(order_id, key_id)
            order['vpn_key_id'] = key_id # Обновляем объект
            
            logger.info(f"Создан черновик ключа {key_id} для заказа {order_id}")
            return True, "✅ Оплата прошла успешно!", order
            
        except Exception as e:
            logger.error(f"Ошибка создания черновика ключа: {e}")
            return True, "✅ Оплата принята, но произошла ошибка при создании ключа. Обратитесь в поддержку.", order


def process_crypto_payment(start_param: str, user_id: Optional[int] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
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
    
    # Если это внутренний ордер, но пользователь оплатил другой тариф (выбрал в UI процессинга)
    if order and parsed.get('tariff') and parsed['tariff'] != '_':
        try:
            tariff_ext_id = int(parsed['tariff'])
            from database.requests import get_tariff_by_external_id, update_order_tariff
            real_tariff = get_tariff_by_external_id(tariff_ext_id)
            
            # Если тариф найден и он отличается от того, что в ордере (или тарифа нет)
            if real_tariff and (real_tariff['id'] != order['tariff_id'] or order.get('payment_type') != 'crypto'):
                logger.info(f"Обновление тарифа ордера {order_id}: {order['tariff_id']} -> {real_tariff['id']} (из callback)")
                if update_order_tariff(order_id, real_tariff['id'], payment_type='crypto'):
                    # Обновляем локальный объект order, чтобы дальше логика работала с новыми данными
                    order['tariff_id'] = real_tariff['id']
                    order['period_days'] = real_tariff['duration_days']
                    order['duration_days'] = real_tariff['duration_days'] # На всякий случай
                    order['payment_type'] = 'crypto'
        except Exception as e:
            logger.error(f"Не удалось обновить тариф из callback: {e}")
    
    if not order:
        if is_internal_order:
             return False, "❌ Ордер не найден в системе.", None
        
        # Внешний ордер -> Создаем PAID order в базе ПЕРЕД обработкой
        if not user_id:
             return False, "⚠️ Ошибка обработки внешнего заказа (нет user_id).", None
        
        logger.info(f"Новый внешний ордер: {order_id}")
        
        # Нам нужен тариф для создания ордера
        tariff_id = None
        amount_cents = 0
        amount_stars = 0
        period_days = 30 # Default
        
        if parsed.get('tariff') and parsed['tariff'] != '_':
            try:
                tariff_external_id = int(parsed['tariff'])
                from database.requests import get_tariff_by_external_id
                tariff = get_tariff_by_external_id(tariff_external_id)
                if tariff:
                    tariff_id = tariff['id']
                    amount_cents = tariff['price_cents']
                    amount_stars = tariff['price_stars']
                    period_days = tariff['duration_days']
            except Exception as e:
                logger.error(f"Ошибка получения тарифа для внешнего ордера: {e}")
        
        # Если тариф не определен, мы не можем создать ордер корректно
        if not tariff_id:
             logger.error(f"Внешний ордер {order_id} без валидного тарифа!")
             return False, "❌ Ошибка: Неизвестный тариф в платеже.", None
             
        # Используем цену из callback если она там есть (PRICE)
        if parsed.get('price') and parsed['price'] > 0:
            amount_cents = parsed['price']
            
        from database.requests import create_paid_order_external
        
        success = create_paid_order_external(
            order_id=order_id,
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='crypto',
            amount_cents=amount_cents,
            amount_stars=amount_stars,
            period_days=period_days
        )
        
        if not success:
             return False, "❌ Ошибка сохранения внешнего заказа.", None
    
    # Delegate to unified logic
    return process_payment_order(order_id)


def build_crypto_payment_url(
    item_id: str,
    invoice_id: str,
    tariff_external_id: Optional[int] = None,
    price_cents: Optional[int] = None
) -> str:
    """
    Формирует ссылку на криптопроцессинг с нашим invoice.
    
    Формат: https://t.me/Ya_SellerBot?start=item-{item_id}-{ref}-{promo}-{invoice}-{price}
    
    Args:
        item_id: ID товара в Ya.Seller (из настроек)
        invoice_id: Наш уникальный invoice (макс 8 символов)
        tariff_external_id: Номер тарифа (1-9) для фиксации цены
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
