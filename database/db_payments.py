import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)
BASE62_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'

from .db_tariffs import get_tariff_by_id
from .db_settings import get_setting, set_setting

DEFAULT_REFERRAL_NEW_REF_NOTIFICATION_TEXT = (
    "👥 <b>Новый реферал</b>\n\n"
    "По вашей ссылке зарегистрировался пользователь.\n\n"
    "👤 Имя: <b>%имя%</b>\n"
    "🔗 Логин: %логин%\n"
    "📊 Уровень: <b>%уровень%</b>"
)

DEFAULT_REFERRAL_PURCHASE_NOTIFICATION_TEXT = (
    "💳 <b>Покупка реферала</b>\n\n"
    "Пользователь <b>%имя%</b> (%логин%) оплатил тариф.\n\n"
    "🎫 Тариф: <b>%тариф%</b>\n"
    "💵 Сумма: <b>%сумма%</b>\n"
    "⏳ Срок: <b>%дней%</b>\n"
    "🎁 Ваш бонус: <b>%вознаграждение%</b>\n"
    "📊 Уровень: <b>%уровень%</b>"
)


__all__ = [
    'save_yookassa_payment_id',
    'find_order_by_yookassa_id',
    'save_wata_link_id',
    'find_order_by_wata_link_id',
    'save_platega_transaction_id',
    'find_order_by_platega_transaction_id',
    'save_cardlink_bill_id',
    'find_order_by_cardlink_bill_id',
    'find_latest_pending_cardlink_order_for_user',
    'get_user_payments_stats',
    'get_daily_payments_stats',
    'get_key_payments_history',
    '_int_to_base62',
    'create_pending_order',
    'create_paid_order_external',
    'find_order_by_order_id',
    'complete_order',
    'update_order_tariff',
    'update_payment_type',
    'update_payment_key_id',
    'is_order_already_paid',
    'get_key_payments_history',
    'get_referral_levels',
    'get_active_referral_levels',
    'update_referral_level',
    'get_referral_stats',
    'update_referral_stat',
    'is_referral_enabled',
    'get_referral_reward_type',
    'get_referral_conditions_text',
    'parse_referral_notification_levels',
    'get_referral_notification_levels',
    'is_referral_new_ref_notifications_enabled',
    'is_referral_purchase_notifications_enabled',
    'get_referral_new_ref_notification_text',
    'get_referral_purchase_notification_text',
    'get_referral_notification_settings',
    'update_referral_setting',
]

