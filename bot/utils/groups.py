"""
Utilities for working with tariff groups in the user area.
"""
from database.requests import (
    get_all_groups,
    get_groups_count,
    get_tariffs_by_group,
    get_active_servers_by_group,
    get_tariff_group_id,
    get_all_tariffs,
    get_active_servers
)


def is_tariff_available_for_payment(
    tariff: dict | None,
    key: dict | None = None,
) -> bool:
    """Validates an ordinary active tariff against an optional key group."""
    if (
        tariff is None
        or tariff.get('system_type') is not None
        or not bool(tariff.get('is_active', 1))
    ):
        return False
    if key is None:
        return True
    if key.get('tariff_id') is None and key.get('id') and key.get('user_id'):
        from database.requests import is_tariff_payment_target_allowed
        return is_tariff_payment_target_allowed(user_id=key['user_id'], tariff_id=tariff['id'], vpn_key_id=key['id'])
    return int(tariff.get('group_id') or 1) == int(
        key.get('tariff_group_id') or 1
    )


def get_tariffs_for_key_renewal(key: dict):
    """Unknown imported plans are selected from their configured panel groups."""
    if key.get('tariff_id') is not None:
        if key.get('id'):
            from database.requests import get_key_entitlement
            terms = get_key_entitlement(key['id'])
            if terms and get_groups_count() > 1:
                return get_tariffs_by_group(int(terms['tariff'].get('group_id') or 1))
        return get_tariffs_for_renewal(key['tariff_id'])
    return [tariff for tariff in get_all_tariffs(include_hidden=False) if is_tariff_available_for_payment(tariff, key)]


def build_groups_data_for_tariffs():
    """
    Generates data for grouped display of tariffs.
    
    The group is shown ONLY if it contains BOTH active tariffs AND active servers (K1).
    With 1 group - returns None (without grouping).
    
    Returns:
        list[dict] or None: List of dictionaries {'group': {...}, 'tariffs': [...]}
                             or None if grouping is not needed
    """
    groups_count = get_groups_count()
    if groups_count <= 1:
        return None
    
    groups = get_all_groups()
    groups_data = []
    
    for group in groups:
        tariffs = get_tariffs_by_group(group['id'])
        servers = get_active_servers_by_group(group['id'])
        
        # K1: the group is visible only if there are BOTH tariffs AND servers
        if tariffs and servers:
            groups_data.append({
                'group': group,
                'tariffs': tariffs
            })
    
    # If there is only 1 visible group left, we do not show the headers
    if len(groups_data) <= 1:
        return None
    
    return groups_data


def get_tariffs_for_renewal(key_tariff_id: int):
    """
    Retrieves rates available for key renewal.
    If >1 group - only tariffs from the group of the current key.
    For group 1 - all active tariffs.
    
    Args:
        key_tariff_id: Tariff ID of the current key
        
    Returns:
        List of tariffs for renewal
    """
    groups_count = get_groups_count()
    
    if groups_count <= 1:
        return get_all_tariffs(include_hidden=False)
    
    # Filter by key group
    group_id = get_tariff_group_id(key_tariff_id)
    return get_tariffs_by_group(group_id)


def get_servers_for_key(key_tariff_id: int):
    """
    Gets the servers available for the key (replacement or creation).
    If >1 group - only servers from the tariff group.
    With group 1 - all active servers.
    
    Args:
        key_tariff_id: Key tariff ID
        
    Returns:
        Server list
    """
    groups_count = get_groups_count()
    
    if groups_count <= 1:
        return get_active_servers()
    
    # Filter by tariff group
    group_id = get_tariff_group_id(key_tariff_id)
    return get_active_servers_by_group(group_id)
