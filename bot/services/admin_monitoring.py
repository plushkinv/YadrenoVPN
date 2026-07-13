"""
Collection and formatting of monitoring for the admin panel.

Administrator handlers do not go directly to the database and panels: they receive a ready-made snapshot
and send it to the HTML renderers of this module.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.requests import (
    get_all_servers,
    get_daily_payments_stats,
    get_expiring_keys,
    get_keys_stats,
    get_new_users_count_today,
    get_users_stats,
)
from bot.services.vpn_api import format_traffic, get_client_from_server_data
from bot.utils.datetime_format import get_display_tzinfo
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)

LOAD_WARNING_THRESHOLD = 90.0
NODE_DISPLAY_LIMIT = 8
MAX_SUMMARY_PROBLEMS = 3


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_percent(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "—"
    if abs(number - round(number)) < 0.05:
        return f"{int(round(number))}%"
    return f"{number:.1f}%"


def _format_payments_sum(payments: Dict[str, Any]) -> str:
    parts: List[str] = []
    cents = _safe_int(payments.get("paid_cents"))
    rub = _safe_float(payments.get("paid_rub"), 0) or 0
    stars = _safe_int(payments.get("paid_stars"))

    if cents > 0:
        parts.append(f"${cents / 100:g}".replace(".", ","))
    if rub > 0:
        parts.append(f"{rub:g}".replace(".", ",") + " ₽")
    if stars > 0:
        parts.append(f"⭐{stars}")

    return " + ".join(parts) if parts else "0"


def _format_rate_pair(up: Any, down: Any) -> str:
    up_value = _safe_int(up)
    down_value = _safe_int(down)
    if up_value <= 0 and down_value <= 0:
        return ""
    return f"↑ {format_traffic(up_value)}/s ↓ {format_traffic(down_value)}/s"


def _is_xray_problem(node: Dict[str, Any]) -> bool:
    state = str(node.get("xrayState") or "").strip().lower()
    return state in {"error", "stop", "stopped"}


def _node_enabled(node: Dict[str, Any]) -> bool:
    return bool(node.get("enable", True))


def _normalize_node(raw: Dict[str, Any]) -> Dict[str, Any]:
    name = (
        raw.get("name")
        or raw.get("remark")
        or raw.get("address")
        or raw.get("guid")
        or f"node-{raw.get('id') or '?'}"
    )
    status = str(raw.get("status") or "unknown").strip().lower() or "unknown"
    cpu = _safe_float(raw.get("cpuPct"), 0) or 0
    mem = _safe_float(raw.get("memPct"), 0) or 0
    enabled = _node_enabled(raw)
    xray_problem = _is_xray_problem(raw)

    if not enabled:
        severity = 0
        problem = False
    elif status != "online":
        severity = 100
        problem = True
    elif xray_problem:
        severity = 90
        problem = True
    elif cpu >= LOAD_WARNING_THRESHOLD or mem >= LOAD_WARNING_THRESHOLD:
        severity = 50
        problem = True
    else:
        severity = 0
        problem = False

    node = dict(raw)
    node.update(
        {
            "name": str(name),
            "status": status,
            "enable": enabled,
            "cpuPct": cpu,
            "memPct": mem,
            "onlineCount": _safe_int(raw.get("onlineCount")),
            "clientCount": _safe_int(raw.get("clientCount")),
            "activeCount": _safe_int(raw.get("activeCount")),
            "disabledCount": _safe_int(raw.get("disabledCount")),
            "depletedCount": _safe_int(raw.get("depletedCount")),
            "netUp": _safe_int(raw.get("netUp")),
            "netDown": _safe_int(raw.get("netDown")),
            "latencyMs": _safe_int(raw.get("latencyMs")),
            "lastHeartbeat": _safe_int(raw.get("lastHeartbeat")),
            "problem": problem,
            "severity": severity,
            "xray_problem": xray_problem,
        }
    )
    return node


def _flatten_nodes(raw_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    def walk(items: List[Dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            children = item.get("children") or item.get("nodes") or []
            current = dict(item)
            current.pop("children", None)
            current.pop("nodes", None)
            result.append(_normalize_node(current))
            if isinstance(children, list):
                walk([child for child in children if isinstance(child, dict)])

    walk(raw_nodes)
    return result


def _panel_problem_line(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    server = entry["server"]
    if not entry.get("is_active"):
        return None
    if entry.get("panel_online"):
        return None
    name = escape_html(str(server.get("name") or "Панель"))
    error = escape_html(str(entry.get("error") or "нет подключения"))
    return {"severity": 120, "text": f"🔴 {name} — панель недоступна: {error}"}


def _node_problem_line(server: Dict[str, Any], node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not node.get("problem"):
        return None

    server_name = escape_html(str(server.get("name") or "Панель"))
    node_name = escape_html(str(node.get("name") or "Нода"))
    status = node.get("status")

    if status != "online":
        detail = escape_html(str(node.get("lastError") or "нет связи"))
        text = f"🔴 {server_name} / {node_name} — {detail}"
    elif node.get("xray_problem"):
        xray_state = escape_html(str(node.get("xrayState") or "Xray"))
        xray_error = escape_html(str(node.get("xrayError") or "").strip())
        suffix = f": {xray_error}" if xray_error else ""
        text = f"🟣 {server_name} / {node_name} — Xray {xray_state}{suffix}"
    else:
        text = (
            f"🟠 {server_name} / {node_name} — "
            f"CPU {_format_percent(node.get('cpuPct'))}, RAM {_format_percent(node.get('memPct'))}"
        )

    return {"severity": node.get("severity", 0), "text": text}


async def _collect_server_entry(server: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "server": server,
        "is_active": bool(server.get("is_active")),
        "panel_online": False,
        "stats": {},
        "nodes": [],
        "error": None,
    }

    if not entry["is_active"]:
        return entry

    try:
        client = get_client_from_server_data(server)
        stats = await client.get_stats()
        entry["stats"] = stats
        entry["panel_online"] = bool(stats.get("online"))
        if not entry["panel_online"]:
            entry["error"] = stats.get("error") or "нет подключения"
            return entry

        try:
            raw_nodes = await client.get_nodes()
            entry["nodes"] = _flatten_nodes(raw_nodes)
        except Exception as e:
            logger.debug(f"Не удалось получить ноды панели {server.get('name')}: {e}")
            entry["nodes"] = []
    except Exception as e:
        logger.warning(f"Ошибка получения мониторинга панели {server.get('name')}: {e}")
        entry["error"] = "ошибка подключения"

    return entry


async def collect_admin_monitoring_snapshot() -> Dict[str, Any]:
    """Collects a common snapshot for the main admin panel and the servers section."""
    servers = get_all_servers()
    entries = await asyncio.gather(*[_collect_server_entry(server) for server in servers]) if servers else []

    users = get_users_stats()
    keys = get_keys_stats()
    payments = get_daily_payments_stats()
    expiring_24h = len(get_expiring_keys(1))
    new_users = get_new_users_count_today()

    problems: List[Dict[str, Any]] = []
    for entry in entries:
        panel_problem = _panel_problem_line(entry)
        if panel_problem:
            problems.append(panel_problem)
        for node in entry.get("nodes", []):
            node_problem = _node_problem_line(entry["server"], node)
            if node_problem:
                problems.append(node_problem)

    problems.sort(key=lambda item: item.get("severity", 0), reverse=True)

    return {
        "collected_at": datetime.now(get_display_tzinfo()),
        "servers": entries,
        "users": users,
        "keys": keys,
        "payments": payments,
        "new_users": new_users,
        "expiring_24h": expiring_24h,
        "problems": problems,
    }


def _snapshot_counts(snapshot: Dict[str, Any]) -> Dict[str, int]:
    entries = snapshot.get("servers", [])
    active_panels = [entry for entry in entries if entry.get("is_active")]
    online_panels = [entry for entry in active_panels if entry.get("panel_online")]
    inactive_panels = [entry for entry in entries if not entry.get("is_active")]

    nodes = [node for entry in entries for node in entry.get("nodes", [])]
    enabled_nodes = [node for node in nodes if node.get("enable", True)]
    online_nodes = [node for node in enabled_nodes if node.get("status") == "online"]
    disabled_nodes = [node for node in nodes if not node.get("enable", True)]

    return {
        "panels_total": len(entries),
        "active_panels": len(active_panels),
        "online_panels": len(online_panels),
        "inactive_panels": len(inactive_panels),
        "nodes_total": len(enabled_nodes),
        "online_nodes": len(online_nodes),
        "disabled_nodes": len(disabled_nodes),
        "panel_online_clients": sum(
            _safe_int((entry.get("stats") or {}).get("online_clients"))
            for entry in online_panels
        ),
    }


def build_admin_summary_text(snapshot: Dict[str, Any]) -> str:
    """Generates a short summary of the main admin panel."""
    entries = snapshot.get("servers", [])
    if not entries:
        return (
            "⚙️ <b>Админ-панель</b>\n\n"
            "🖥️ Серверов пока нет.\n"
            "Добавьте первый сервер в разделе «Сервера»."
        )

    counts = _snapshot_counts(snapshot)
    problems = snapshot.get("problems", [])
    collected_at = snapshot.get("collected_at")
    updated = collected_at.strftime("%H:%M") if isinstance(collected_at, datetime) else "—"

    if counts["active_panels"] == 0:
        health_icon = "⚠️"
        health_text = "Мониторинг выключен"
    elif counts["online_panels"] == 0:
        health_icon = "🔴"
        health_text = "Все панели недоступны"
    elif problems:
        health_icon = "⚠️"
        health_text = "Есть проблемы"
    else:
        health_icon = "🟢"
        health_text = "Система работает"

    panel_suffix = f" | ⏸️ {counts['inactive_panels']} выкл." if counts["inactive_panels"] else ""
    node_suffix = f" | ⏸️ {counts['disabled_nodes']} выкл." if counts["disabled_nodes"] else ""

    lines = [
        "⚙️ <b>Админ-панель</b>",
        "",
        f"{health_icon} <b>{health_text}</b>",
        f"🖥️ Панели: {counts['online_panels']}/{counts['active_panels']} онлайн{panel_suffix}",
        f"🌐 Ноды: {counts['online_nodes']}/{counts['nodes_total']} онлайн{node_suffix}",
        f"🔑 Онлайн на панелях: {counts['panel_online_clients']}",
        f"🕒 Обновлено: {updated}",
        "",
    ]

    if problems:
        lines.append("⚠️ <b>Требуют внимания</b>")
        for problem in problems[:MAX_SUMMARY_PROBLEMS]:
            lines.append(problem["text"])
        omitted = len(problems) - MAX_SUMMARY_PROBLEMS
        if omitted > 0:
            lines.append(f"… ещё {omitted}")
    else:
        lines.append("✅ Проблем не обнаружено")

    payments = snapshot.get("payments", {})
    users = snapshot.get("users", {})
    keys = snapshot.get("keys", {})

    lines.extend(
        [
            "",
            "💰 <b>За 24 часа</b>",
            f"Оплат: {payments.get('paid_count', 0)}",
            f"Сумма: {_format_payments_sum(payments)}",
            "",
            "👥 <b>Клиенты</b>",
            f"Всего: {users.get('total', 0)}",
            f"Активных: {users.get('active', 0)}",
            f"Новых за 24 часа: {snapshot.get('new_users', 0)}",
            "",
            "🔑 <b>Ключи</b>",
            f"Активных: {keys.get('active', 0)}",
            f"Истекают за 24 часа: {snapshot.get('expiring_24h', 0)}",
        ]
    )

    return "\n".join(lines)


def _node_icon(node: Dict[str, Any]) -> str:
    if not node.get("enable", True):
        return "⚪"
    if node.get("status") != "online":
        return "🔴"
    if node.get("xray_problem"):
        return "🟣"
    if node.get("problem"):
        return "🟠"
    return "🟢"


def _format_node_line(node: Dict[str, Any], prefix: str) -> str:
    icon = _node_icon(node)
    name = escape_html(str(node.get("name") or "Нода"))

    if not node.get("enable", True):
        return f"   {prefix} {icon} {name} | выключена"

    if node.get("status") != "online":
        detail = escape_html(str(node.get("lastError") or "нет связи"))
        return f"   {prefix} {icon} {name} | {detail}"

    parts = [
        f"🔑 {node.get('onlineCount', 0)}",
        f"💻 {_format_percent(node.get('cpuPct'))}",
        f"🧠 {_format_percent(node.get('memPct'))}",
    ]
    rate = _format_rate_pair(node.get("netUp"), node.get("netDown"))
    if rate:
        parts.append(rate)

    if node.get("xray_problem"):
        state = escape_html(str(node.get("xrayState") or "Xray"))
        error = escape_html(str(node.get("xrayError") or "").strip())
        xray_text = f"Xray {state}"
        if error:
            xray_text += f": {error}"
        parts.append(xray_text)

    return f"   {prefix} {icon} {name} | " + " | ".join(parts)


def _select_nodes_for_display(nodes: List[Dict[str, Any]], limit: int = NODE_DISPLAY_LIMIT) -> List[Dict[str, Any]]:
    problems = [node for node in nodes if node.get("problem")]
    healthy = [node for node in nodes if not node.get("problem")]
    ordered = sorted(problems, key=lambda item: item.get("severity", 0), reverse=True) + healthy
    return ordered[:limit]


def build_servers_monitoring_text(snapshot: Dict[str, Any]) -> str:
    """Generates detailed monitoring for the “Servers” section."""
    entries = snapshot.get("servers", [])
    if not entries:
        return (
            "🖥️ <b>Сервера</b>\n\n"
            "Серверов пока нет.\n"
            "Нажмите «➕ Добавить сервер» чтобы добавить первый!"
        )

    collected_at = snapshot.get("collected_at")
    updated = collected_at.strftime("%H:%M") if isinstance(collected_at, datetime) else "—"
    lines = ["🖥️ <b>Сервера</b>", f"🕒 Обновлено: {updated}", ""]

    for entry in entries:
        server = entry["server"]
        server_name = escape_html(str(server.get("name") or "Сервер"))
        host = escape_html(f"{server.get('host')}:{server.get('port')}")

        if not entry.get("is_active"):
            lines.extend(
                [
                    f"🔴 <b>{server_name}</b> (<code>{host}</code>)",
                    "   ⏸️ Деактивирован",
                    "",
                ]
            )
            continue

        if not entry.get("panel_online"):
            error = escape_html(str(entry.get("error") or "нет подключения"))
            lines.extend(
                [
                    f"🔴 <b>{server_name}</b> (<code>{host}</code>)",
                    f"   ⚠️ Панель недоступна: {error}",
                    "",
                ]
            )
            continue

        stats = entry.get("stats") or {}
        traffic = format_traffic(_safe_int(stats.get("total_traffic_bytes")))
        cpu_text = ""
        if stats.get("cpu_percent") is not None:
            cpu_text = f" | 💻 {_format_percent(stats.get('cpu_percent'))}"

        lines.append(f"🟢 <b>{server_name}</b> (<code>{host}</code>)")
        lines.append(
            f"   Панель: онлайн | 🔑 {stats.get('online_clients', 0)} онлайн "
            f"| 📊 {traffic}{cpu_text}"
        )

        nodes = entry.get("nodes", [])
        if not nodes:
            lines.append("   🌐 Ноды: нет")
            lines.append("")
            continue

        enabled_nodes = [node for node in nodes if node.get("enable", True)]
        online_nodes = [node for node in enabled_nodes if node.get("status") == "online"]
        disabled_nodes = [node for node in nodes if not node.get("enable", True)]
        node_online_clients = sum(_safe_int(node.get("onlineCount")) for node in enabled_nodes)

        disabled_text = f" | ⏸️ {len(disabled_nodes)} выкл." if disabled_nodes else ""
        lines.append(
            f"   🌐 Ноды: {len(online_nodes)}/{len(enabled_nodes)} онлайн "
            f"| 🔑 {node_online_clients} онлайн{disabled_text}"
        )

        selected_nodes = _select_nodes_for_display(nodes)
        omitted = max(0, len(nodes) - len(selected_nodes))
        for index, node in enumerate(selected_nodes):
            is_last = index == len(selected_nodes) - 1 and omitted == 0
            prefix = "└" if is_last else "├"
            lines.append(_format_node_line(node, prefix))
        if omitted > 0:
            lines.append(f"   └ … ещё {omitted}")

        lines.append("")

    return "\n".join(lines).rstrip()