def save_yookassa_payment_id(order_id: str, yookassa_payment_id: str) -> bool:
    """
    Сохраняет ID платежа ЮКасса в запись ордера.

    Args:
        order_id: Наш внутренний order_id
        yookassa_payment_id: ID платежа в системе ЮКассы

    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE payments SET yookassa_payment_id = ? WHERE order_id = ?",
            (yookassa_payment_id, order_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Сохранён yookassa_payment_id={yookassa_payment_id} для order_id={order_id}")
        return success

def find_order_by_yookassa_id(yookassa_payment_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит ордер по ID платежа ЮКасса.

    Args:
        yookassa_payment_id: ID платежа в системе ЮКассы

    Returns:
        Словарь с данными ордера или None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE yookassa_payment_id = ?",
            (yookassa_payment_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def save_wata_link_id(order_id: str, wata_link_id: str) -> bool:
    """
    Сохраняет ID платёжной ссылки WATA в запись ордера.

    Args:
        order_id: Наш внутренний order_id
        wata_link_id: ID ссылки в системе WATA

    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE payments SET wata_link_id = ? WHERE order_id = ?",
            (wata_link_id, order_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Сохранён wata_link_id={wata_link_id} для order_id={order_id}")
        return success

def find_order_by_wata_link_id(wata_link_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит ордер по ID платёжной ссылки WATA.

    Args:
        wata_link_id: ID ссылки в системе WATA

    Returns:
        Словарь с данными ордера или None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE wata_link_id = ?",
            (wata_link_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def save_platega_transaction_id(order_id: str, transaction_id: str) -> bool:
    """
    Сохраняет ID транзакции Platega в запись ордера.

    Args:
        order_id: Наш внутренний order_id
        transaction_id: ID транзакции в системе Platega

    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE payments SET platega_transaction_id = ? WHERE order_id = ?",
            (transaction_id, order_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Сохранён platega_transaction_id={transaction_id} для order_id={order_id}")
        return success

def find_order_by_platega_transaction_id(transaction_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит ордер по ID транзакции Platega.

    Args:
        transaction_id: ID транзакции в системе Platega

    Returns:
        Словарь с данными ордера или None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE platega_transaction_id = ?",
            (transaction_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def save_cardlink_bill_id(order_id: str, bill_id: str) -> bool:
    """
    Сохраняет bill_id Cardlink в запись ордера.

    Args:
        order_id: Наш внутренний order_id
        bill_id: ID счёта в системе Cardlink

    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE payments SET cardlink_bill_id = ? WHERE order_id = ?",
            (bill_id, order_id)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Сохранён cardlink_bill_id={bill_id} для order_id={order_id}")
        return success

def find_order_by_cardlink_bill_id(bill_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит ордер по bill_id Cardlink.

    Args:
        bill_id: ID счёта в системе Cardlink

    Returns:
        Словарь с данными ордера или None
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM payments WHERE cardlink_bill_id = ?",
            (bill_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def find_latest_pending_cardlink_order_for_user(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Находит последний pending-ордер типа 'cardlink' для пользователя.

    Используется при возврате пользователя по deep-link cl_Success/cl_Fail/cl_Result,
    чтобы понять, какой именно платёж проверять.

    Args:
        user_id: Внутренний ID пользователя

    Returns:
        Словарь с данными ордера или None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT * FROM payments
            WHERE user_id = ?
              AND payment_type = 'cardlink'
              AND status = 'pending'
              AND cardlink_bill_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_payments_stats(user_id: int) -> Dict[str, Any]:
    """
    Получает статистику оплат пользователя.
    
    Args:
        user_id: Внутренний ID пользователя
    
    Returns:
        Словарь со статистикой:
        - total_payments: количество платежей
        - total_amount_cents: общая сумма в центах
        - total_amount_stars: общая сумма в звёздах
        - last_payment_at: дата последней оплаты
        - tariffs: список уникальных тарифов
    """
    with get_db() as conn:
        # Общая статистика
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total_payments,
                COALESCE(SUM(CASE WHEN payment_type = 'crypto' THEN amount_cents ELSE 0 END), 0) as total_amount_cents,
                COALESCE(SUM(CASE WHEN payment_type = 'stars' THEN amount_stars ELSE 0 END), 0) as total_amount_stars,
                COALESCE(SUM(CASE WHEN payment_type IN ('cards', 'yookassa_qr', 'wata', 'platega', 'cardlink', 'balance') THEN t.price_rub ELSE 0 END), 0) as total_amount_rub,
                MAX(paid_at) as last_payment_at
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.user_id = ? AND p.status = 'paid'
        """, (user_id,))
        stats = dict(cursor.fetchone())
        
        # Уникальные тарифы
        cursor = conn.execute("""
            SELECT DISTINCT t.name 
            FROM payments p
            JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.user_id = ?
        """, (user_id,))
        stats['tariffs'] = [row['name'] for row in cursor.fetchall()]
        
        return stats

def get_daily_payments_stats() -> Dict[str, Any]:
    """
    Получает статистику платежей за последние 24 часа.
    
    Returns:
        Словарь со статистикой:
        - paid_count: количество успешных платежей
        - paid_cents: сумма успешных в центах
        - paid_stars: сумма успешных в звёздах
        - pending_count: количество ожидающих (неоплаченных)
    """
    with get_db() as conn:
        # 1. Считаем USDT (crypto)
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(amount_cents), 0) as total_cents
            FROM payments
            WHERE status = 'paid' 
            AND payment_type = 'crypto'
            AND paid_at >= datetime('now', '-1 day')
        """)
        crypto_row = cursor.fetchone()
        
        # 2. Считаем Stars
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(amount_stars), 0) as total_stars
            FROM payments
            WHERE status = 'paid' 
            AND payment_type = 'stars'
            AND paid_at >= datetime('now', '-1 day')
        """)
        stars_row = cursor.fetchone()
        
        # 3. Считаем Карты (Cards - Рубли)
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(t.price_rub), 0) as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid' 
            AND p.payment_type = 'cards'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        cards_row = cursor.fetchone()
        
        # 4. Считаем QR-оплату (ЮКасса QR/СБП - Рубли)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(t.price_rub), 0) as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'yookassa_qr'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        qr_row = cursor.fetchone()

        # 5. Считаем WATA (Карта/СБП - Рубли)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(t.price_rub), 0) as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'wata'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        wata_row = cursor.fetchone()

        # 6. Считаем Platega (СБП - Рубли)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(t.price_rub), 0) as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'platega'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        platega_row = cursor.fetchone()

        # 7. Считаем Cardlink (Карта/СБП - Рубли)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(t.price_rub), 0) as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'cardlink'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        cardlink_row = cursor.fetchone()

        paid_count = (crypto_row['count'] if crypto_row else 0) + \
                     (stars_row['count'] if stars_row else 0) + \
                     (cards_row['count'] if cards_row else 0) + \
                     (qr_row['count'] if qr_row else 0) + \
                     (wata_row['count'] if wata_row else 0) + \
                     (platega_row['count'] if platega_row else 0) + \
                     (cardlink_row['count'] if cardlink_row else 0)
        total_cents = crypto_row['total_cents'] if crypto_row else 0
        total_stars = stars_row['total_stars'] if stars_row else 0
        total_rub = (cards_row['total_rub'] if cards_row else 0) + \
                    (qr_row['total_rub'] if qr_row else 0) + \
                    (wata_row['total_rub'] if wata_row else 0) + \
                    (platega_row['total_rub'] if platega_row else 0) + \
                    (cardlink_row['total_rub'] if cardlink_row else 0)
        
        return {
            'paid_count': paid_count,
            'paid_cents': total_cents,
            'paid_stars': total_stars,
            'paid_rub': total_rub,
            'pending_count': 0 
        }

def get_key_payments_history(key_id: int) -> List[Dict[str, Any]]:
    """
    Получает историю платежей по конкретному ключу.
    
    Args:
        key_id: ID ключа
    
    Returns:
        Список платежей, отсортированный по дате (по убыванию).
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                p.id, p.paid_at, p.payment_type, p.amount_cents, p.amount_stars,
                t.name as tariff_name, t.price_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.vpn_key_id = ? AND p.status = 'paid'
            ORDER BY p.paid_at DESC
        """, (key_id,))
        return [dict(row) for row in cursor.fetchall()]

def _int_to_base62(num: int) -> str:
    """
    Конвертирует число в base62 строку.
    
    Args:
        num: Положительное целое число
        
    Returns:
        Base62 строка (0-9, A-Z, a-z)
    """
    if num == 0:
        return BASE62_ALPHABET[0]
    
    result = []
    while num > 0:
        result.append(BASE62_ALPHABET[num % 62])
        num //= 62
    
    return ''.join(reversed(result))

def create_pending_order(
    user_id: int,
    tariff_id: Optional[int],
    payment_type: Optional[str],
    vpn_key_id: Optional[int] = None
) -> tuple[int, str]:
    """
    Создаёт pending order и генерирует уникальный order_id.
    
    Order_id генерируется из внутреннего ID записи в base62 формате,
    что гарантирует уникальность и соответствие формату криптопроцессинга
    (макс 8 символов A-Za-z0-9).
    
    Args:
        user_id: Внутренний ID пользователя
        tariff_id: ID тарифа (может быть None для крипты)
        payment_type: 'crypto', 'stars' или None (если выбирается при оплате)
        vpn_key_id: ID ключа для продления (None для нового ключа)
    
    Returns:
        Кортеж (payment_id, order_id)
    """
    tariff = get_tariff_by_id(tariff_id) if tariff_id else None
    
    with get_db() as conn:
        # Шаг 1: создаём запись с временным order_id
        cursor = conn.execute("""
            INSERT INTO payments 
            (user_id, tariff_id, order_id, payment_type, vpn_key_id, 
             amount_cents, amount_stars, period_days, status, paid_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, 'pending', NULL)
        """, (
            user_id, tariff_id, payment_type, vpn_key_id,
            tariff['price_cents'] if tariff else 0,
            tariff['price_stars'] if tariff else 0,
            tariff['duration_days'] if tariff else None
        ))
        payment_id = cursor.lastrowid
        
        # Шаг 2: генерируем order_id из ID записи (base62)
        # Добавляем префикс '00' для исключения конфликтов с внешними ID
        order_id = "00" + _int_to_base62(payment_id)
        
        # Шаг 3: обновляем order_id
        conn.execute("""
            UPDATE payments SET order_id = ? WHERE id = ?
        """, (order_id, payment_id))
        
        logger.info(f"Создан pending order: {order_id} (id={payment_id}, user={user_id}, type={payment_type})")
        return payment_id, order_id

def create_paid_order_external(
    order_id: str,
    user_id: int,
    tariff_id: int,
    payment_type: str,
    amount_cents: int,
    amount_stars: int,
    period_days: int
) -> bool:
    """
    Создаёт сразу оплаченный ордер (для внешних платежей).
    
    Используется когда оплата пришла извне (без предварительного pending order).
    
    Args:
        order_id: Внешний ID ордера
        user_id: ID пользователя
        tariff_id: ID тарифа
        payment_type: Тип оплаты ('crypto', 'stars')
        amount_cents: Сумма в центах
        amount_stars: Сумма в звёздах
        period_days: Срок действия
        
    Returns:
        True если успешно
    """
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO payments 
                (user_id, tariff_id, order_id, payment_type, vpn_key_id, 
                 amount_cents, amount_stars, period_days, status, paid_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'pending', NULL)
            """, (
                user_id, tariff_id, order_id, payment_type,
                amount_cents, amount_stars, period_days
            ))
            logger.info(f"Создан external pending order: {order_id} (user={user_id})")
            return True
    except Exception as e:
        logger.error(f"Ошибка создания external order {order_id}: {e}")
        return False

def find_order_by_order_id(order_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит платёж по order_id.
    
    Args:
        order_id: Уникальный ID ордера
    
    Returns:
        Словарь с данными платежа или None
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT p.*, t.duration_days, t.name as tariff_name
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.order_id = ?
        """, (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def complete_order(order_id: str) -> bool:
    """
    Завершает платёж: меняет статус на 'paid'.
    
    Args:
        order_id: ID ордера
    
    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE payments 
            SET status = 'paid', paid_at = CURRENT_TIMESTAMP
            WHERE order_id = ? AND status = 'pending'
        """, (order_id,))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Order {order_id} завершён (paid)")
        return success

def update_order_tariff(order_id: str, tariff_id: int, payment_type: Optional[str] = None) -> bool:
    """
    Обновляет тариф и суммы в ордере.
    
    Args:
        order_id: ID ордера
        tariff_id: ID нового тарифа
        payment_type: Тип оплаты (опционально)
    
    Returns:
        True если успешно
    """
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        return False
        
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE payments 
            SET tariff_id = ?, 
                amount_cents = ?, 
                amount_stars = ?, 
                period_days = ?,
                payment_type = COALESCE(?, payment_type)
            WHERE order_id = ?
        """, (
            tariff_id, 
            tariff['price_cents'], 
            tariff['price_stars'], 
            tariff['duration_days'], 
            payment_type,
            order_id
        ))
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Order {order_id} обновлен на тариф {tariff_id} (тип: {payment_type})")
        return success

def update_payment_type(order_id: str, payment_type: str) -> bool:
    """
    Обновляет тип оплаты в ордере.
    
    Args:
        order_id: ID ордера
        payment_type: Новый тип оплаты ('crypto', 'stars')
        
    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE payments 
            SET payment_type = ?
            WHERE order_id = ?
        """, (payment_type, order_id))
        success = cursor.rowcount > 0
        if success:
             logger.info(f"Order {order_id} тип оплаты обновлен на {payment_type}")
        return success

def update_payment_key_id(order_id: str, vpn_key_id: int) -> bool:
    """
    Привязывает созданный VPN-ключ к платежу.
    
    Args:
        order_id: ID ордера
        vpn_key_id: ID ключа
    
    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE payments 
            SET vpn_key_id = ?
            WHERE order_id = ?
        """, (vpn_key_id, order_id))
        return cursor.rowcount > 0

def is_order_already_paid(order_id: str) -> bool:
    """
    Проверяет, был ли ордер уже оплачен.
    
    Args:
        order_id: ID ордера
    
    Returns:
        True если статус = 'paid'
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT status FROM payments WHERE order_id = ?",
            (order_id,)
        )
        row = cursor.fetchone()
        return row and row['status'] == 'paid'

def get_key_payments_history(key_id: int) -> List[Dict[str, Any]]:
    """
    Получает историю платежей по ключу.
    
    Args:
        key_id: ID ключа
    
    Returns:
        Список платежей с названиями тарифов
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT p.*, t.name as tariff_name, t.price_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.vpn_key_id = ? AND p.status = 'paid'
            ORDER BY p.paid_at DESC
        """, (key_id,))
        return [dict(row) for row in cursor.fetchall()]

def get_referral_levels() -> List[Dict[str, Any]]:
    """
    Получить все уровни реферальной системы.
    
    Returns:
        Список [{level_number, percent, enabled}, ...]
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT level_number, percent, enabled FROM referral_levels ORDER BY level_number"
        )
        return [dict(row) for row in cursor.fetchall()]

