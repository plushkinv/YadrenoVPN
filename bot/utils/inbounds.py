"""
Общие правила работы с inbound'ами панели.
"""
from typing import Any, Dict, List, Tuple


IGNORED_INBOUND_PREFIX = "--!"


def is_ignored_inbound(inbound: Dict[str, Any]) -> bool:
    """True, если inbound скрыт от бота через префикс в начале remark."""
    if not isinstance(inbound, dict):
        return False
    remark = inbound.get("remark") or ""
    return str(remark).lstrip().startswith(IGNORED_INBOUND_PREFIX)


def split_ignored_inbounds(
    inbounds: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Разделяет inbound'ы на видимые для бота и скрытые."""
    visible: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for inbound in inbounds:
        if is_ignored_inbound(inbound):
            ignored.append(inbound)
        else:
            visible.append(inbound)
    return visible, ignored


def filter_visible_inbounds(inbounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Возвращает только inbound'ы без служебного префикса скрытия."""
    visible, _ = split_ignored_inbounds(inbounds)
    return visible
