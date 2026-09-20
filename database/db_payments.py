import sqlite3
import logging
import secrets
import string
import datetime
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db
from .payment_semantics import paid_key_purchase_predicate

logger = logging.getLogger(__name__)
from .db_settings import get_setting, set_setting

__all__ = [
    'get_user_payments_stats',
    'get_user_payment_snapshot_stats',
    'get_daily_payments_stats',
    'get_key_payments_history',
    'is_tariff_payment_target_allowed',
    'is_key_tariff_group_allowed',
    'find_order_by_order_id',
    'find_latest_paid_order_for_key',
    'update_payment_key_id',
    'save_payment_balance_deduction',
    'cancel_pending_order',
    'get_key_payments_history',
    'get_referral_levels',
    'get_active_referral_levels',
    'update_referral_level',
    'get_referral_stats',
    'get_user_referral_snapshot_stats',
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


def _tariff_payment_target_allowed(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    tariff_id: int,
    vpn_key_id: Optional[int] = None,
    require_active: bool = True,
) -> bool:
    """Validates a user payment target without trusting callback data."""
    tariff = conn.execute(
        """
        SELECT id, group_id, is_active, system_type
        FROM tariffs
        WHERE id = ?
        """,
        (int(tariff_id),),
    ).fetchone()
    if (
        tariff is None
        or (require_active and not bool(tariff['is_active']))
        or tariff['system_type'] is not None
    ):
        return False
    if vpn_key_id is None:
        return True
    key = conn.execute(
        """
        SELECT vk.user_id, vk.tariff_id, vk.server_id,
               COALESCE(json_extract(ke.tariff_json, '$.group_id'), t.group_id) AS group_id
        FROM vpn_keys vk
        LEFT JOIN tariffs t ON t.id = vk.tariff_id
        LEFT JOIN key_entitlements ke ON ke.key_id = vk.id
        WHERE vk.id = ?
        """,
        (int(vpn_key_id),),
    ).fetchone()
    return bool(key and int(key['user_id']) == int(user_id) and
                _key_tariff_group_allowed_with_conn(conn, vpn_key_id, tariff['group_id'], key=key))


def _key_tariff_group_allowed_with_conn(conn, key_id, group_id, *, key=None):
    if key is None:
        key = conn.execute('SELECT k.user_id,k.tariff_id,k.server_id, '
                           "COALESCE(json_extract(e.tariff_json,'$.group_id'),t.group_id) AS group_id "
                           'FROM vpn_keys k LEFT JOIN tariffs t ON t.id=k.tariff_id '
                           'LEFT JOIN key_entitlements e ON e.key_id=k.id WHERE k.id=?', (key_id,)).fetchone()
    if key and key['tariff_id'] is None:
        from core.panel_identity import physical_panel_key
        imported = conn.execute('SELECT 1 FROM subscription_import_members m '
                                'JOIN subscription_imports i ON i.id=m.import_id WHERE m.key_id=? AND i.user_id=?',
                                (key_id, key['user_id'])).fetchone()
        if not imported:
            return False
        server = conn.execute('SELECT * FROM servers WHERE id=?', (key['server_id'],)).fetchone()
        if not server:
            return False
        endpoint = physical_panel_key(dict(server))
        peers = [row['id'] for row in conn.execute('SELECT * FROM servers')
                 if physical_panel_key(dict(row)) == endpoint]
        return any(conn.execute('SELECT 1 FROM server_groups WHERE server_id=? AND group_id=?',
                                (server_id, group_id)).fetchone() for server_id in peers)
    return bool(key and key['group_id'] is not None and int(key['group_id']) == int(group_id))


def is_key_tariff_group_allowed(key_id, group_id):
    """Use bought group terms, or the actual configured panel for unknown imports."""
    with get_db() as conn:
        return _key_tariff_group_allowed_with_conn(conn, key_id, group_id)


def is_tariff_payment_target_allowed(
    *,
    user_id: int,
    tariff_id: int,
    vpn_key_id: Optional[int] = None,
    require_active: bool = True,
) -> bool:
    """Returns whether an active ordinary tariff can be bought for a key."""
    with get_db() as conn:
        return _tariff_payment_target_allowed(
            conn,
            user_id=int(user_id),
            tariff_id=int(tariff_id),
            vpn_key_id=vpn_key_id,
            require_active=bool(require_active),
        )

def get_user_payments_stats(user_id: int) -> Dict[str, Any]:
    """
    Gets user payment statistics.
    
    Args:
        user_id: Internal user ID
    
    Returns a count, last-payment time, totals by currency and tariff names.
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT
                COUNT(*) as total_payments,
                MAX(paid_at) as last_payment_at
            FROM payments
            WHERE user_id = ? AND status = 'paid'
        """, (user_id,))
        stats = dict(cursor.fetchone())
        base_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB') AS currency,
                   COALESCE(SUM(payable_amount_minor), 0) AS amount_minor
            FROM payments
            WHERE user_id = ? AND status = 'paid'
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


