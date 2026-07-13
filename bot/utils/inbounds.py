"""
General rules for working with inbound panels.
"""
from typing import Any, Dict, List, Tuple


IGNORED_INBOUND_PREFIX = "--!"


def is_ignored_inbound(inbound: Dict[str, Any]) -> bool:
    """True if inbound is hidden from the bot through the prefix at the beginning of remark."""
    if not isinstance(inbound, dict):
        return False
    remark = inbound.get("remark") or ""
    return str(remark).lstrip().startswith(IGNORED_INBOUND_PREFIX)


def split_ignored_inbounds(
    inbounds: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Divides inbounds into those visible to the bot and hidden."""
    visible: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for inbound in inbounds:
        if is_ignored_inbound(inbound):
            ignored.append(inbound)
        else:
            visible.append(inbound)
    return visible, ignored


def filter_visible_inbounds(inbounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Returns only inbounds without the hidden service prefix."""
    visible, _ = split_ignored_inbounds(inbounds)
    return visible
