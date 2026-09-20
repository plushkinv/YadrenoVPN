"""Use persisted terms for new orders and unchanged live-tariff semantics for old ones."""
from database import requests as db


def order_tariff(order_id: str, tariff_id: int | None):
    terms = db.get_payment_order_terms(order_id)
    if terms is not None:
        return dict(terms['tariff']) if tariff_id else None
    return db.get_tariff_by_id(tariff_id) if tariff_id else None


def key_servers(key: dict):
    """Keep purchased group membership while preserving the legacy selector."""
    from bot.utils.groups import get_servers_for_key
    entitlement = db.get_key_entitlement(key['id'])
    if entitlement and db.get_groups_count() > 1:
        return db.get_active_servers_by_group(int(entitlement['tariff'].get('group_id') or 1))
    return get_servers_for_key(key['tariff_id']) if key.get('tariff_id') else db.get_active_servers()
