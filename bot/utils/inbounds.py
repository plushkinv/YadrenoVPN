"""
General rules for working with inbound panels.
"""
from typing import Any, Dict, List, Tuple


IGNORED_INBOUND_PREFIX = "--!"
MTPROTO_PROTOCOL = "mtproto"


def inbound_protocol(inbound: Dict[str, Any]) -> str:
    """Return a normalized panel protocol name."""
    if not isinstance(inbound, dict):
        return ""
    return str(inbound.get("protocol") or "").strip().lower()


def is_mtproto_inbound(inbound: Dict[str, Any]) -> bool:
    """Whether the inbound is an MTProto proxy rather than a regular VPN key."""
    return inbound_protocol(inbound) == MTPROTO_PROTOCOL


def filter_regular_inbounds(inbounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return inbounds supported by the single-key flow (MTProto excluded)."""
    return [inbound for inbound in inbounds if not is_mtproto_inbound(inbound)]


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
