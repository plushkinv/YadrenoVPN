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
    'reopen_paid_order',
    'update_order_tariff',
    'update_payment_type',
    'update_payment_key_id',
    'save_payment_balance_deduction',
    'cancel_pending_order',
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
    Saves the YuKass payment ID in the order record.

    Args:
        order_id: Our internal order_id
        yookassa_payment_id: Payment ID in the Yookassa system

    Returns:
        True if successful
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
    Finds an order by YuKass payment ID.

    Args:
        yookassa_payment_id: Payment ID in the Yookassa system

    Returns:
        Dictionary with order data or None
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
    Saves the WATA payment link ID to the order record.

    Args:
        order_id: Our internal order_id
        wata_link_id: WATA link ID

    Returns:
        True if successful
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
    Finds an order by WATA payment link ID.

    Args:
        wata_link_id: WATA link ID

    Returns:
        Dictionary with order data or None
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
    Saves the Platega transaction ID to the order record.

    Args:
        order_id: Our internal order_id
        transaction_id: Transaction ID in the Platega system

    Returns:
        True if successful
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
    Finds an order by Platega transaction ID.

    Args:
        transaction_id: Transaction ID in the Platega system

    Returns:
        Dictionary with order data or None
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
    Saves the bill_id Cardlink to the order record.

    Args:
        order_id: Our internal order_id
        bill_id: Account ID in the Cardlink system

    Returns:
        True if successful
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
    Finds an order by bill_id Cardlink.

    Args:
        bill_id: Account ID in the Cardlink system

    Returns:
        Dictionary with order data or None
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
    Finds the last pending order of type 'cardlink' for the user.

    Used when returning a user via deep-link cl_Success/cl_Fail/cl_Result,
    to understand which payment to check.

    Args:
        user_id: Internal user ID

    Returns:
        Dictionary with order data or None
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
    Gets user payment statistics.
    
    Args:
        user_id: Internal user ID
    
    Returns:
        Dictionary with statistics:
        - total_payments: number of payments
        - total_amount_cents: total amount in cents
        - total_amount_stars: total amount in stars
        - last_payment_at: date of last payment
        - tariffs: list of unique tariffs
    """
    with get_db() as conn:
        # General statistics
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total_payments,
                COALESCE(SUM(CASE WHEN COALESCE(intent_version, 0) != 1 AND payment_type = 'crypto' THEN COALESCE(final_amount_cents, amount_cents, 0) ELSE 0 END), 0) as total_amount_cents,
                COALESCE(SUM(CASE WHEN COALESCE(intent_version, 0) != 1 AND payment_type = 'stars' THEN COALESCE(final_amount_stars, amount_stars, 0) ELSE 0 END), 0) as total_amount_stars,
                COALESCE(SUM(CASE WHEN COALESCE(intent_version, 0) != 1 AND payment_type IN ('cards', 'yookassa_qr', 'wata', 'platega', 'cardlink', 'balance') THEN COALESCE(final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_amount_rub,
                MAX(paid_at) as last_payment_at
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.user_id = ? AND p.status = 'paid'
        """, (user_id,))
        stats = dict(cursor.fetchone())
        base_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB') AS currency,
                   COALESCE(SUM(COALESCE(payable_amount_minor, payable_amount_cents, 0)), 0) AS amount_minor
            FROM payments
            WHERE user_id = ? AND status = 'paid' AND intent_version = 1
            GROUP BY COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB')
            """,
            (user_id,),
        ).fetchall()
        stats['base_totals'] = {
            str(row['currency']): int(row['amount_minor'] or 0)
            for row in base_rows
        }
        
        # Unique rates
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
    Receives payment statistics for the last 24 hours.
    
    Returns:
        Dictionary with statistics:
        - paid_count: number of successful payments
        - paid_cents: amount of successful ones in cents
        - paid_stars: sum of successful ones in stars
        - pending_count: number of pending (unpaid)
    """
    with get_db() as conn:
        # 1. We count USDT (crypto)
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(intent_version, 0) != 1 THEN COALESCE(final_amount_cents, amount_cents, 0) ELSE 0 END), 0) as total_cents
            FROM payments
            WHERE status = 'paid' 
            AND payment_type = 'crypto'
            AND paid_at >= datetime('now', '-1 day')
        """)
        crypto_row = cursor.fetchone()
        
        # 2. Count Stars
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(intent_version, 0) != 1 THEN COALESCE(final_amount_stars, amount_stars, 0) ELSE 0 END), 0) as total_stars
            FROM payments
            WHERE status = 'paid' 
            AND payment_type = 'stars'
            AND paid_at >= datetime('now', '-1 day')
        """)
        stars_row = cursor.fetchone()
        
        # 3. We count TG payments (historical payment_type 'cards')
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(p.intent_version, 0) != 1 THEN COALESCE(p.final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid' 
            AND p.payment_type = 'cards'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        cards_row = cursor.fetchone()
        
        # 4. We count YuKassa (historical payment_type 'yookassa_qr')
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(p.intent_version, 0) != 1 THEN COALESCE(p.final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'yookassa_qr'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        qr_row = cursor.fetchone()

        # 5. We count WATA (Card/SBP - Rubles)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(p.intent_version, 0) != 1 THEN COALESCE(p.final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'wata'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        wata_row = cursor.fetchone()

        # 6. Count Platega (Rubles)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(p.intent_version, 0) != 1 THEN COALESCE(p.final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_rub
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.status = 'paid'
            AND p.payment_type = 'platega'
            AND p.paid_at >= datetime('now', '-1 day')
        """)
        platega_row = cursor.fetchone()

        # 7. We count Cardlink (Card/SBP - Rubles)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(CASE WHEN COALESCE(p.intent_version, 0) != 1 THEN COALESCE(p.final_amount_cents, t.price_rub * 100, 0) ELSE 0 END), 0) / 100.0 as total_rub
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
        
        base_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB') AS currency,
                   COALESCE(SUM(COALESCE(payable_amount_minor, payable_amount_cents, 0)), 0) AS amount_minor
            FROM payments
            WHERE status = 'paid' AND intent_version = 1
              AND paid_at >= datetime('now', '-1 day')
            GROUP BY COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB')
            """
        ).fetchall()
        return {
            'paid_count': paid_count,
            'paid_cents': total_cents,
            'paid_stars': total_stars,
            'paid_rub': total_rub,
            'pending_count': 0,
            'paid_base': {
                str(row['currency']): int(row['amount_minor'] or 0)
                for row in base_rows
            },
        }

def get_key_payments_history(key_id: int) -> List[Dict[str, Any]]:
    """
    Retrieves payment history for a specific key.
    
    Args:
        key_id: Key ID
    
    Returns:
        List of payments sorted by date (descending).
    """
    with get_db() as conn:
        from database.db_business_operations import create_business_operation_tables, get_key_operation_history

        create_business_operation_tables(conn)
        cursor = conn.execute("""
            SELECT 
                p.id, p.paid_at, p.payment_type, p.amount_cents, p.amount_stars,
                p.final_amount_cents, p.final_amount_stars, p.promo_code,
                p.intent_version, p.base_currency,
                p.payable_amount_minor, p.payable_amount_cents,
                t.name as tariff_name, t.price_rub,
                'payment' AS history_type
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.vpn_key_id = ? AND p.status = 'paid'
            ORDER BY p.paid_at DESC
        """, (key_id,))
        rows = [dict(row) for row in cursor.fetchall()]
    rows.extend(get_key_operation_history(key_id))
    return _sort_key_history_rows(rows)

def _int_to_base62(num: int) -> str:
    """
    Converts a number to a base62 string.
    
    Args:
        num: Positive integer
        
    Returns:
        Base62 string (0-9, A-Z, a-z)
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
    Creates a pending order and generates a unique order_id.
    
    Order_id is generated from the internal record ID in base62 format,
    which guarantees uniqueness and compliance with the cryptoprocessing format
    (max 8 characters A-Za-z0-9).
    
    Args:
        user_id: Internal user ID
        tariff_id: Tariff ID (can be None for crypto)
        payment_type: 'crypto', 'stars' or None (if selected during payment)
        vpn_key_id: ID of the key to renew (None for a new key)
    
    Returns:
        Tuple (payment_id, order_id)
    """
    tariff = get_tariff_by_id(tariff_id) if tariff_id else None
    
    with get_db() as conn:
        # Step 1: create a record with a temporary order_id
        cursor = conn.execute("""
            INSERT INTO payments 
            (user_id, tariff_id, order_id, payment_type, vpn_key_id, 
             amount_cents, amount_stars, period_days, status, paid_at,
             base_currency, nominal_amount_minor, payable_amount_minor,
             nominal_amount_cents, payable_amount_cents)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?, ?, ?)
        """, (
            user_id, tariff_id, payment_type, vpn_key_id,
            int(tariff['price_minor'] or 0) if tariff else 0,
            0,
            tariff['duration_days'] if tariff else None,
            str(tariff.get('base_currency') or 'RUB') if tariff else 'RUB',
            int(tariff['price_minor'] or 0) if tariff else 0,
            int(tariff['price_minor'] or 0) if tariff else 0,
            int(tariff['price_minor'] or 0) if tariff else 0,
            int(tariff['price_minor'] or 0) if tariff else 0,
        ))
        payment_id = cursor.lastrowid
        
        # Step 2: generate order_id from post ID (base62)
        # Add the prefix '00' to avoid conflicts with external IDs
        order_id = "00" + _int_to_base62(payment_id)
        
        # Step 3: update order_id
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
    Creates an immediately paid order (for external payments).
    
    Used when payment came from outside (without a prior pending order).
    
    Args:
        order_id: External order ID
        user_id: User ID
        tariff_id: Tariff ID
        payment_type: Payment type ('crypto', 'stars')
        amount_cents: Amount in cents
        amount_stars: Amount in stars
        period_days: Validity period
        
    Returns:
        True if successful
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
    Finds payment by order_id.
    
    Args:
        order_id: Unique order ID
    
    Returns:
        Dictionary with payment data or None
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
    Completes the payment: changes the status to 'paid'.
    
    Args:
        order_id: Order ID
    
    Returns:
        True if successful
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


def reopen_paid_order(order_id: str) -> bool:
    """Returns an API order to pending when fulfillment failed after settlement."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'pending', paid_at = NULL
            WHERE order_id = ? AND status = 'paid'
            """,
            (str(order_id),),
        )
        if cursor.rowcount > 0:
            logger.warning("Order %s reopened after fulfillment failure", order_id)
            return True
        return False