def get_active_referral_levels() -> List[tuple]:
    """
    Получить только включённые уровни.
    
    Returns:
        Список кортежей [(level_num, percent), ...]
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT level_number, percent FROM referral_levels WHERE enabled = 1 ORDER BY level_number"
        )
        return [(row['level_number'], row['percent']) for row in cursor.fetchall()]

def update_referral_level(level_number: int, percent: int, enabled: bool) -> bool:
    """
    Обновить уровень реферальной системы.
    
    Args:
        level_number: Номер уровня (1, 2, 3)
        percent: Процент (1-100)
        enabled: Включён ли уровень
    
    Returns:
        True если успешно
    """
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE referral_levels SET percent = ?, enabled = ? WHERE level_number = ?",
            (percent, 1 if enabled else 0, level_number)
        )
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Уровень {level_number} обновлён: {percent}%, enabled={enabled}")
        return success

def get_referral_stats(user_id: int) -> List[Dict[str, Any]]:
    """
    Статистика по включённым уровням реферальной программы.
    
    Args:
        user_id: Внутренний ID пользователя (реферера)
    
    Returns:
        Список [{level, count, total_reward_cents, total_reward_days}, ...]
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT level_number FROM referral_levels WHERE enabled = 1 ORDER BY level_number"
        )
        active_levels = [row['level_number'] for row in cursor.fetchall()]
        if not active_levels:
            return []

        cursor = conn.execute("""
            SELECT 
                level,
                COUNT(*) as paying_count,
                COALESCE(SUM(total_reward_cents), 0) as total_reward_cents,
                COALESCE(SUM(total_reward_days), 0) as total_reward_days
            FROM referral_stats
            WHERE referrer_id = ?
            GROUP BY level
            ORDER BY level
        """, (user_id,))
        rewards = {row['level']: dict(row) for row in cursor.fetchall()}
        
        # Общее количество приглашенных по уровням
        # Используем рекурсивный CTE (WITH RECURSIVE) для получения дерева рефералов
        cursor = conn.execute("""
            WITH RECURSIVE referral_tree(id, level) AS (
                SELECT id, 1 
                FROM users 
                WHERE referred_by = ?
                UNION ALL
                SELECT u.id, rt.level + 1 
                FROM users u
                JOIN referral_tree rt ON u.referred_by = rt.id
                WHERE rt.level < 10
            )
            SELECT level, COUNT(*) as total_count 
            FROM referral_tree 
            WHERE level <= 3
            GROUP BY level
        """, (user_id,))
        counts = {row['level']: row['total_count'] for row in cursor.fetchall()}
        
        result = []
        for level in active_levels:
            rew = rewards.get(level, {
                'level': level,
                'paying_count': 0,
                'total_reward_cents': 0,
                'total_reward_days': 0
            })
            # Заменяем 'count' на 'total_count', чтобы показывать всех приглашённых
            rew['count'] = counts.get(level, 0)
            result.append(rew)
            
        return result

