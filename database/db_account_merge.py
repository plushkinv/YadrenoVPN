"""Move a phone account into its confirmed Telegram owner in the caller's transaction."""
from .connection import get_db
from core.results import CoreError

__all__ = ['resolve_account_id']


def resolve_account_id(user_id: int, *, _conn=None) -> int:
    from contextlib import nullcontext
    with (nullcontext(_conn) if _conn is not None else get_db()) as conn:
        row = conn.execute('SELECT merged_into_user_id FROM users WHERE id=?', (user_id,)).fetchone()
        return int(row['merged_into_user_id']) if row and row['merged_into_user_id'] else user_id


def _move(conn, table, columns, source, target, *, collisions=False, history=False):
    """Only fixed core-owned table/column names enter this transfer helper."""
    where = ' OR '.join(f'{name}=?' for name in columns)
    values = ', '.join(f'{name}=CASE WHEN {name}=? THEN ? ELSE {name} END' for name in columns)
    params = [item for _ in columns for item in (source, target)] + [source] * len(columns)
    conn.execute(f'UPDATE {"OR IGNORE " if collisions or history else ""}{table} SET {values} WHERE {where}', params)
    if history:
        # Keep both monetary/history rows, but the Telegram receipt retains the original reference.
        conn.execute(f"UPDATE {table} SET user_id=?, reference_id=reference_id || ':merged:' || id WHERE user_id=?",
                     (target, source))
    elif collisions:
        conn.execute(f'DELETE FROM {table} WHERE {where}', [source] * len(columns))


def _referrer(conn, source, target):
    for candidate in (target['referred_by'], source['referred_by']):
        current, seen = candidate, {source['id'], target['id']}
        while current and current not in seen:
            seen.add(current)
            row = conn.execute('SELECT referred_by FROM users WHERE id=?', (current,)).fetchone()
            current = row['referred_by'] if row else None
        if candidate and not current:
            return candidate
    return None


def merge_account_with_conn(conn, source_id: int, target_id: int, now: int, *, subscribers=()) -> str:
    source = conn.execute('SELECT * FROM users WHERE id=?', (source_id,)).fetchone()
    target = conn.execute('SELECT * FROM users WHERE id=?', (target_id,)).fetchone()
    if (not source or not target or source['telegram_id'] is not None or target['telegram_id'] is None
            or source['merged_into_user_id'] or target['merged_into_user_id'] or source_id == target_id):
        raise CoreError('link_conflict')
    if source['is_banned'] or target['is_banned']:
        raise CoreError('access_denied')
    credentials = conn.execute('SELECT * FROM account_credentials WHERE user_id=?', (source_id,)).fetchone()
    old_target = conn.execute('SELECT * FROM account_credentials WHERE user_id=?', (target_id,)).fetchone()
    conn.execute('DELETE FROM auth_challenges WHERE user_id IN (?,?) OR session_hash IN '
                 '(SELECT token_hash FROM account_sessions WHERE user_id IN (?,?)) OR phone IN '
                 '(SELECT phone FROM account_credentials WHERE user_id IN (?,?))', (source_id, target_id) * 3)
    if credentials:
        version = max(credentials['version'], old_target['version'] if old_target else 0) + 1
        conn.execute('DELETE FROM account_credentials WHERE user_id=?', (target_id,))
        conn.execute('UPDATE account_credentials SET user_id=?,version=?,updated_at=? WHERE user_id=?',
                     (target_id, version, now, source_id))
    conn.execute('UPDATE account_sessions SET revoked_at=? WHERE user_id IN (?,?)', (now, source_id, target_id))

    # These records have stable identities independent of their account owner.
    for table in ('vpn_keys', 'payments', 'support_threads', 'promo_link_visits', 'promo_redemptions',
                  'semantic_action_contexts', 'payment_offers', 'extension_scheduled_tasks', 'panel_identity_renames'):
        _move(conn, table, ('user_id',), source_id, target_id)
    _move(conn, 'promo_codes', ('issued_to_user_id',), source_id, target_id)
    _move(conn, 'payment_referral_effects', ('referrer_id', 'payer_id'), source_id, target_id)
    for table in ('trial_activations', 'lapsed_coupon_deliveries', 'account_operations'):
        _move(conn, table, ('user_id',), source_id, target_id, collisions=True)
    _move(conn, 'referral_stats', ('referrer_id', 'referral_id'), source_id, target_id, collisions=True)
    for table in ('balance_operations', 'key_operation_log'):
        _move(conn, table, ('user_id',), source_id, target_id, history=True)

    referrer = _referrer(conn, source, target)
    conn.execute('UPDATE users SET referred_by=? WHERE referred_by=? AND id!=?', (target_id, source_id, target_id))
    amount = int(source['personal_balance'] or 0)
    previous = int(target['personal_balance'] or 0)
    conn.execute('UPDATE users SET personal_balance=?,used_trial=?,last_key_number=?,referred_by=?, '
                 'active_promo_code_id=COALESCE(active_promo_code_id,?), '
                 'referral_coefficient=COALESCE(referral_coefficient,?) WHERE id=?',
                 (previous + amount, int(bool(source['used_trial'] or target['used_trial'])),
                  max(source['last_key_number'], target['last_key_number']), referrer,
                  source['active_promo_code_id'], source['referral_coefficient'], target_id))
    conn.execute('UPDATE users SET merged_into_user_id=?,personal_balance=0,referral_code=NULL,referred_by=NULL, '
                 'active_promo_code_id=NULL,username=NULL,first_name=NULL,last_name=NULL WHERE id=?', (target_id, source_id))
    currency = conn.execute("SELECT value FROM settings WHERE key='base_currency'").fetchone()
    reason = conn.execute("SELECT COALESCE(text_custom,text_default) FROM user_ui_texts "
                          "WHERE text_key='account.merge.balance_reason'").fetchone()[0]
    conn.execute('INSERT INTO balance_operations(user_id,operation_type,delta_cents,delta_minor,currency, '
                 'balance_before,balance_after,source,reason,reference_type,reference_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (target_id, 'credit', amount, amount, currency[0] if currency else 'RUB', previous, previous + amount,
                  'account_merge', reason, 'account_merge', str(source_id)))
    from .db_core_events import record_core_event_with_conn
    return record_core_event_with_conn(conn, event_name='user.merged', source_id=str(source_id),
        merged_accounts={'source_account_id': source_id, 'target_account_id': target_id}, subscribers=subscribers)