def save_payment_balance_deduction(
    order_id: str,
    amount_minor: int | None = None,
    *,
    amount_cents: int | None = None,
) -> bool:
    """Persists the base-money part that must be debited after settlement."""
    raw_amount = amount_minor if amount_minor is not None else amount_cents
    amount = max(0, int(raw_amount or 0))
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET balance_deduct_cents = ?, balance_deduct_minor = ?
            WHERE order_id = ?
            """,
            (amount, amount, str(order_id)),
        )
        return cursor.rowcount > 0


def cancel_pending_order(order_id: str) -> bool:
    """Marks a provider-canceled pending order and releases its promo reservation."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'canceled'
            WHERE order_id = ? AND status = 'pending'
            """,
            (str(order_id),),
        )
        if cursor.rowcount > 0:
            conn.execute(
                """
                UPDATE promo_redemptions
                SET status = 'canceled'
                WHERE order_id = ? AND status = 'reserved'
                """,
                (str(order_id),),
            )
            return True
        return False

def update_order_tariff(order_id: str, tariff_id: int, payment_type: Optional[str] = None) -> bool:
    """
    Updates the tariff and amounts in the order.
    
    Args:
        order_id: Order ID
        tariff_id: ID of the new tariff
        payment_type: Payment type (optional)
    
    Returns:
        True if successful
    """
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        return False
        
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE payments 
            SET tariff_id = ?, 
                amount_cents = ?, 
                nominal_amount_minor = ?,
                payable_amount_minor = ?,
                nominal_amount_cents = ?,
                payable_amount_cents = ?,
                base_currency = ?,
                amount_stars = ?, 
                period_days = ?,
                payment_type = COALESCE(?, payment_type)
            WHERE order_id = ?
        """, (
            tariff_id, 
            int(tariff['price_minor'] or 0),
            int(tariff['price_minor'] or 0),
            int(tariff['price_minor'] or 0),
            int(tariff['price_minor'] or 0),
            int(tariff['price_minor'] or 0),
            str(tariff.get('base_currency') or 'RUB'),
            0,
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
    Updates the payment type in the order.
    
    Args:
        order_id: Order ID
        payment_type: New payment type ('crypto', 'stars')
        
    Returns:
        True if successful
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
    Links the created VPN key to the payment.
    
    Args:
        order_id: Order ID
        vpn_key_id: Key ID
    
    Returns:
        True if successful
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
    Checks whether the order has already been paid.
    
    Args:
        order_id: Order ID
    
    Returns:
        True if status = 'paid'
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
    Retrieves payment history by key.
    
    Args:
        key_id: Key ID
    
    Returns:
        List of payments with tariff names
    """
    with get_db() as conn:
        from database.db_business_operations import create_business_operation_tables, get_key_operation_history

        create_business_operation_tables(conn)
        cursor = conn.execute("""
            SELECT p.*, t.name as tariff_name, t.price_rub, 'payment' AS history_type
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.vpn_key_id = ? AND p.status = 'paid'
            ORDER BY p.paid_at DESC
        """, (key_id,))
        rows = [dict(row) for row in cursor.fetchall()]
    rows.extend(get_key_operation_history(key_id))
    return _sort_key_history_rows(rows)


def _sort_key_history_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get('paid_at') or ''), reverse=True)

def get_referral_levels() -> List[Dict[str, Any]]:
    """
    Get all levels of the referral system.
    
    Returns:
        List [{level_number, percent, enabled}, ...]
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT level_number, percent, enabled FROM referral_levels ORDER BY level_number"
        )
        return [dict(row) for row in cursor.fetchall()]