def get_user_payment_snapshot_stats(user_id: int) -> Dict[str, Any]:
    """Returns bounded payment aggregates for the extension user snapshot."""
    paid_key_predicate = paid_key_purchase_predicate('p')
    with get_db() as conn:
        summary = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN p.status = 'paid' THEN 1 ELSE 0 END), 0)
                    AS successful_count,
                COALESCE(SUM(CASE WHEN {paid_key_predicate} THEN 1 ELSE 0 END), 0)
                    AS paid_key_count,
                MAX(CASE WHEN p.status = 'paid' THEN datetime(p.paid_at) END)
                    AS last_payment_at
            FROM payments p
            WHERE p.user_id = ?
            """,
            (int(user_id),),
        ).fetchone()
        amount_rows = conn.execute(
            """
            SELECT UPPER(base_currency) AS currency,
                   COALESCE(SUM(payable_amount_minor), 0) AS amount_minor
            FROM payments
            WHERE user_id = ?
              AND status = 'paid'
              AND TRIM(COALESCE(base_currency, '')) <> ''
            GROUP BY UPPER(base_currency)
            ORDER BY UPPER(base_currency)
            """,
            (int(user_id),),
        ).fetchall()

    paid_key_count = int(summary['paid_key_count'] or 0)
    return {
        'successful_count': int(summary['successful_count'] or 0),
        'paid_key_count': paid_key_count,
        'has_ever_paid_key': paid_key_count > 0,
        'last_payment_at': summary['last_payment_at'],
        'amounts_by_currency': {
            str(row['currency']): int(row['amount_minor'] or 0)
            for row in amount_rows
        },
    }


def get_daily_payments_stats() -> Dict[str, Any]:
    """Return 24-hour payment counts and canonical totals by currency."""
    with get_db() as conn:
        summary = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END), 0)
                    AS paid_count,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0)
                    AS pending_count
            FROM payments
            WHERE created_at >= datetime('now', '-1 day')
               OR paid_at >= datetime('now', '-1 day')
            """
        ).fetchone()
        amount_rows = conn.execute(
            """
            SELECT UPPER(base_currency) AS currency,
                   COALESCE(SUM(payable_amount_minor), 0) AS amount_minor
            FROM payments
            WHERE status = 'paid'
              AND paid_at >= datetime('now', '-1 day')
              AND TRIM(COALESCE(base_currency, '')) <> ''
            GROUP BY UPPER(base_currency)
            """
        ).fetchall()
        return {
            'paid_count': int(summary['paid_count'] or 0),
            'pending_count': int(summary['pending_count'] or 0),
            'paid_base': {
                str(row['currency']): int(row['amount_minor'] or 0)
                for row in amount_rows
            },
        }

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