def update_referral_stat(
    referrer_id: int, 
    referral_id: int, 
    level: int, 
    reward_cents: int, 
    reward_days: int
) -> bool:
    """
    Обновить статистику реферала (INSERT ON CONFLICT DO UPDATE).
    
    Args:
        referrer_id: ID реферера
        referral_id: ID реферала
        level: Уровень (1, 2, 3)
        reward_cents: Вознаграждение в копейках
        reward_days: Вознаграждение в днях
    
    Returns:
        True если успешно
    """
    with get_db() as conn:
        conn.execute("""
            INSERT INTO referral_stats (referrer_id, referral_id, level, total_payments_count, total_reward_cents, total_reward_days)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(referrer_id, referral_id, level) DO UPDATE SET
                total_payments_count = total_payments_count + 1,
                total_reward_cents = total_reward_cents + excluded.total_reward_cents,
                total_reward_days = total_reward_days + excluded.total_reward_days
        """, (referrer_id, referral_id, level, reward_cents, reward_days))
        return True

def is_referral_enabled() -> bool:
    """Включена ли реферальная система."""
    return get_setting('referral_enabled', '0') == '1'

def get_referral_reward_type() -> str:
    """Тип начисления: 'days' или 'balance'."""
    return get_setting('referral_reward_type', 'days')