def get_active_referral_levels() -> List[tuple]:
    """
    Get only included levels.
    
    Returns:
        List of tuples [(level_num, percent), ...]
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT level_number, percent FROM referral_levels WHERE enabled = 1 ORDER BY level_number"
        )
        return [(row['level_number'], row['percent']) for row in cursor.fetchall()]

def update_referral_level(level_number: int, percent: int, enabled: bool) -> bool:
    """
    Update the level of the referral system.
    
    Args:
        level_number: Level number (1, 2, 3)
        percent: Percent (1-100)
        enabled: Whether the level is enabled
    
    Returns:
        True if successful
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
    Statistics on included levels of the referral program.
    
    Args:
        user_id: Internal user ID (referrer)
    
    Returns:
        List [{level, count, total_reward_cents, total_reward_days}, ...]
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
                COALESCE(SUM(total_reward_minor), 0) as total_reward_minor,
                MAX(reward_currency) as reward_currency,
                COALESCE(SUM(total_reward_days), 0) as total_reward_days
            FROM referral_stats
            WHERE referrer_id = ?
            GROUP BY level
            ORDER BY level
        """, (user_id,))
        rewards = {row['level']: dict(row) for row in cursor.fetchall()}
        
        # Total number of invitees by level
        # We use recursive CTE (WITH RECURSIVE) to obtain a referral tree
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
                'total_reward_minor': 0,
                'reward_currency': 'RUB',
                'total_reward_days': 0
            })
            # Replace 'count' with 'total_count' to show all invitees
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
    Update referral statistics (INSERT ON CONFLICT DO UPDATE).
    
    Args:
        referrer_id: Referrer ID
        referral_id: Referral ID
        level: Level (1, 2, 3)
        reward_cents: Reward in kopecks
        reward_days: Reward in days
    
    Returns:
        True if successful
    """
    with get_db() as conn:
        conn.execute("""
            INSERT INTO referral_stats (
                referrer_id, referral_id, level, total_payments_count,
                total_reward_cents, total_reward_minor, reward_currency,
                total_reward_days
            )
            VALUES (?, ?, ?, 1, ?, ?, COALESCE(
                (SELECT value FROM settings WHERE key = 'base_currency'), 'RUB'
            ), ?)
            ON CONFLICT(referrer_id, referral_id, level) DO UPDATE SET
                total_payments_count = total_payments_count + 1,
                total_reward_cents = total_reward_cents + excluded.total_reward_cents,
                total_reward_minor = total_reward_minor + excluded.total_reward_minor,
                reward_currency = excluded.reward_currency,
                total_reward_days = total_reward_days + excluded.total_reward_days
        """, (referrer_id, referral_id, level, reward_cents, reward_cents, reward_days))
        return True