def find_latest_paid_order_for_key(key_id: int) -> Optional[Dict[str, Any]]:
    """Return the latest paid order linked to one key."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.*, t.duration_days, t.name AS tariff_name
            FROM payments p
            LEFT JOIN tariffs t ON p.tariff_id = t.id
            WHERE p.vpn_key_id = ? AND p.status = 'paid'
            ORDER BY p.paid_at DESC, p.id DESC
            LIMIT 1
            """,
            (int(key_id),),
        ).fetchone()
        return dict(row) if row else None

def save_payment_balance_deduction(
    order_id: str,
    amount_minor: int,
) -> bool:
    """Persists the base-money part that must be debited after settlement."""
    amount = max(0, int(amount_minor or 0))
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET balance_deduct_minor = ?
            WHERE order_id = ? AND intent_version = 1
            """,
            (amount, str(order_id)),
        )
        return cursor.rowcount > 0


def cancel_pending_order(order_id: str) -> bool:
    """Marks a provider-canceled pending order and releases its promo reservation."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'canceled'
            WHERE order_id = ? AND status = 'pending' AND intent_version = 1
            """,
            (str(order_id),),
        )
        if cursor.rowcount > 0:
            conn.execute(
                """
                UPDATE payment_provider_orders
                SET status = 'canceled', updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ? AND status = 'pending'
                """,
                (str(order_id),),
            )
            conn.execute(
                """
                UPDATE payment_auto_checks
                SET state = 'canceled', next_check_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ? AND state IN ('active', 'exhausted')
                """,
                (str(order_id),),
            )
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
            WHERE order_id = ? AND intent_version = 1
        """, (vpn_key_id, order_id))
        return cursor.rowcount > 0

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
            SELECT p.*, t.name as tariff_name, 'payment' AS history_type
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


def get_user_referral_snapshot_stats(user_id: int) -> Dict[str, Any]:
    """Returns bounded referral-tree and accumulated reward aggregates."""
    with get_db() as conn:
        counts = conn.execute(
            """
            WITH RECURSIVE referral_tree(id, level) AS (
                SELECT id, 1
                FROM users
                WHERE referred_by = ?
                UNION ALL
                SELECT u.id, referral_tree.level + 1
                FROM users u
                JOIN referral_tree ON u.referred_by = referral_tree.id
                WHERE referral_tree.level < 3
            )
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN level = 1 THEN 1 ELSE 0 END), 0)
                    AS direct_count
            FROM referral_tree
            """,
            (int(user_id),),
        ).fetchone()
        reward_summary = conn.execute(
            """
            SELECT
                COUNT(DISTINCT CASE
                    WHEN COALESCE(total_payments_count, 0) > 0 THEN referral_id
                END) AS paying_count,
                COALESCE(SUM(total_reward_days), 0) AS reward_days
            FROM referral_stats
            WHERE referrer_id = ?
            """,
            (int(user_id),),
        ).fetchone()
        reward_rows = conn.execute(
            """
            SELECT
                CASE
                    WHEN UPPER(TRIM(COALESCE(reward_currency, ''))) IN ('RUB', 'USD')
                        THEN UPPER(TRIM(reward_currency))
                    ELSE 'RUB'
                END AS currency,
                COALESCE(SUM(CASE
                    WHEN COALESCE(total_reward_minor, 0) != 0
                        THEN total_reward_minor
                    ELSE COALESCE(total_reward_cents, 0)
                END), 0) AS amount_minor
            FROM referral_stats
            WHERE referrer_id = ?
            GROUP BY CASE
                WHEN UPPER(TRIM(COALESCE(reward_currency, ''))) IN ('RUB', 'USD')
                    THEN UPPER(TRIM(reward_currency))
                ELSE 'RUB'
            END
            ORDER BY currency
            """,
            (int(user_id),),
        ).fetchall()

    return {
        'direct_count': int(counts['direct_count'] or 0),
        'total_count': int(counts['total_count'] or 0),
        'paying_count': int(reward_summary['paying_count'] or 0),
        'reward_amounts_by_currency': {
            str(row['currency']): int(row['amount_minor'] or 0)
            for row in reward_rows
        },
        'reward_days': int(reward_summary['reward_days'] or 0),
    }


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