def get_referral_conditions_text() -> str:
    """Текст условий реферальной программы."""
    return get_setting('referral_conditions_text', '')

def parse_referral_notification_levels(raw: Optional[str]) -> List[int]:
    """
    Разбирает CSV уровней уведомлений рефовода.

    Допустимые значения: 1, 2, 3. Пустое или некорректное значение
    трактуется как дефолтный первый уровень.
    """
    value = (raw or '').strip()
    if not value:
        return [1]

    result = []
    for part in value.split(','):
        part = part.strip()
        if not part.isdigit():
            return [1]
        level = int(part)
        if level not in (1, 2, 3):
            return [1]
        if level not in result:
            result.append(level)

    return result or [1]

def get_referral_notification_levels() -> List[int]:
    """Уровни, по которым рефовод получает скрытые уведомления."""
    return parse_referral_notification_levels(
        get_setting('referral_notification_levels', '1')
    )

def is_referral_new_ref_notifications_enabled() -> bool:
    """Включены ли скрытые уведомления о новых рефералах."""
    return get_setting('referral_new_ref_notifications_enabled', '0') == '1'

def is_referral_purchase_notifications_enabled() -> bool:
    """Включены ли скрытые уведомления о покупках рефералов."""
    return get_setting('referral_purchase_notifications_enabled', '0') == '1'