def is_referral_enabled() -> bool:
    """Is the referral system enabled?"""
    return get_setting('referral_enabled', '0') == '1'

def get_referral_reward_type() -> str:
    """Accrual type: 'days' or 'balance'."""
    return get_setting('referral_reward_type', 'days')

def get_referral_conditions_text() -> str:
    """Text of the terms and conditions of the referral program."""
    return get_setting('referral_conditions_text', '')

def parse_referral_notification_levels(raw: Optional[str]) -> List[int]:
    """
    Parses CSV of referral notification levels.

    Valid values: 1, 2, 3. Empty or invalid value
    is treated as the default first level.
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
    """Levels at which the referral manager receives hidden notifications."""
    return parse_referral_notification_levels(
        get_setting('referral_notification_levels', '1')
    )

def is_referral_new_ref_notifications_enabled() -> bool:
    """Are hidden notifications about new referrals enabled?"""
    return get_setting('referral_new_ref_notifications_enabled', '0') == '1'

def is_referral_purchase_notifications_enabled() -> bool:
    """Are hidden notifications enabled for referral purchases?"""
    return get_setting('referral_purchase_notifications_enabled', '0') == '1'

def get_referral_new_ref_notification_text() -> str:
    """The text of the hidden notification about a new referral."""
    value = get_setting('referral_new_ref_notification_text')
    if not value:
        raise RuntimeError("Required setting 'referral_new_ref_notification_text' is empty")
    return value

def get_referral_purchase_notification_text() -> str:
    """The text of the hidden referral purchase notification."""
    value = get_setting('referral_purchase_notification_text')
    if not value:
        raise RuntimeError("Required setting 'referral_purchase_notification_text' is empty")
    return value

def get_referral_notification_settings() -> Dict[str, Any]:
    """Current state of hidden referral notifications for read-only output."""
    return {
        'new_ref_enabled': is_referral_new_ref_notifications_enabled(),
        'new_ref_text_set': bool(get_referral_new_ref_notification_text().strip()),
        'purchase_enabled': is_referral_purchase_notifications_enabled(),
        'purchase_text_set': bool(get_referral_purchase_notification_text().strip()),
        'levels': get_referral_notification_levels(),
    }

def update_referral_setting(key: str, value: str) -> bool:
    """
    Update the referral system settings.
    
    Args:
        key: Setting key
        value: Value
    
    Returns:
        True if successful
    """
    set_setting(key, value)
    return True
