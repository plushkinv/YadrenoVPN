"""Shared access-plan arithmetic; callers own validation and transaction receipts."""


def apply_key_plan_with_conn(conn, key, tariff, days, traffic_limit_bytes):
    """Extend cumulative quota without resetting identity or usage counters."""
    current_limit = int(key['traffic_limit'] or 0)
    purchased_limit = max(0, int(traffic_limit_bytes or 0))
    new_limit = (0 if purchased_limit == 0 else
                 (current_limit if current_limit > 0 else int(key['traffic_used'] or 0)) + purchased_limit)
    custom = bool(tariff.get('extension_custom_terms'))
    return conn.execute(
        """UPDATE vpn_keys SET expires_at = CASE WHEN ? = 0 THEN NULL
               ELSE MAX(datetime('now'), datetime(
                   CASE WHEN expires_at > datetime('now') THEN expires_at ELSE datetime('now') END, ?)) END,
               tariff_id=?, traffic_limit=?, traffic_limit_override=?, max_ips_override=?,
               traffic_notified_pct=100 WHERE id=? AND user_id=?""",
        (int(days), f'{int(days):+} days', tariff['id'], new_limit,
         purchased_limit if custom else None, tariff['max_ips'] if custom else None,
         key['id'], key['user_id']),
    ).rowcount > 0


def set_custom_key_overrides_with_conn(conn, key_id, tariff):
    if tariff.get('extension_custom_terms'):
        conn.execute('UPDATE vpn_keys SET traffic_limit_override=?,max_ips_override=? WHERE id=?',
                     (int(tariff['traffic_limit_gb']) * 1024**3, tariff['max_ips'], key_id))


def order_custom_tariff_with_conn(conn, order_id, tariff_id):
    """Only a persisted server-owned order snapshot authorizes a system tariff."""
    import json
    row = conn.execute('SELECT t.tariff_json FROM payment_order_terms t JOIN payments p ON p.order_id=t.order_id '
                       'WHERE t.order_id=? AND p.tariff_id=?', (order_id, tariff_id)).fetchone()
    tariff = json.loads(row['tariff_json']) if row else {}
    return tariff if tariff.get('extension_custom_terms') and tariff.get('id') == tariff_id else None