def get_referral_new_ref_notification_text() -> str:
    """Текст скрытого уведомления о новом реферале."""
    return get_setting(
        'referral_new_ref_notification_text',
        DEFAULT_REFERRAL_NEW_REF_NOTIFICATION_TEXT,
    ) or DEFAULT_REFERRAL_NEW_REF_NOTIFICATION_TEXT

def get_referral_purchase_notification_text() -> str:
    """Текст скрытого уведомления о покупке реферала."""
    return get_setting(
        'referral_purchase_notification_text',
        DEFAULT_REFERRAL_PURCHASE_NOTIFICATION_TEXT,
    ) or DEFAULT_REFERRAL_PURCHASE_NOTIFICATION_TEXT

def get_referral_notification_settings() -> Dict[str, Any]:
    """Текущее состояние скрытых уведомлений рефовода для read-only вывода."""
    return {
        'new_ref_enabled': is_referral_new_ref_notifications_enabled(),
        'new_ref_text_set': bool(get_referral_new_ref_notification_text().strip()),
        'purchase_enabled': is_referral_purchase_notifications_enabled(),
        'purchase_text_set': bool(get_referral_purchase_notification_text().strip()),
        'levels': get_referral_notification_levels(),
    }

def update_referral_setting(key: str, value: str) -> bool:
    """
    Обновить настройку реферальной системы.
    
    Args:
        key: Ключ настройки
        value: Значение
    
    Returns:
        True если успешно
    """
    set_setting(key, value)
    return True
