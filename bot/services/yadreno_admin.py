"""
Yadreno Admin satellite protocol client.

The service takes care of the full request cycle:
process → poll → tool_result → final, as well as local execution of tool_call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from bot.services.yadreno_admin_core_guard import (
    finalize_core_guard,
    finalize_core_guards_for_request,
    interrupted_tool_result,
    run_with_core_guard,
)
from bot.services.yadreno_admin_page_binding import (
    YaaPageBinding,
    YaaPageBindingContextError,
    build_yaa_binding_runtime_context,
    get_yaa_page_binding,
    set_yaa_page_binding,
)
from bot.services.yadreno_admin_customization_tools import (
    CUSTOMIZATION_TOOL_NAMES,
    execute_customization_tool,
)
from bot.services.satellite_lane import (
    RequestLaneKey,
    SatelliteLaneCycle,
    satellite_lane_controller,
)
from bot.version import BOT_COMMIT, BOT_RELEASE
from config import RETRY_CONFIG
from database.requests import (
    clear_yadreno_admin_active_request_id,
    clear_yadreno_admin_last_request_id,
    clear_yadreno_admin_tool_call_started,
    clear_yadreno_admin_tool_runtime,
    get_yadreno_admin_active_request_id,
    get_yadreno_admin_api_key,
    get_yadreno_admin_last_request_id,
    get_yadreno_admin_server_ip,
    is_yadreno_admin_core_changes_enabled,
    list_yadreno_admin_active_requests,
    list_yadreno_admin_tool_runtime,
    mark_yadreno_admin_tool_call_started,
    set_yadreno_admin_active_request_id,
    set_yadreno_admin_last_request_id,
    set_yadreno_admin_server_ip,
    set_yadreno_admin_tool_runtime,
)

logger = logging.getLogger(__name__)

HUB_URLS: tuple[str, ...] = (
    "https://admin.yadreno.ru",
    "https://admin-en.yadreno.ru",
)
# Kept as the canonical public origin for compatibility with code/tests that
# import the old constant. HTTP calls use the process-local endpoint selector.
HUB_URL = HUB_URLS[0]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TMP_DIR = PROJECT_ROOT / "tmp"
UPLOAD_TMP_DIR = TMP_DIR / "yadreno_uploads"
YADRENO_ADMIN_CHAT_TOPIC_ID = 0
YADRENO_ADMIN_YAA_TOPIC_ID = 1001
YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID = 1002
YADRENO_ADMIN_BROADCAST_TOPIC_ID = 1003
YADRENO_ADMIN_DEFAULT_SKILL_ID = "yadreno_vpn"
YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID = "yadreno_vpn_customization"
YADRENO_ADMIN_BROADCAST_SKILL_ID = "yadreno_vpn_broadcast"
YADRENO_ADMIN_SATELLITE_TYPE = "yadreno_vpn"
PROGRESS_EVENTS_CAPABILITY = "progress_events"
BROADCAST_EDITOR_CAPABILITY = "broadcast_editor_v1"
RICH_MESSAGES_CAPABILITY = "rich_messages_v1"
RUNTIME_CONTEXT_CAPABILITY = "runtime_context_v1"
CUSTOMIZATION_TOOLS_CAPABILITY = "customization_tools_v2"
YADRENO_ADMIN_TELEGRAM_HTML_TASK_FORMAT = "telegram_html"
SATELLITE_PROTOCOL_VERSION = "v1"
SATELLITE_CAPABILITIES: tuple[str, ...] = (
    PROGRESS_EVENTS_CAPABILITY,
    RICH_MESSAGES_CAPABILITY,
)
HUB_MAINTENANCE_FALLBACK_MESSAGE = (
    "Сервис временно на техническом обслуживании. "
    "Попробуйте снова через несколько минут."
)
HUB_TEMPORARILY_UNAVAILABLE_MESSAGE = (
    "Хаб Yadreno Admin временно недоступен. Возможно, идёт техническое "
    "обслуживание или обновление. Попробуйте снова через несколько минут."
)
_YADRENO_ADMIN_API_KEY_RE = re.compile(
    r"[A-Za-z0-9\-._~+/]+=*",
    flags=re.ASCII,
)
PUBLIC_IP_URLS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
)

_server_ip_cache: Optional[str] = None


class _HubEndpointSelector:
    """Keep the last healthy trusted Hub endpoint in process memory only."""

    def __init__(self, endpoints: tuple[str, ...]) -> None:
        if not endpoints:
            raise ValueError("At least one Hub endpoint is required")
        self._endpoints = tuple(endpoint.rstrip("/") for endpoint in endpoints)
        self._preferred_index = 0

    @property
    def preferred(self) -> str:
        return self._endpoints[self._preferred_index]

    def candidates(self, attempts: int) -> tuple[str, ...]:
        """Return a stable round-robin order starting with the last success."""
        start = self._preferred_index
        return tuple(
            self._endpoints[(start + offset) % len(self._endpoints)]
            for offset in range(max(1, attempts))
        )

    def mark_success(self, endpoint: str) -> None:
        try:
            self._preferred_index = self._endpoints.index(endpoint.rstrip("/"))
        except ValueError:
            logger.warning("Ignoring an unknown Yadreno Admin Hub endpoint")

    def reset(self) -> None:
        """Restore the primary endpoint; used by deterministic tests."""
        self._preferred_index = 0


_hub_endpoint_selector = _HubEndpointSelector(HUB_URLS)
_dangerous_shell_patterns: tuple[tuple[str, str], ...] = (
    (
        r"(^|[;&|]\s*)(sudo\s+)?rm\s+([^\n;&|]*\s)?-(?=[^\s\n;&|]*r)(?=[^\s\n;&|]*f)[^\s\n;&|]*\s+(?:-[^\s\n;&|]+\s+)*(--\s+)?(/|\*/|/\*|~|\$HOME)(\s|$)",
        "опасное рекурсивное удаление",
    ),
    (
        r"\bmkfs(\.[a-z0-9_-]+)?\b",
        "форматирование файловой системы",
    ),
    (
        r"\bdd\b[^\n;&|]*\bof\s*=\s*/dev/",
        "прямая запись dd в /dev",
    ),
    (
        r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:",
        "fork bomb",
    ),
    (
        r"\b(chmod|chown|chgrp)\b[^\n;&|]*\s-[^\n;&|]*R[^\n;&|]*(\s/|\s/\*)",
        "рекурсивная смена прав/владельца от корня",
    ),
    (
        r"\b(curl|wget)\b[^\n]*(\|\s*(sudo\s+)?(ba)?sh\b)",
        "pipe curl/wget в shell",
    ),
)
_guard_integrity_shell_patterns: tuple[tuple[str, str], ...] = (
    (
        r"(^|[;&|]\s*)(?:sudo\s+)?git\s+"
        r"(?:add|commit|checkout|switch|restore|reset|clean|pull|fetch|merge|rebase|"
        r"cherry-pick|revert|apply|am|stash|worktree|update-index|read-tree|write-tree|"
        r"commit-tree|update-ref|symbolic-ref|gc|prune)\b",
        "mutating Git command",
    ),
    (
        r"(^|[;&|]\s*)(?:sudo\s+)?git\s+(?:branch|tag|remote|config)\b[^\n;&|]*"
        r"(?:\s-[dDmMcf]\b|\b(?:add|delete|remove|rename|set-url|unset|replace-all)\b)",
        "mutating Git metadata command",
    ),
    (r"\b(?:GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)\s*=", "Git control environment override"),
    (
        r"(?:\brm\b|\bmv\b|\bcp\b|\btouch\b|\bmkdir\b|\bchmod\b|\bchown\b|"
        r"\btee\b|>|>>)\s*[^\n;&|]*\.git(?:[/\\]|\b)",
        "direct .git mutation",
    ),
)

_SELF_RESTART_RE = re.compile(
    r"^\s*(?:sudo\s+)?(?:"
    r"systemctl\s+(?:restart|try-restart)\s+yadreno-vpn(?:\.service)?"
    r"|service\s+yadreno-vpn\s+restart"
    r")\s*$",
    flags=re.IGNORECASE,
)


def is_yadreno_admin_customization_topic(topic_id: int) -> bool:
    """Return True for Yadreno Admin lanes that use the customization skill."""
    return int(topic_id) in {
        YADRENO_ADMIN_YAA_TOPIC_ID,
        YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID,
    }


def yadreno_admin_message_storage_format(
    topic_id: int,
) -> Literal["html", "plain"]:
    """Return the Telegram message representation sent to the Hub."""
    return "html" if is_yadreno_admin_customization_topic(topic_id) else "plain"


def yadreno_admin_task_format_for_topic(topic_id: int) -> str | None:
    """Return runtime metadata for the current user message, when required."""
    if yadreno_admin_message_storage_format(topic_id) == "html":
        return YADRENO_ADMIN_TELEGRAM_HTML_TASK_FORMAT
    return None


def is_yadreno_admin_broadcast_topic(topic_id: int) -> bool:
    """Return True only for the structured broadcast editor lane."""
    return int(topic_id) == YADRENO_ADMIN_BROADCAST_TOPIC_ID


def _capabilities_for_skill(
    skill_id: str,
    *,
    runtime_context_supported: bool = False,
    customization_tools_supported: bool = False,
) -> list[str]:
    """Advertise optional capabilities only inside their isolated skills."""
    capabilities = list(SATELLITE_CAPABILITIES)
    if skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
        capabilities.append(BROADCAST_EDITOR_CAPABILITY)
    elif runtime_context_supported:
        capabilities.append(RUNTIME_CONTEXT_CAPABILITY)
        if (
            skill_id == YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID
            and customization_tools_supported
            and CUSTOMIZATION_TOOL_NAMES
            == {
                'satellite_customization_inspect',
                'satellite_customization_apply',
                'satellite_customization_replace',
            }
        ):
            capabilities.append(CUSTOMIZATION_TOOLS_CAPABILITY)
    return capabilities


def yadreno_admin_skill_id_for_topic(
    topic_id: int,
    requested_skill_id: Optional[str] = None,
) -> str:
    """Resolve the skill id sent to the hub for a local Yadreno Admin lane."""
    requested = (requested_skill_id or "").strip()
    if is_yadreno_admin_broadcast_topic(topic_id):
        return YADRENO_ADMIN_BROADCAST_SKILL_ID
    if requested in {
        YADRENO_ADMIN_DEFAULT_SKILL_ID,
        YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID,
    }:
        return requested
    if is_yadreno_admin_customization_topic(topic_id):
        return YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID
    return YADRENO_ADMIN_DEFAULT_SKILL_ID


def _core_policy_for_skill(skill_id: str) -> Optional[bool]:
    """Return the core-change policy payload for customization skill calls."""
    if skill_id != YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID:
        return None
    return is_yadreno_admin_core_changes_enabled()


def build_agent_env_context() -> dict[str, Any]:
    """Return compact environment context for the remote agent prompt."""
    try:
        from database.migrations import LATEST_VERSION, get_current_version

        db_version: int | None = get_current_version()
        latest_db_version = LATEST_VERSION
    except Exception as e:
        logger.warning("Не удалось собрать версию БД для Yadreno Admin context: %s", e)
        from database.migrations import LATEST_VERSION

        db_version = None
        latest_db_version = LATEST_VERSION

    try:
        from bot.utils.custom_extensions import is_custom_extensions_enabled

        custom_extensions_loader_enabled = is_custom_extensions_enabled()
    except Exception as e:
        logger.warning("Не удалось собрать статус custom extensions для Yadreno Admin context: %s", e)
        custom_extensions_loader_enabled = False

    return {
        "db_version": db_version,
        "latest_db_version": latest_db_version,
        "custom_extensions_loader_enabled": bool(custom_extensions_loader_enabled),
    }


def build_agent_runtime_context(
    extra_context: Optional[dict[str, Any]] = None,
    *,
    task_format: str | None = None,
) -> dict[str, Any]:
    """Build request-scoped runtime data for structured and legacy hubs."""
    context: dict[str, Any] = {
        "bot_release": BOT_RELEASE,
        "bot_commit": BOT_COMMIT,
        "environment": build_agent_env_context(),
    }
    if task_format is not None:
        context["task_format"] = task_format
    if extra_context:
        for key, value in extra_context.items():
            if key not in context and key != "task_format":
                context[key] = value
    return context




def _runtime_context_factory_for_turn(
    telegram_id: int,
    topic_id: int,
    extra_context: Optional[dict[str, Any]],
    page_binding: YaaPageBinding | None = None,
) -> Callable[[], dict[str, Any]]:
    """Return a builder that prefers the active pinned /yaa page snapshot."""
    task_format = yadreno_admin_task_format_for_topic(topic_id)
    binding = page_binding or get_yaa_page_binding(telegram_id, topic_id)
    if topic_id == YADRENO_ADMIN_YAA_TOPIC_ID and binding is not None:
        def build_bound() -> dict[str, Any]:
            context = build_yaa_binding_runtime_context(binding)
            return build_agent_runtime_context(
                context,
                task_format=task_format,
            )

        return build_bound

    def build_unbound() -> dict[str, Any]:
        if is_yadreno_admin_customization_topic(topic_id):
            return build_agent_runtime_context(task_format=task_format)
        return build_agent_runtime_context(
            extra_context,
            task_format=task_format,
        )

    return build_unbound


def _build_runtime_context_or_error(
    factory: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Build a complete new-turn snapshot or expose a local retryable error."""
    try:
        return factory()
    except YaaPageBindingContextError as exc:
        raise YadrenoAdminError(
            f"Failed to build active /yaa page context: {exc}",
            user_message=(
                "Не удалось заново собрать контекст закреплённой страницы. "
                "Запрос агенту не отправлен; попробуйте ещё раз."
            ),
        ) from exc


def _with_agent_runtime_context(
    message: str,
    runtime_context: dict[str, Any],
) -> str:
    """Prefix a request for a legacy hub without structured context support."""
    return (
        "Служебный контекст:\n"
        f"{json.dumps(runtime_context, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"{message}"
    )


def normalize_yadreno_admin_api_key(api_key: str) -> str:
    """Return a normalized HTTP-safe Bearer token without exposing its value."""
    if not isinstance(api_key, str):
        raise ValueError("invalid api_key format")

    normalized = api_key.strip()
    if not normalized:
        raise ValueError("invalid api_key format")
    if any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F
        for char in normalized
    ):
        raise ValueError("invalid api_key format")
    if _YADRENO_ADMIN_API_KEY_RE.fullmatch(normalized) is None:
        raise ValueError("invalid api_key format")
    return normalized


def _hub_headers(api_key: str) -> dict[str, str]:
    """Build authentication and version headers for every hub request."""
    try:
        normalized_api_key = normalize_yadreno_admin_api_key(api_key)
    except ValueError:
        logger.warning(
            "Yadreno Admin request rejected locally: invalid api_key format"
        )
        raise YadrenoAdminError(
            "Yadreno Admin request rejected locally: invalid api_key format",
            kind="configuration",
        ) from None
    return {
        "Authorization": f"Bearer {normalized_api_key}",
        "X-Yadreno-Satellite-Protocol-Version": SATELLITE_PROTOCOL_VERSION,
    }


class YadrenoAdminError(RuntimeError):
    """Classified failure of a Yadreno Admin request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        user_message: str | None = None,
        cancel_button_text: str | None = None,
        kind: Literal[
            "transport",
            "authentication",
            "configuration",
            "hub_rejection",
            "maintenance",
            "service_unavailable",
            "protocol",
            "local",
        ] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.user_message = user_message
        self.cancel_button_text = cancel_button_text
        if kind is None:
            if status_code in {401, 403}:
                kind = "authentication"
            elif user_message is not None:
                kind = "local"
            else:
                kind = "transport"
        self.kind = kind


class YadrenoAdminRequestStopped(YadrenoAdminError):
    """Internal signal that this process no longer owns the lane cycle."""

    def __init__(self, request_id: int | None = None) -> None:
        request_label = str(int(request_id)) if request_id is not None else "pending"
        super().__init__(
            f"Local Satellite cycle stopped for request {request_label}",
            kind="local",
        )
        self.request_id = int(request_id) if request_id is not None else None


class DangerousShellCommandError(ValueError):
    """The command was rejected by the local deny-list."""


@dataclass
class YadrenoAdminFinal:
    """Agent's final response."""

    content: str
    viewer_url: Optional[str] = None
    request_id: Optional[int] = None
    rich_markdown: Optional[str] = None


@dataclass
class YadrenoAdminProgressEvent:
    """Intermediate user event from the hub."""

    event: str
    content: str
    slot: str = ""
    cancel_button_text: str | None = None


@dataclass
class YadrenoAdminUpload:
    """File to be sent to Yadreno Admin upload API."""

    path: Path
    filename: str
    content_type: str = "application/octet-stream"


@dataclass
class YadrenoAdminLatest:
    """Latest non-destructive snapshot of the request."""

    request_id: int
    event: str = ""
    final: Optional[YadrenoAdminFinal] = None
    progress: Optional[YadrenoAdminProgressEvent] = None
    resume_allowed: bool = True
    cancel_button_text: str | None = None


@dataclass
class YadrenoAdminNewChatResult:
    """Result of a new chat request on the hub."""

    status: str
    response_text: str = ""
    cancel_button_text: str | None = None
    closed_session_id: Optional[int] = None


@dataclass
class YadrenoAdminHubStatus:
    """Request/lane status according to hub /status."""

    status: str
    response_text: str = ""
    cancel_button_text: str | None = None
    request_id: Optional[int] = None
    retry_after_sec: Optional[float] = None
    local_tool_running: bool = False
    local_tool_call_id: Optional[str] = None
    resume_allowed: bool = False


@dataclass
class YadrenoAdminCancelResult:
    """Result of smart cancellation request/lane."""

    status: str
    response_text: str = ""
    cancel_button_text: str | None = None
    request_id: Optional[int] = None
    retry_after_sec: Optional[float] = None


ProgressCallback = Callable[[YadrenoAdminProgressEvent], Awaitable[None]]

_active_requests: dict[RequestLaneKey, int] = {}
_last_requests: dict[RequestLaneKey, int] = {}
_running_tool_calls: dict[tuple[int, str], dict[str, Any]] = {}


def _lane_key(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> RequestLaneKey:
    return int(telegram_id), int(topic_id)


def get_active_request_id(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> Optional[int]:
    """Returns the active request_id of the administrator, if any."""
    key = _lane_key(telegram_id, topic_id)
    return _active_requests.get(key) or get_yadreno_admin_active_request_id(
        telegram_id,
        topic_id,
    )


def get_last_request_id(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> Optional[int]:
    """Returns the last request_id for manual recovery."""
    key = _lane_key(telegram_id, topic_id)
    return _last_requests.get(key) or get_yadreno_admin_last_request_id(
        telegram_id,
        topic_id,
    )


def is_local_request_active(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> bool:
    """Return whether this process owns or queues work for the lane."""
    return satellite_lane_controller.is_active(_lane_key(telegram_id, topic_id))


def _remember_request(
    telegram_id: int,
    topic_id: int,
    request_id: int,
    *,
    active: bool,
) -> None:
    """Saves request_id in memory and settings for restoration after restart."""
    key = _lane_key(telegram_id, topic_id)
    _last_requests[key] = request_id
    set_yadreno_admin_last_request_id(telegram_id, topic_id, request_id)
    if active:
        _active_requests[key] = request_id
        set_yadreno_admin_active_request_id(telegram_id, topic_id, request_id)


def _clear_active_request(
    telegram_id: int,
    topic_id: int,
    *,
    expected_request_id: int | None = None,
) -> None:
    """Clear an active id without deleting a newer cycle's id."""
    key = _lane_key(telegram_id, topic_id)
    current = _active_requests.get(key)
    if expected_request_id is None or current == int(expected_request_id):
        _active_requests.pop(key, None)
    clear_yadreno_admin_active_request_id(
        telegram_id,
        topic_id,
        expected_request_id=expected_request_id,
    )


def _clear_last_request(
    telegram_id: int,
    topic_id: int,
    *,
    expected_request_id: int | None = None,
) -> None:
    """Clear a last id without deleting a newer cycle's id."""
    key = _lane_key(telegram_id, topic_id)
    current = _last_requests.get(key)
    if expected_request_id is None or current == int(expected_request_id):
        _last_requests.pop(key, None)
    clear_yadreno_admin_last_request_id(
        telegram_id,
        topic_id,
        expected_request_id=expected_request_id,
    )


def _signal_poll_stop(
    telegram_id: int,
    topic_id: int,
    request_id: int,
) -> bool:
    """Wake the local poll only if it owns the expected Hub request."""
    return satellite_lane_controller.signal_poll_stop(
        _lane_key(telegram_id, topic_id),
        request_id,
    )


@dataclass(frozen=True)
class _ToolRuntimeContext:
    request_id: int
    topic_id: int
    tool_call_id: str
    tool: str


def _runtime_context_from_event(
    event: dict[str, Any],
    *,
    topic_id: int,
) -> Optional[_ToolRuntimeContext]:
    tool_call_id = str(event.get("tool_call_id") or "")
    if not tool_call_id:
        return None
    try:
        request_id = int(event.get("request_id") or 0)
    except (TypeError, ValueError):
        request_id = 0
    if request_id <= 0:
        return None
    return _ToolRuntimeContext(
        request_id=request_id,
        topic_id=int(topic_id),
        tool_call_id=tool_call_id,
        tool=str(event.get("tool") or ""),
    )


def _remember_tool_runtime(
    runtime: Optional[_ToolRuntimeContext],
    *,
    pid: Optional[int] = None,
) -> None:
    """Saves the runtime state of a locally running tool_call."""
    if runtime is None:
        return
    payload = {
        "request_id": runtime.request_id,
        "topic_id": runtime.topic_id,
        "tool_call_id": runtime.tool_call_id,
        "tool": runtime.tool,
        "pid": int(pid) if pid is not None else None,
    }
    _running_tool_calls[(runtime.request_id, runtime.tool_call_id)] = payload
    set_yadreno_admin_tool_runtime(
        runtime.request_id,
        runtime.tool_call_id,
        runtime.tool,
        topic_id=runtime.topic_id,
        pid=pid,
    )


def _clear_tool_runtime(request_id: int, tool_call_id: str) -> None:
    """Removes the runtime state of a completed local tool_call."""
    _running_tool_calls.pop((int(request_id), str(tool_call_id)), None)
    clear_yadreno_admin_tool_runtime(request_id, tool_call_id)


def _pid_is_running(pid: Any) -> bool:
    """Best-effort check for liveness of local subprocess by pid."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(value)
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _windows_pid_is_running(pid: int) -> bool:
    """Dev-only fuse: on Windows os.kill(pid, 0) sends CTRL+C."""
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(pid),
        )
        if not handle:
            return False

        exit_code = wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def get_local_tool_diagnostics(
    request_id: Optional[int],
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> dict[str, Any]:
    """Returns local diagnostics running tool_call for hub /cancel."""
    if request_id is None:
        return {
            "local_checked": True,
            "local_tool_running": False,
            "local_tool_call_id": None,
        }

    for payload in list(_running_tool_calls.values()):
        if (
            int(payload.get("request_id") or 0) == int(request_id)
            and int(payload.get("topic_id") or 0) == int(topic_id)
        ):
            return {
                "local_checked": True,
                "local_tool_running": True,
                "local_tool_call_id": str(payload.get("tool_call_id") or ""),
            }

    for payload in list_yadreno_admin_tool_runtime(request_id, topic_id):
        tool_call_id = str(payload.get("tool_call_id") or "")
        if _pid_is_running(payload.get("pid")):
            return {
                "local_checked": True,
                "local_tool_running": True,
                "local_tool_call_id": tool_call_id,
            }
        if tool_call_id:
            clear_yadreno_admin_tool_runtime(request_id, tool_call_id)

    return {
        "local_checked": True,
        "local_tool_running": False,
        "local_tool_call_id": None,
    }


def _reject_dangerous_shell(command: str) -> None:
    """Rejects catastrophically dangerous shell commands before subprocess."""
    for pattern, reason in _dangerous_shell_patterns:
        if re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE):
            raise DangerousShellCommandError(
                f"dangerous shell command rejected: {reason}"
            )


def _resolve_tool_path(raw_path: str) -> Path:
    """Converts the tool_call path to an absolute path on the local server."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _core_guard_enabled(topic_id: int) -> bool:
    return (
        is_yadreno_admin_customization_topic(topic_id)
        and not is_yadreno_admin_core_changes_enabled()
    )


def _core_guard_integrity_error(command: str) -> Optional[str]:
    """Protect only the Git checkpoint machinery, not customization capabilities."""
    for pattern, reason in _guard_integrity_shell_patterns:
        if re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE):
            return f"core protection integrity check rejected: {reason}"
    return None


def _is_deferred_self_restart(tool: str, args: dict[str, Any]) -> bool:
    """Recognize only a standalone restart of the current satellite service."""
    if tool == "satellite_execute":
        command = str(args.get("command", ""))
    elif tool == "satellite_run_script":
        command = str(args.get("script_body", ""))
    else:
        return False
    return bool(_SELF_RESTART_RE.fullmatch(command))


def _get_timeout(args: dict[str, Any], default: int = 60) -> int:
    """Reads timeout from tool_call arguments with safe default."""
    try:
        timeout = int(args.get("timeout", default) or default)
    except (TypeError, ValueError):
        timeout = default
    return max(1, timeout)


async def _detect_public_server_ip_with_session(
    session: aiohttp.ClientSession,
    *,
    use_cache: bool = True,
) -> str:
    """Best-effort determines the public IP of the server through external services."""
    global _server_ip_cache
    if use_cache and _server_ip_cache is not None:
        return _server_ip_cache

    for url in PUBLIC_IP_URLS:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status >= 400:
                    continue
                ip = (await response.text()).strip()
                if ip and len(ip) <= 64:
                    _server_ip_cache = ip
                    return ip
        except Exception as e:
            logger.debug("Не удалось определить публичный IP через %s: %s", url, e)

    return ""


async def detect_public_server_ip(*, use_cache: bool = True) -> str:
    """Determines the public IP of the server without accessing config.py."""
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await _detect_public_server_ip_with_session(
            session,
            use_cache=use_cache,
        )


async def _get_server_ip(session: aiohttp.ClientSession) -> str:
    """Returns the public IP of the satellite: settings → autodetect → ''."""
    saved_ip = get_yadreno_admin_server_ip().strip()
    if saved_ip:
        return saved_ip

    detected_ip = await _detect_public_server_ip_with_session(session)
    if detected_ip:
        try:
            set_yadreno_admin_server_ip(detected_ip)
        except Exception as e:
            logger.warning("Не удалось сохранить публичный IP Yadreno Admin: %s", e)
    return detected_ip


def _is_retryable_hub_error(error: Exception) -> bool:
    """Return whether repeating the same HTTP operation may recover."""
    if not isinstance(error, YadrenoAdminError):
        return True
    if error.kind in {
        "authentication",
        "configuration",
        "hub_rejection",
        "maintenance",
        "service_unavailable",
        "protocol",
        "local",
    }:
        return False
    status_code = error.status_code
    return (
        status_code is None
        or status_code in {408, 429}
        or status_code >= 500
    )


def _is_definitely_pre_request_failure(error: Exception) -> bool:
    """Return whether HTTP request bytes could not have reached the Hub."""
    return isinstance(error, aiohttp.ClientConnectorError)


def _can_repeat_hub_request(error: Exception, *, read_only: bool) -> bool:
    """Protect mutating protocol calls from ambiguous duplicate side effects."""
    if read_only:
        if isinstance(error, YadrenoAdminError) and error.kind == "protocol":
            return True
        return _is_retryable_hub_error(error)
    return _is_definitely_pre_request_failure(error)


def _hub_attempt_count() -> int:
    """Try every trusted endpoint at least once despite a lower retry setting."""
    try:
        configured = int(RETRY_CONFIG.get("max_attempts", 3))
    except (TypeError, ValueError):
        configured = 3
    return max(1, configured, len(HUB_URLS))


def _rewrite_hub_viewer_url(
    data: dict[str, Any],
    *,
    endpoint: str,
) -> dict[str, Any]:
    """Use the endpoint that delivered a final for its Hub-owned viewer URL."""
    viewer_url = data.get("viewer_url")
    if not isinstance(viewer_url, str) or not viewer_url:
        return data
    try:
        viewer_parts = urlsplit(viewer_url)
        primary_parts = urlsplit(HUB_URLS[0])
        endpoint_parts = urlsplit(endpoint)
    except ValueError:
        return data
    if (
        viewer_parts.scheme.lower() != primary_parts.scheme.lower()
        or viewer_parts.netloc.lower() != primary_parts.netloc.lower()
    ):
        return data
    rewritten = urlunsplit(
        (
            endpoint_parts.scheme,
            endpoint_parts.netloc,
            viewer_parts.path,
            viewer_parts.query,
            viewer_parts.fragment,
        )
    )
    if rewritten == viewer_url:
        return data
    return {**data, "viewer_url": rewritten}


async def _request_json(
    session: aiohttp.ClientSession,
    api_key: str,
    method: str,
    path: str,
    *,
    json_payload: Optional[dict] = None,
    allow_no_content: bool = False,
) -> tuple[int, Optional[dict]]:
    """
    Makes an HTTP request to the hub with retry and returns status + JSON.

    204 is processed separately, because long-polling is a standard response.
    """
    headers = _hub_headers(api_key)
    delays = RETRY_CONFIG.get("delays", [1, 3, 9])
    max_attempts = _hub_attempt_count()
    last_error: Optional[Exception] = None
    read_only = method.upper() in {"GET", "HEAD", "OPTIONS"}
    endpoints = _hub_endpoint_selector.candidates(max_attempts)

    for attempt, endpoint in enumerate(endpoints, start=1):
        try:
            async with session.request(
                method,
                f"{endpoint}{path}",
                headers=headers,
                json=json_payload,
            ) as response:
                if allow_no_content and response.status == 204:
                    _hub_endpoint_selector.mark_success(endpoint)
                    return response.status, None
                if response.status >= 400:
                    await response.text()
                    raise YadrenoAdminError(
                        f"Hub returned HTTP {response.status}",
                        status_code=response.status,
                    )
                try:
                    data = await response.json()
                except json.JSONDecodeError as exc:
                    raise YadrenoAdminError(
                        "Hub returned invalid JSON",
                        kind="protocol",
                    ) from exc
                if not isinstance(data, dict):
                    raise YadrenoAdminError(
                        "Hub returned a non-object JSON response",
                        kind="protocol",
                    )
                _hub_endpoint_selector.mark_success(endpoint)
                return response.status, _rewrite_hub_viewer_url(
                    data,
                    endpoint=endpoint,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, YadrenoAdminError) as e:
            last_error = e
            if not _can_repeat_hub_request(e, read_only=read_only):
                if isinstance(e, YadrenoAdminError):
                    raise
                raise YadrenoAdminError(
                    f"Could not reach the Yadreno Admin hub: {e}",
                ) from e
            if attempt >= max_attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)]
            logger.warning(
                "Ошибка запроса к Yadreno Admin (%s %s via %s), попытка %s/%s: %s",
                method,
                path,
                urlsplit(endpoint).netloc,
                attempt,
                max_attempts,
                e,
            )
            await asyncio.sleep(delay)

    if isinstance(last_error, YadrenoAdminError):
        raise last_error
    raise YadrenoAdminError(
        f"Could not reach the Yadreno Admin hub: {last_error}",
    ) from last_error


def _incompatible_broadcast_hub(detail: str) -> YadrenoAdminError:
    """Build a stable user-facing error for a missing safe editor contract."""
    return YadrenoAdminError(
        f"incompatible broadcast editor hub: {detail}",
        user_message=(
            "Редактор рассылок пока несовместим с хабом. "
            "Сначала обновите Yadreno Admin на хабе, затем повторите /yaa. "
            "Обычная рассылка при этом продолжает работать."
        ),
    )


def _incompatible_customization_hub(detail: str) -> YadrenoAdminError:
    """Build a stable error when /yaa cannot prove its specialized contract."""
    return YadrenoAdminError(
        f"incompatible customization hub: {detail}",
        user_message=(
            "Не удалось безопасно согласовать режим /yaa с хабом. "
            "Проверьте подключение и API-ключ типа YadrenoVPN, затем повторите запрос."
        ),
    )


def _incompatible_specialized_hub(
    skill_id: str,
    detail: str,
) -> YadrenoAdminError:
    """Build the lane-specific fail-closed negotiation error."""
    if skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
        return _incompatible_broadcast_hub(detail)
    return _incompatible_customization_hub(detail)


def _is_temporary_hub_unavailability(error: YadrenoAdminError) -> bool:
    """Return True for discovery failures consistent with a hub restart."""
    if error.kind == "protocol":
        return True
    if error.kind != "transport":
        return False
    return (
        error.status_code is None
        or error.status_code in {408, 429}
        or error.status_code >= 500
    )


def _temporary_hub_unavailable(
    error: YadrenoAdminError,
    operation: str,
) -> YadrenoAdminError:
    """Build a calm user-facing error after retryable discovery has failed."""
    detail = error.status_code or "network_or_protocol"
    return YadrenoAdminError(
        f"Hub temporarily unavailable during {operation} ({detail})",
        status_code=error.status_code,
        user_message=HUB_TEMPORARILY_UNAVAILABLE_MESSAGE,
        kind="service_unavailable",
    )


def _raise_for_capability_status(data: dict[str, Any]) -> None:
    """Stop a new task when the authenticated hub reports maintenance."""
    if data.get("status") != "maintenance":
        return
    response_text = data.get("response_text")
    if not isinstance(response_text, str) or not response_text.strip():
        response_text = HUB_MAINTENANCE_FALLBACK_MESSAGE
    raise YadrenoAdminError(
        "Hub reported maintenance during capability discovery",
        user_message=response_text.strip(),
        kind="maintenance",
    )


async def _ensure_broadcast_hub_support(
    session: aiohttp.ClientSession,
    api_key: str,
    skill_id: str,
) -> None:
    """Negotiate the broadcast capability before any agent request starts."""
    if skill_id != YADRENO_ADMIN_BROADCAST_SKILL_ID:
        return
    try:
        _, data = await _request_json(
            session,
            api_key,
            "GET",
            "/api/v1/satellite/capabilities",
        )
    except YadrenoAdminError as error:
        if error.kind in {"authentication", "configuration"}:
            raise
        if _is_temporary_hub_unavailability(error):
            raise _temporary_hub_unavailable(
                error,
                "broadcast capability discovery",
            ) from error
        raise _incompatible_broadcast_hub(
            f"capability discovery failed ({error.status_code or 'network'})"
        ) from error
    if not isinstance(data, dict):
        raise _incompatible_broadcast_hub("empty capability response")
    _raise_for_capability_status(data)
    capabilities = data.get("capabilities")
    allowed_skills = data.get("allowed_skill_ids")
    if data.get("satellite_type") != YADRENO_ADMIN_SATELLITE_TYPE:
        raise _incompatible_broadcast_hub("wrong satellite_type")
    if not isinstance(capabilities, list) or BROADCAST_EDITOR_CAPABILITY not in capabilities:
        raise _incompatible_broadcast_hub("broadcast_editor_v1 is absent")
    if not isinstance(allowed_skills, list) or skill_id not in allowed_skills:
        raise _incompatible_broadcast_hub("broadcast skill is not allowed")


async def _negotiate_runtime_context_support(
    session: aiohttp.ClientSession,
    api_key: str,
    skill_id: str,
    negotiated_capabilities: set[str] | None = None,
) -> bool:
    """Discover structured context support for one new task.

    Broadcast and customization keep fail-closed specialized contracts.
    Only the ordinary lane may degrade to the legacy text prefix.
    """
    if skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
        await _ensure_broadcast_hub_support(session, api_key, skill_id)
        return False
    try:
        _, data = await _request_json(
            session,
            api_key,
            "GET",
            "/api/v1/satellite/capabilities",
        )
    except YadrenoAdminError as error:
        if error.kind in {"authentication", "configuration"}:
            raise
        if _is_temporary_hub_unavailability(error):
            raise _temporary_hub_unavailable(
                error,
                "capability discovery",
            ) from error
        if skill_id == YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID:
            raise _incompatible_customization_hub(
                f"capability discovery failed ({error.status_code or 'network'})"
            ) from error
        logger.warning(
            "runtime_context capability discovery unavailable; using legacy "
            "prefix (status=%s)",
            error.status_code or "network",
        )
        return False
    if not isinstance(data, dict):
        if skill_id == YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID:
            raise _incompatible_customization_hub("empty capability response")
        return False
    _raise_for_capability_status(data)
    capabilities = data.get("capabilities")
    allowed_skills = data.get("allowed_skill_ids")
    supported = bool(
        data.get("protocol_version") == SATELLITE_PROTOCOL_VERSION
        and data.get("satellite_type") == YADRENO_ADMIN_SATELLITE_TYPE
        and isinstance(capabilities, list)
        and RUNTIME_CONTEXT_CAPABILITY in capabilities
        and isinstance(allowed_skills, list)
        and skill_id in allowed_skills
    )
    if supported and negotiated_capabilities is not None:
        negotiated_capabilities.update(
            item for item in capabilities if isinstance(item, str)
        )
    if skill_id == YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID and not supported:
        raise _incompatible_customization_hub("capability response mismatch")
    return supported


def _validate_specialized_hub_response(data: dict[str, Any], skill_id: str) -> None:
    """Reject fallback after customization or broadcast negotiation."""
    if skill_id not in {
        YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID,
        YADRENO_ADMIN_BROADCAST_SKILL_ID,
    }:
        return
    if data.get("satellite_type") != YADRENO_ADMIN_SATELLITE_TYPE:
        raise _incompatible_specialized_hub(
            skill_id,
            "response satellite_type mismatch",
        )
    if data.get("skill_id") != skill_id:
        raise _incompatible_specialized_hub(skill_id, "response skill_id mismatch")


def _raise_for_hub_rejection(data: dict[str, Any], operation: str) -> None:
    """Expose a well-formed business rejection using the hub-owned text."""
    status = data.get("status")
    if status == "accepted":
        return
    if not isinstance(status, str) or not status:
        raise YadrenoAdminError(
            f"Hub returned an invalid {operation} status",
            kind="protocol",
        )
    response_text = data.get("response_text")
    if not isinstance(response_text, str) or not response_text.strip():
        raise YadrenoAdminError(
            f"Hub rejected {operation} without response_text (status={status})",
            kind="protocol",
        )
    raise YadrenoAdminError(
        f"Hub rejected {operation} (status={status})",
        user_message=response_text.strip(),
        cancel_button_text=data.get("cancel_button_text"),
        kind="maintenance" if status == "maintenance" else "hub_rejection",
    )


def _accepted_request_id(data: dict[str, Any], operation: str) -> int:
    """Validate the polling identifier of an accepted protocol response."""
    request_id = data.get("request_id")
    if (
        isinstance(request_id, bool)
        or not isinstance(request_id, int)
        or request_id <= 0
    ):
        raise YadrenoAdminError(
            f"Hub accepted {operation} without a valid request_id",
            kind="protocol",
        )
    return request_id


async def _request_multipart(
    session: aiohttp.ClientSession,
    api_key: str,
    path: str,
    *,
    fields: dict[str, Any],
    uploads: list[YadrenoAdminUpload],
    file_field: str,
) -> tuple[int, Optional[dict]]:
    """Makes a multipart request to the upload API of the hub with retry."""
    headers = _hub_headers(api_key)
    delays = RETRY_CONFIG.get("delays", [1, 3, 9])
    max_attempts = _hub_attempt_count()
    last_error: Optional[Exception] = None
    endpoints = _hub_endpoint_selector.candidates(max_attempts)

    for attempt, endpoint in enumerate(endpoints, start=1):
        handles = []
        try:
            form = aiohttp.FormData()
            for key, value in fields.items():
                if value is not None:
                    form.add_field(key, str(value))
            for upload in uploads:
                handle = upload.path.open("rb")
                handles.append(handle)
                form.add_field(
                    file_field,
                    handle,
                    filename=upload.filename,
                    content_type=upload.content_type or "application/octet-stream",
                )
            async with session.post(
                f"{endpoint}{path}",
                headers=headers,
                data=form,
            ) as response:
                if response.status >= 400:
                    await response.text()
                    raise YadrenoAdminError(
                        f"Hub returned HTTP {response.status}",
                        status_code=response.status,
                    )
                try:
                    data = await response.json()
                except json.JSONDecodeError as exc:
                    raise YadrenoAdminError(
                        "Hub returned invalid JSON",
                        kind="protocol",
                    ) from exc
                if not isinstance(data, dict):
                    raise YadrenoAdminError(
                        "Hub returned a non-object JSON response",
                        kind="protocol",
                    )
                _hub_endpoint_selector.mark_success(endpoint)
                return response.status, _rewrite_hub_viewer_url(
                    data,
                    endpoint=endpoint,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, YadrenoAdminError) as e:
            last_error = e
            if not _can_repeat_hub_request(e, read_only=False):
                if isinstance(e, YadrenoAdminError):
                    raise
                raise YadrenoAdminError(
                    f"Could not upload a file to the Yadreno Admin hub: {e}",
                ) from e
            if attempt >= max_attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)]
            logger.warning(
                "Ошибка upload-запроса к Yadreno Admin (%s via %s), попытка %s/%s: %s",
                path,
                urlsplit(endpoint).netloc,
                attempt,
                max_attempts,
                e,
            )
            await asyncio.sleep(delay)
        finally:
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass

    if isinstance(last_error, YadrenoAdminError):
        raise last_error
    raise YadrenoAdminError(
        f"Could not upload a file to the Yadreno Admin hub: {last_error}",
    ) from last_error


async def _execute_shell(
    args: dict[str, Any],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Optional[str]]:
    """Executes satellite_execute on the server where the bot is running."""
    command = str(args.get("command", "")).strip()
    if not command:
        return {"result": "", "error": "empty command"}

    timeout = _get_timeout(args)
    try:
        _reject_dangerous_shell(command)
    except DangerousShellCommandError as e:
        return {"result": "", "error": str(e)}

    try:
        if os.name == "nt":
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                command,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        _remember_tool_runtime(runtime, pid=process.pid)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        output = (stdout or b"").decode("utf-8", errors="replace")
        output += (stderr or b"").decode("utf-8", errors="replace")
        output += f"\n[exit_code={process.returncode}]"
        return {"result": output, "error": None}
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return {"result": "", "error": f"command timed out after {timeout}s"}
    except Exception as e:
        return {"result": "", "error": str(e)}


async def _write_file(args: dict[str, Any]) -> dict[str, Optional[str]]:
    """Executes satellite_write_file: writes content to the explicitly passed path."""
    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return {"result": "", "error": "empty path"}

    content = args.get("content", "")
    if content is None:
        content = ""

    try:
        path = _resolve_tool_path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, str(content), encoding="utf-8")
        return {"result": f"File {path} written successfully.", "error": None}
    except Exception as e:
        return {"result": "", "error": str(e)}


async def _run_script(
    args: dict[str, Any],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Optional[str]]:
    """Executes satellite_run_script via a temporary .sh in tmp/."""
    script_body = str(args.get("script_body", "")).strip()
    if not script_body:
        return {"result": "", "error": "empty script_body"}

    timeout = _get_timeout(args)
    try:
        _reject_dangerous_shell(script_body)
    except DangerousShellCommandError as e:
        return {"result": "", "error": str(e)}

    script_path: Optional[Path] = None
    process = None
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        script_path = TMP_DIR / f"agent_job_{uuid.uuid4().hex}.sh"
        safe_script = f"#!/bin/bash\nset -euo pipefail\n\n{script_body}\n"
        await asyncio.to_thread(script_path.write_text, safe_script, encoding="utf-8")
        script_path.chmod(0o700)

        runner = [str(script_path)]
        if os.name == "nt":
            bash = shutil.which("bash")
            if not bash:
                return {"result": "", "error": "bash is not installed or not in PATH"}
            runner = [bash, str(script_path)]

        process = await asyncio.create_subprocess_exec(
            *runner,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _remember_tool_runtime(runtime, pid=process.pid)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        output = (stdout or b"").decode("utf-8", errors="replace")
        output += (stderr or b"").decode("utf-8", errors="replace")
        output += f"\n[exit_code={process.returncode}]"
        return {"result": output, "error": None}
    except asyncio.TimeoutError:
        if process:
            try:
                process.kill()
            except Exception:
                pass
        return {"result": "", "error": f"script timed out after {timeout}s"}
    except Exception as e:
        return {"result": "", "error": str(e)}
    finally:
        if script_path:
            try:
                script_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Не удалось удалить временный скрипт %s: %s", script_path, e)


def _format_sql_rows(rows: list[sqlite3.Row] | list[tuple[Any, ...]], columns: list[str]) -> str:
    """Formats a tabular SQL response into compact text."""
    if not rows:
        return "(0 rows)"
    lines = [" | ".join(columns)]
    for row in rows:
        values = list(row)
        lines.append(" | ".join(str(value) for value in values))
    return "\n".join(lines)


async def _execute_sqlite(
    args: dict[str, Any],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Any]:
    """Executes an sqlite query using db_path/db_name."""
    db_path = str(args.get("db_path") or args.get("db_name") or "").strip()
    if not db_path:
        return {"result": "", "error": "sqlite requires db_path or db_name"}

    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

    query = str(args.get("query", "")).strip()
    if not query:
        return {"result": "", "error": "empty query"}

    def _run() -> dict[str, Any]:
        statement_match = re.match(r"^\s*([A-Za-z]+)", query)
        statement_type = (
            statement_match.group(1).upper() if statement_match else "UNKNOWN"
        )
        touched_tables: set[str] = set()
        authorizer_actions: set[str] = set()
        action_names = {
            sqlite3.SQLITE_READ: "read",
            sqlite3.SQLITE_INSERT: "insert",
            sqlite3.SQLITE_UPDATE: "update",
            sqlite3.SQLITE_DELETE: "delete",
        }

        def _authorizer(
            action_code: int,
            first_argument: Optional[str],
            _second_argument: Optional[str],
            _database_name: Optional[str],
            _trigger_name: Optional[str],
        ) -> int:
            action_name = action_names.get(action_code)
            if action_name:
                authorizer_actions.add(action_name)
                if first_argument and first_argument != "sqlite_master":
                    touched_tables.add(str(first_argument))
            return sqlite3.SQLITE_OK

        audit = {
            "db_path": str(path),
            "statement_type": statement_type,
            "actions": [],
            "tables": [],
        }
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                foreign_keys_row = conn.execute("PRAGMA foreign_keys").fetchone()
                if not foreign_keys_row or int(foreign_keys_row[0]) != 1:
                    raise RuntimeError("sqlite foreign_keys could not be enabled")
                conn.set_authorizer(_authorizer)
                cursor = conn.execute(query)
                if cursor.description:
                    columns = [item[0] for item in cursor.description]
                    rows = cursor.fetchall()
                    audit["actions"] = sorted(authorizer_actions)
                    audit["tables"] = sorted(touched_tables)[:20]
                    return {
                        "result": _format_sql_rows(rows, columns),
                        "error": None,
                        "_audit": audit,
                    }
                conn.commit()
                audit["actions"] = sorted(authorizer_actions)
                audit["tables"] = sorted(touched_tables)[:20]
                audit["rows_affected"] = cursor.rowcount
                return {
                    "result": f"OK, rows_affected={cursor.rowcount}",
                    "error": None,
                    "_audit": audit,
                }
        except Exception as e:
            audit["actions"] = sorted(authorizer_actions)
            audit["tables"] = sorted(touched_tables)[:20]
            return {"result": "", "error": str(e), "_audit": audit}

    return await asyncio.to_thread(_run)


async def _execute_sql_cli(
    args: dict[str, Any],
    binary_name: str,
    command_args: list[str],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Optional[str]]:
    """
    Executes SQL via local CLI.

    Credentials are deliberately not stored in the code: mysql/psql use it themselves
    environment and local process user configs.
    """
    binary = shutil.which(binary_name)
    if not binary:
        return {"result": "", "error": f"{binary_name} is not installed or not in PATH"}

    query = str(args.get("query", "")).strip()
    if not query:
        return {"result": "", "error": "empty query"}

    timeout = int(args.get("timeout", 60) or 60)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _remember_tool_runtime(runtime, pid=process.pid)
        stdout, stderr = await asyncio.wait_for(
            process.communicate(query.encode("utf-8")),
            timeout=timeout,
        )
        output = (stdout or b"").decode("utf-8", errors="replace")
        error = (stderr or b"").decode("utf-8", errors="replace")
        if process.returncode:
            return {"result": output, "error": error or f"exit_code={process.returncode}"}
        return {"result": output or "OK", "error": None}
    except asyncio.TimeoutError:
        if process:
            try:
                process.kill()
            except Exception:
                pass
        return {"result": "", "error": f"sql command timed out after {timeout}s"}
    except Exception as e:
        return {"result": "", "error": str(e)}


async def _execute_sql(
    args: dict[str, Any],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Any]:
    """Executes satellite_sql for sqlite/mysql/postgres."""
    db_type = str(args.get("db_type", "")).strip().lower()
    db_name = str(args.get("db_name", "")).strip()

    if db_type == "sqlite":
        return await _execute_sqlite(args, runtime=runtime)
    if db_type == "mysql":
        command_args = ["--batch", "--raw"]
        if db_name:
            command_args.append(db_name)
        return await _execute_sql_cli(args, "mysql", command_args, runtime=runtime)
    if db_type in {"postgres", "postgresql"}:
        command_args = ["--tuples-only", "--no-align"]
        if db_name:
            command_args.extend(["--dbname", db_name])
        return await _execute_sql_cli(args, "psql", command_args, runtime=runtime)
    return {"result": "", "error": f"unsupported db_type {db_type}"}


def _log_tool_audit(event: dict[str, Any], tool_result: dict[str, Any]) -> None:
    """Writes an audit log for a locally executed tool_call."""
    args = event.get("args") or {}
    tool = str(event.get("tool") or "")
    result = tool_result.get("result") or ""
    error = tool_result.get("error") or ""
    audit = tool_result.pop("_audit", None)
    status = "error" if error else "ok"
    details = ""

    if tool == "satellite_write_file":
        details = f" path={args.get('path') or ''}"
    elif tool == "satellite_run_script":
        details = f" tmp_dir={TMP_DIR}"
    elif tool == "satellite_sql" and isinstance(audit, dict):
        details = (
            f" db_path={audit.get('db_path') or ''}"
            f" statement_type={audit.get('statement_type') or 'UNKNOWN'}"
            f" actions={audit.get('actions') or []}"
            f" tables={audit.get('tables') or []}"
            f" rows_affected={audit.get('rows_affected')}"
        )

    if tool == "satellite_sql":
        logger.info(
            "Yadreno Admin tool audit: request_id=%s tool_call_id=%s tool=%s "
            "status=%s result_len=%s error_len=%s%s",
            event.get("request_id"),
            event.get("tool_call_id"),
            tool,
            status,
            len(result),
            len(error),
            details,
        )
    else:
        logger.info(
            "Yadreno Admin tool audit: request_id=%s tool_call_id=%s tool=%s "
            "status=%s result_len=%s error_len=%s error_preview=%r%s",
            event.get("request_id"),
            event.get("tool_call_id"),
            tool,
            status,
            len(result),
            len(error),
            error[:200],
            details,
        )


def _core_guard_integrity_error_for_tool(
    tool: str,
    args: dict[str, Any],
    *,
    topic_id: int,
) -> Optional[str]:
    if not _core_guard_enabled(topic_id):
        return None
    if tool == "satellite_write_file":
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return None
        try:
            path = _resolve_tool_path(raw_path)
        except (OSError, RuntimeError, ValueError) as e:
            return f"core protection integrity check rejected invalid path: {e}"
        if _is_inside(path, PROJECT_ROOT / ".git"):
            return "core protection integrity check rejected: direct .git mutation"
        return None
    if tool == "satellite_execute":
        return _core_guard_integrity_error(str(args.get("command", "")).strip())
    if tool == "satellite_run_script":
        return _core_guard_integrity_error(str(args.get("script_body", "")).strip())
    return None


async def _run_tool_call(
    event: dict[str, Any],
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    telegram_id: int = 0,
) -> dict[str, Any]:
    """Executes one tool_call of the hub."""
    tool = str(event.get("tool") or "")
    args = event.get("args") or {}
    if is_yadreno_admin_broadcast_topic(topic_id):
        if tool != "satellite_broadcast_editor":
            result = {
                "result": "",
                "error": f"tool {tool} is forbidden in broadcast topic 1003",
            }
        elif not isinstance(args, dict) or telegram_id <= 0:
            result = {
                "result": "",
                "error": "invalid local broadcast editor context",
            }
        else:
            from bot.services.broadcast_editor import execute_broadcast_editor_action

            result = {
                "result": await asyncio.to_thread(
                    execute_broadcast_editor_action,
                    telegram_id,
                    args,
                ),
                "error": None,
            }
        _log_tool_audit(event, result)
        return result
    if tool == "satellite_broadcast_editor":
        result = {
            "result": "",
            "error": "satellite_broadcast_editor is allowed only in topic 1003",
        }
        _log_tool_audit(event, result)
        return result
    if tool in CUSTOMIZATION_TOOL_NAMES and not is_yadreno_admin_customization_topic(topic_id):
        result = {
            "result": "",
            "error": (
                f"{tool} is allowed only in customization topics 1001 and 1002"
            ),
        }
        _log_tool_audit(event, result)
        return result

    runtime = _runtime_context_from_event(event, topic_id=topic_id)

    async def execute_tool() -> dict[str, Any]:
        if _is_deferred_self_restart(tool, args):
            return {
                "result": (
                    "YadrenoVPN service restart queued. It will run only after the "
                    "tool result is delivered to the agent."
                ),
                "error": None,
                "_deferred_restart": True,
            }
        if tool == "satellite_execute":
            return await _execute_shell(args, runtime=runtime)
        if tool == "satellite_write_file":
            return await _write_file(args)
        if tool == "satellite_run_script":
            return await _run_script(args, runtime=runtime)
        if tool == "satellite_sql":
            return await _execute_sql(args, runtime=runtime)
        if tool in CUSTOMIZATION_TOOL_NAMES:
            if not isinstance(args, dict):
                return {"result": "", "error": "invalid customization tool arguments"}
            return {
                "result": await asyncio.to_thread(
                    execute_customization_tool,
                    tool,
                    args,
                ),
                "error": None,
            }
        return {"result": "", "error": f"unknown tool {tool}"}

    guard_error = _core_guard_integrity_error_for_tool(tool, args, topic_id=topic_id)
    if guard_error:
        result: dict[str, Any] = {"result": "", "error": guard_error}
    elif _core_guard_enabled(topic_id):
        if runtime is None:
            result = {
                "result": "",
                "error": (
                    "Core Git protection could not start because request_id or "
                    "tool_call_id is missing; the tool was NOT executed."
                ),
            }
        else:
            result = await run_with_core_guard(
                repository=PROJECT_ROOT,
                request_id=runtime.request_id,
                tool_call_id=runtime.tool_call_id,
                topic_id=runtime.topic_id,
                executor=execute_tool,
            )
    else:
        result = await execute_tool()

    _log_tool_audit(event, result)
    return result


async def _notify_progress(
    event: dict[str, Any],
    progress_callback: Optional[ProgressCallback],
) -> None:
    """Passes status/task_update to the UI layer and does not drop the agent loop."""
    if progress_callback is None:
        return

    progress_event = YadrenoAdminProgressEvent(
        event=str(event.get("event") or ""),
        content=str(event.get("content") or ""),
        slot=str(event.get("slot") or ""),
        cancel_button_text=event.get("cancel_button_text"),
    )
    try:
        await progress_callback(progress_event)
    except Exception as e:
        logger.warning(
            "Не удалось показать progress-событие Yadreno Admin: event=%s slot=%s error=%s",
            progress_event.event,
            progress_event.slot,
            e,
        )


async def _schedule_deferred_self_restart() -> None:
    """Launch a detached self-restart after the hub accepted the tool result."""
    if os.name == "nt":
        logger.warning("Deferred YadrenoVPN self-restart is unavailable on Windows")
        return
    try:
        await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            "sleep 1; systemctl restart yadreno-vpn.service",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("YadrenoVPN self-restart scheduled after tool_result delivery")
    except OSError as exc:
        logger.error("Failed to schedule deferred YadrenoVPN self-restart: %s", exc)


async def _poll_next_or_stop(
    session: aiohttp.ClientSession,
    api_key: str,
    *,
    request_id: int,
    cycle: SatelliteLaneCycle,
) -> tuple[int, Optional[dict]] | None:
    """Wait for the next Hub event or wake the local long-poll."""
    if cycle.poll_stop_requested.is_set():
        return None

    poll_task = asyncio.create_task(
        _request_json(
            session,
            api_key,
            "GET",
            f"/api/v1/satellite/poll?request_id={request_id}&timeout=30",
            allow_no_content=True,
        )
    )
    stop_task = asyncio.create_task(cycle.poll_stop_requested.wait())
    try:
        done, _ = await asyncio.wait(
            {poll_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            if not poll_task.done():
                poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)
            return None
        return await poll_task
    finally:
        if not poll_task.done():
            poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)
        if not stop_task.done():
            stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


async def _poll_until_final(
    session: aiohttp.ClientSession,
    api_key: str,
    *,
    telegram_id: int,
    topic_id: int,
    request_id: int,
    cycle: SatelliteLaneCycle,
    initial_status: dict[str, Any] | None = None,
    satellite_type: str | None = None,
    server_ip: str = "",
    progress_callback: Optional[ProgressCallback] = None,
    runtime_context_supported: bool = False,
    runtime_context_factory: Callable[[], dict[str, Any]] | None = None,
) -> YadrenoAdminFinal:
    """Single poll/tool/final loop for text and upload requests."""
    _remember_request(telegram_id, topic_id, request_id, active=True)
    logger.info(
        "Yadreno Admin request accepted: admin=%s topic=%s request_id=%s satellite_type=%s server_ip=%s",
        telegram_id,
        topic_id,
        request_id,
        satellite_type,
        server_ip,
    )
    if initial_status and initial_status.get("response_text"):
        await _notify_progress(
            {"event": "status", "content": initial_status["response_text"],
             "cancel_button_text": initial_status.get("cancel_button_text")},
            progress_callback,
        )
    final_received = False
    try:
        while True:
            poll_result = await _poll_next_or_stop(
                session,
                api_key,
                request_id=request_id,
                cycle=cycle,
            )
            if cycle.poll_stop_requested.is_set() or poll_result is None:
                await finalize_core_guards_for_request(request_id)
                _clear_active_request(
                    telegram_id,
                    topic_id,
                    expected_request_id=request_id,
                )
                raise YadrenoAdminRequestStopped(request_id)
            status_code, event = poll_result
            if status_code == 204:
                continue
            if not event:
                raise YadrenoAdminError("Хаб вернул пустое событие")

            if event.get("event") == "tool_call":
                tool_call_id = str(event.get("tool_call_id") or "")
                tool_started_here = False
                runtime = _runtime_context_from_event(event, topic_id=topic_id)
                logger.info(
                    "Yadreno Admin tool_call: admin=%s topic=%s request_id=%s tool_call_id=%s tool=%s",
                    telegram_id,
                    topic_id,
                    request_id,
                    tool_call_id,
                    event.get("tool"),
                )
                if not tool_call_id:
                    tool_result = {
                        "result": "",
                        "error": "tool_call without tool_call_id",
                    }
                elif not mark_yadreno_admin_tool_call_started(request_id, tool_call_id):
                    tool_result = interrupted_tool_result(request_id, tool_call_id) or {
                        "result": "",
                        "error": (
                            "Локальный сателлит был перезапущен во время выполнения "
                            "этого tool_call. Результат неизвестен; проверь состояние "
                            "новыми read-only командами и продолжай без повторения "
                            "опасного действия."
                        ),
                    }
                else:
                    tool_started_here = True
                    _remember_tool_runtime(runtime)
                try:
                    if tool_started_here:
                        if is_yadreno_admin_broadcast_topic(topic_id):
                            tool_result = await _run_tool_call(
                                event,
                                topic_id=topic_id,
                                telegram_id=telegram_id,
                            )
                        else:
                            tool_result = await _run_tool_call(
                                event,
                                topic_id=topic_id,
                            )
                    deferred_restart = bool(tool_result.pop("_deferred_restart", False))
                    tool_result_payload: dict[str, Any] = {
                        "request_id": request_id,
                        "tool_call_id": tool_call_id,
                        **tool_result,
                    }
                    if runtime_context_supported and runtime_context_factory:
                        try:
                            tool_result_payload["runtime_context"] = (
                                runtime_context_factory()
                            )
                        except YaaPageBindingContextError as exc:
                            logger.warning(
                                "Omitting post-tool runtime_context for request %s "
                                "tool_call %s: %s",
                                request_id,
                                tool_call_id,
                                exc,
                            )
                    await _request_json(
                        session,
                        api_key,
                        "POST",
                        "/api/v1/satellite/tool_result",
                        json_payload=tool_result_payload,
                    )
                    guard_finalized = await finalize_core_guard(request_id, tool_call_id)
                    if tool_call_id:
                        clear_yadreno_admin_tool_call_started(request_id, tool_call_id)
                    if deferred_restart and guard_finalized:
                        await _schedule_deferred_self_restart()
                finally:
                    if tool_started_here:
                        _clear_tool_runtime(request_id, tool_call_id)
                continue

            event_type = event.get("event")
            if event_type in {"status", "task_update"}:
                await _notify_progress(event, progress_callback)
                continue

            if event_type == "final":
                await finalize_core_guards_for_request(request_id)
                final_received = True
                return YadrenoAdminFinal(
                    content=event.get("content") or "",
                    rich_markdown=event.get("rich_markdown"),
                    viewer_url=event.get("viewer_url"),
                    request_id=request_id,
                )

            raise YadrenoAdminError(f"Неизвестное событие хаба: {event}")
    finally:
        if final_received:
            _clear_active_request(
                telegram_id,
                topic_id,
                expected_request_id=request_id,
            )


async def run_dialog(
    telegram_id: int,
    api_key: str,
    message: str,
    *,
    topic_id: int = 0,
    skill_id: Optional[str] = None,
    runtime_context: Optional[dict[str, Any]] = None,
    progress_callback: Optional[ProgressCallback] = None,
    page_binding: YaaPageBinding | None = None,
) -> YadrenoAdminFinal:
    """
    Performs a full cycle of dialogue with the Yadreno Admin agent.

    The Hub admits one active request per topic and rejects overlapping turns.
    Local polling ownership is acquired only after acceptance.
    """
    key = _lane_key(telegram_id, topic_id)
    runtime_context_factory = _runtime_context_factory_for_turn(
        telegram_id, topic_id, runtime_context, page_binding,
    )
    timeout = aiohttp.ClientTimeout(total=70)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
        negotiated_capabilities: set[str] = set()
        runtime_context_supported = await _negotiate_runtime_context_support(
            session,
            api_key,
            effective_skill_id,
            negotiated_capabilities,
        )
        server_ip = await _get_server_ip(session)
        core_changes_allowed = _core_policy_for_skill(effective_skill_id)
        agent_runtime_context = (
            None
            if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
            else _build_runtime_context_or_error(runtime_context_factory)
        )
        agent_message = (
            message
            if (
                effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
                or runtime_context_supported
            )
            else _with_agent_runtime_context(
                message,
                agent_runtime_context or {},
            )
        )
        payload: dict[str, Any] = {
            "message": agent_message,
            "server_ip": server_ip,
            "topic_id": topic_id,
            "skill_id": effective_skill_id,
            "capabilities": _capabilities_for_skill(
                effective_skill_id,
                runtime_context_supported=runtime_context_supported,
                customization_tools_supported=(
                    CUSTOMIZATION_TOOLS_CAPABILITY in negotiated_capabilities
                ),
            ),
        }
        if runtime_context_supported:
            if agent_runtime_context is None:
                raise YadrenoAdminError(
                    "Hub runtime-context negotiation invariant failed"
                )
            payload["runtime_context"] = agent_runtime_context
        if core_changes_allowed is not None:
            payload["core_changes_allowed"] = core_changes_allowed
        _, process_data = await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/process",
            json_payload=payload,
        )
        if not process_data:
            raise YadrenoAdminError("Хаб вернул пустой ответ на /process")

        _validate_specialized_hub_response(process_data, effective_skill_id)
        _raise_for_hub_rejection(process_data, "process request")

        request_id = _accepted_request_id(process_data, "process request")
        async with satellite_lane_controller.cycle(key, request_id) as cycle:
            if cycle is None:
                raise YadrenoAdminError("Hub returned an already polled request", kind="protocol")
            if page_binding is not None:
                set_yaa_page_binding(telegram_id, topic_id, page_binding)
            return await _poll_until_final(
                session,
                api_key,
                telegram_id=telegram_id,
                topic_id=topic_id,
                request_id=request_id,
                initial_status=process_data,
                satellite_type=process_data.get("satellite_type"),
                server_ip=server_ip,
                progress_callback=progress_callback,
                runtime_context_supported=runtime_context_supported,
                runtime_context_factory=(
                    runtime_context_factory
                    if effective_skill_id != YADRENO_ADMIN_BROADCAST_SKILL_ID
                    else None
                ),
                cycle=cycle,
            )


async def run_dialog_with_uploads(
    telegram_id: int,
    api_key: str,
    message: str,
    uploads: list[YadrenoAdminUpload],
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    skill_id: Optional[str] = None,
    runtime_context: Optional[dict[str, Any]] = None,
    progress_callback: Optional[ProgressCallback] = None,
    page_binding: YaaPageBinding | None = None,
    overflow_count: int = 0,
) -> YadrenoAdminFinal:
    """Sends files to Yadreno Admin and waits for the final response from the agent."""
    if not uploads:
        return await run_dialog(
            telegram_id,
            api_key,
            message,
            topic_id=topic_id,
            skill_id=skill_id,
            runtime_context=runtime_context,
            progress_callback=progress_callback,
            page_binding=page_binding,
        )

    key = _lane_key(telegram_id, topic_id)
    runtime_context_factory = _runtime_context_factory_for_turn(
        telegram_id, topic_id, runtime_context, page_binding,
    )
    timeout = aiohttp.ClientTimeout(total=70)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
        negotiated_capabilities: set[str] = set()
        runtime_context_supported = await _negotiate_runtime_context_support(
            session,
            api_key,
            effective_skill_id,
            negotiated_capabilities,
        )
        server_ip = await _get_server_ip(session)
        core_changes_allowed = _core_policy_for_skill(effective_skill_id)
        agent_runtime_context = (
            None
            if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
            else _build_runtime_context_or_error(runtime_context_factory)
        )
        agent_message = (
            message
            if (
                effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
                or runtime_context_supported
            )
            else _with_agent_runtime_context(
                message,
                agent_runtime_context or {},
            )
        )
        is_batch = len(uploads) > 1
        path = (
            "/api/v1/satellite/upload_batch"
            if is_batch
            else "/api/v1/satellite/upload"
        )
        fields: dict[str, str | int] = {
            "message": agent_message,
            "topic_id": topic_id,
            "server_ip": server_ip,
            "skill_id": effective_skill_id,
            "capabilities": ",".join(_capabilities_for_skill(
                effective_skill_id,
                runtime_context_supported=runtime_context_supported,
                customization_tools_supported=(
                    CUSTOMIZATION_TOOLS_CAPABILITY in negotiated_capabilities
                ),
            )),
        }
        if runtime_context_supported:
            if agent_runtime_context is None:
                raise YadrenoAdminError(
                    "Hub runtime-context negotiation invariant failed"
                )
            fields["runtime_context"] = json.dumps(
                agent_runtime_context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if core_changes_allowed is not None:
            fields["core_changes_allowed"] = "true" if core_changes_allowed else "false"
        if is_batch:
            fields["overflow_count"] = overflow_count
        _, upload_data = await _request_multipart(
            session,
            api_key,
            path,
            fields=fields,
            uploads=uploads,
            file_field="files" if is_batch else "file",
        )
        if not upload_data:
            raise YadrenoAdminError("Хаб вернул пустой ответ на upload")

        _validate_specialized_hub_response(upload_data, effective_skill_id)
        _raise_for_hub_rejection(upload_data, "upload request")

        request_id = _accepted_request_id(upload_data, "upload request")
        async with satellite_lane_controller.cycle(key, request_id) as cycle:
            if cycle is None:
                raise YadrenoAdminError("Hub returned an already polled request", kind="protocol")
            if page_binding is not None:
                set_yaa_page_binding(telegram_id, topic_id, page_binding)
            return await _poll_until_final(
                session,
                api_key,
                telegram_id=telegram_id,
                topic_id=topic_id,
                request_id=request_id,
                initial_status=upload_data,
                satellite_type=upload_data.get("satellite_type"),
                server_ip=server_ip,
                progress_callback=progress_callback,
                runtime_context_supported=runtime_context_supported,
                runtime_context_factory=(
                    runtime_context_factory
                    if effective_skill_id != YADRENO_ADMIN_BROADCAST_SKILL_ID
                    else None
                ),
                cycle=cycle,
            )


async def resume_active_dialog(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    progress_callback: Optional[ProgressCallback] = None,
) -> Optional[YadrenoAdminFinal]:
    """Restores polling of an active request after a local bot restart."""
    request_id = get_active_request_id(telegram_id, topic_id)
    if request_id is None:
        return None

    key = _lane_key(telegram_id, topic_id)
    async with satellite_lane_controller.cycle(key, request_id) as cycle:
        if cycle is None:
            return None
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            server_ip = await _get_server_ip(session)
            hub_status = await fetch_dialog_status(
                telegram_id,
                api_key,
                topic_id=topic_id,
                request_id=request_id,
            )
            if hub_status is None or hub_status.status == "idle":
                return None
            if not hub_status.resume_allowed:
                raise YadrenoAdminError(
                    hub_status.response_text
                    or "Hub не подтвердил живую задачу для восстановления polling."
                )
            return await _poll_until_final(
                session,
                api_key,
                telegram_id=telegram_id,
                topic_id=topic_id,
                request_id=request_id,
                server_ip=server_ip,
                initial_status={
                    "response_text": hub_status.response_text,
                    "cancel_button_text": hub_status.cancel_button_text,
                },
                progress_callback=progress_callback,
                cycle=cycle,
            )


def _latest_from_event(request_id: int, event: Optional[dict[str, Any]]) -> YadrenoAdminLatest:
    """Converts a hub snapshot into a local structure without tool_call."""
    if not event:
        return YadrenoAdminLatest(request_id=request_id)

    event_type = str(event.get("event") or "")
    if event_type == "final":
        return YadrenoAdminLatest(
            request_id=request_id,
            event=event_type,
            final=YadrenoAdminFinal(
                content=str(event.get("content") or ""),
                rich_markdown=event.get("rich_markdown"),
                viewer_url=event.get("viewer_url"),
                request_id=request_id,
            ),
        )
    if event_type in {"status", "task_update"}:
        return YadrenoAdminLatest(
            request_id=request_id,
            event=event_type,
            progress=YadrenoAdminProgressEvent(
                event=event_type,
                content=str(event.get("content") or ""),
                slot=str(event.get("slot") or ""),
                cancel_button_text=event.get("cancel_button_text"),
            ),
        )

    logger.warning(
        "Yadreno Admin latest ignored unsupported event: admin_request_id=%s event=%s",
        request_id,
        event_type,
    )
    return YadrenoAdminLatest(request_id=request_id)


def _status_from_data(
    request_id: Optional[int],
    data: Optional[dict[str, Any]],
) -> YadrenoAdminHubStatus:
    """Converts the hub /status response to a local structure."""
    if not data:
        return YadrenoAdminHubStatus(status="unsafe_unknown", request_id=request_id)
    status = str(data.get("status") or "unsafe_unknown")
    return YadrenoAdminHubStatus(
        status=status,
        response_text=str(data.get("response_text") or ""),
        request_id=data.get("request_id") or request_id,
        retry_after_sec=data.get("retry_after_sec"),
        local_tool_running=bool(data.get("local_tool_running")),
        local_tool_call_id=data.get("local_tool_call_id"),
        resume_allowed=data.get("resume_allowed") is True,
        cancel_button_text=data.get("cancel_button_text"),
    )


def _latest_from_hub_status(
    request_id: int,
    hub_status: YadrenoAdminHubStatus,
) -> YadrenoAdminLatest:
    """Shows read-only /status as a progress event without running /poll."""
    return YadrenoAdminLatest(
        request_id=request_id,
        event="hub_status",
        progress=YadrenoAdminProgressEvent(
            event="status",
            content=hub_status.response_text,
            slot="hub_status",
            cancel_button_text=hub_status.cancel_button_text,
        ),
        resume_allowed=hub_status.resume_allowed,
        cancel_button_text=hub_status.cancel_button_text,
    )


async def fetch_dialog_status(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    request_id: Optional[int] = None,
) -> Optional[YadrenoAdminHubStatus]:
    """Read Hub status without taking ownership of the active poll."""
    request_id = request_id or get_active_request_id(telegram_id, topic_id) or get_last_request_id(
        telegram_id,
        topic_id,
    )
    if request_id is None:
        return None

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        _, data = await _request_json(
            session,
            api_key,
            "GET",
            f"/api/v1/satellite/status?topic_id={topic_id}&request_id={request_id}",
        )
    hub_status = _status_from_data(request_id, data)
    local_diag = get_local_tool_diagnostics(request_id, topic_id)
    if local_diag.get("local_tool_running"):
        hub_status.local_tool_running = True
        hub_status.local_tool_call_id = str(
            local_diag.get("local_tool_call_id") or ""
        ) or None
    elif hub_status.status in {"idle", "orphan_cleared"}:
        _signal_poll_stop(telegram_id, topic_id, request_id)
        _clear_active_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
    return hub_status


async def fetch_latest_dialog_event(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> Optional[YadrenoAdminLatest]:
    """Reads the latest snapshot via /latest, without consuming /poll."""
    request_id = get_active_request_id(telegram_id, topic_id) or get_last_request_id(
        telegram_id,
        topic_id,
    )
    if request_id is None:
        return None

    hub_status = await fetch_dialog_status(
        telegram_id,
        api_key,
        topic_id=topic_id,
        request_id=request_id,
    )

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        status_code, event = await _request_json(
            session,
            api_key,
            "GET",
            f"/api/v1/satellite/latest?request_id={request_id}",
            allow_no_content=True,
        )
    if status_code == 204:
        if (
            hub_status is not None
            and hub_status.status in {"idle", "orphan_cleared"}
            and not hub_status.local_tool_running
        ):
            _signal_poll_stop(telegram_id, topic_id, request_id)
            _clear_active_request(
                telegram_id,
                topic_id,
                expected_request_id=request_id,
            )
            _clear_last_request(
                telegram_id,
                topic_id,
                expected_request_id=request_id,
            )
            return None
        if hub_status is not None and not hub_status.resume_allowed:
            return _latest_from_hub_status(request_id, hub_status)
        return YadrenoAdminLatest(
            request_id=request_id,
            resume_allowed=hub_status.resume_allowed if hub_status else True,
        )
    latest = _latest_from_event(request_id, event)
    if (
        hub_status is not None
        and hub_status.status in {"idle", "orphan_cleared"}
        and not hub_status.local_tool_running
        and latest.final is None
    ):
        _signal_poll_stop(telegram_id, topic_id, request_id)
        _clear_active_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
        _clear_last_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
        return None
    if hub_status is not None:
        latest.resume_allowed = hub_status.resume_allowed
        latest.cancel_button_text = hub_status.cancel_button_text
    if latest.final is not None:
        _signal_poll_stop(telegram_id, topic_id, request_id)
        _clear_active_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
    return latest


async def start_new_chat(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    skill_id: Optional[str] = None,
) -> YadrenoAdminNewChatResult:
    """Asks the hub to close the active satellite session if the lane is free."""
    timeout = aiohttp.ClientTimeout(total=20)
    effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Discovery chooses a reachable trusted endpoint before the only
        # side-effectful request is sent.
        await _negotiate_runtime_context_support(
            session,
            api_key,
            effective_skill_id,
        )
        previous_active = get_active_request_id(telegram_id, topic_id)
        previous_last = get_last_request_id(telegram_id, topic_id)
        payload: dict[str, Any] = {
            "topic_id": topic_id,
            "skill_id": effective_skill_id,
        }
        if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
            payload["capabilities"] = _capabilities_for_skill(
                effective_skill_id
            )
        _, data = await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/new_chat",
            json_payload=payload,
        )
    if not data:
        raise YadrenoAdminError("Хаб вернул пустой ответ на /new_chat")

    _validate_specialized_hub_response(data, effective_skill_id)
    result = YadrenoAdminNewChatResult(
        status=str(data.get("status") or ""),
        response_text=str(data.get("response_text") or ""),
        closed_session_id=data.get("closed_session_id"),
        cancel_button_text=data.get("cancel_button_text"),
    )
    if result.status == "ok":
        if previous_active is not None:
            _clear_active_request(telegram_id, topic_id, expected_request_id=previous_active)
        if previous_last is not None:
            _clear_last_request(telegram_id, topic_id, expected_request_id=previous_last)
    return result


async def _cancel_request(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int,
    request_id: int,
) -> YadrenoAdminCancelResult:
    """Send cancellation for one known Hub request."""
    local_diag = get_local_tool_diagnostics(request_id, topic_id)

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        _, data = await _request_json(
            session, api_key, "POST", "/api/v1/satellite/cancel",
            json_payload={
                "request_id": request_id, "topic_id": topic_id, **local_diag,
            },
        )

    hub_status = _status_from_data(request_id, data)
    logger.info(
        "Yadreno Admin cancel result: admin=%s topic=%s request_id=%s status=%s",
        telegram_id,
        topic_id,
        request_id,
        hub_status.status,
    )
    current_local_diag = get_local_tool_diagnostics(request_id, topic_id)
    local_tool_running = bool(
        hub_status.local_tool_running
        or current_local_diag.get("local_tool_running")
    )
    if hub_status.status in {"orphan_cleared", "idle"}:
        if local_tool_running:
            raise YadrenoAdminError(
                "Hub terminal status conflicts with a running local tool",
                kind="protocol",
            )
        _signal_poll_stop(telegram_id, topic_id, request_id)
        _clear_active_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
        _clear_last_request(
            telegram_id,
            topic_id,
            expected_request_id=request_id,
        )
    return YadrenoAdminCancelResult(
        status=hub_status.status,
        response_text=hub_status.response_text,
        cancel_button_text=hub_status.cancel_button_text,
        request_id=request_id,
        retry_after_sec=hub_status.retry_after_sec,
    )


async def cancel_active_dialog(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> YadrenoAdminCancelResult:
    """Cancel the cycle that is current in this conversation lane."""
    request_id = get_active_request_id(telegram_id, topic_id)
    if request_id is None:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            _, data = await _request_json(
                session, api_key, "GET",
                f"/api/v1/satellite/status?topic_id={topic_id}",
            )
        hub_status = _status_from_data(None, data)
        request_id = hub_status.request_id
        if request_id is None or hub_status.status == "idle":
            return YadrenoAdminCancelResult(
                status=hub_status.status, response_text=hub_status.response_text,
                cancel_button_text=hub_status.cancel_button_text,
                request_id=request_id,
            )
    return await _cancel_request(
        telegram_id,
        api_key,
        topic_id=topic_id,
        request_id=request_id,
    )


def _format_recovered_final(content: str) -> str:
    """Formats the final delivered by background startup recovery."""
    return content or "Готово."


def _recovered_final_keyboard(topic_id: int, viewer_url: Optional[str]) -> Any:
    """Build inactive agent controls for a startup-recovered final response."""
    if is_yadreno_admin_broadcast_topic(topic_id):
        from bot.keyboards.admin_broadcast import broadcast_editor_kb

        return broadcast_editor_kb()
    from bot.keyboards.admin_yadreno import yadreno_admin_agent_kb

    return yadreno_admin_agent_kb(
        topic_id,
        active_request=False,
        viewer_url=viewer_url,
    )


async def _recover_one_active_dialog_on_startup(
    bot: Any,
    api_key: str,
    *,
    telegram_id: int,
    topic_id: int,
    request_id: int,
) -> None:
    """Checks one active request after restart and continues only live tasks."""
    from bot.utils.yadreno_admin_delivery import send_yadreno_admin_final

    try:
        hub_status = await fetch_dialog_status(
            telegram_id,
            api_key,
            topic_id=topic_id,
            request_id=request_id,
        )
        if hub_status is None or not hub_status.resume_allowed:
            if hub_status is not None and hub_status.status == "idle":
                latest = await fetch_latest_dialog_event(
                    telegram_id,
                    api_key,
                    topic_id=topic_id,
                )
                if latest is not None and latest.final is not None:
                    await send_yadreno_admin_final(
                        bot,
                        chat_id=telegram_id,
                        fallback_html=_format_recovered_final(latest.final.content),
                        rich_markdown=latest.final.rich_markdown,
                        reply_markup=_recovered_final_keyboard(
                            topic_id,
                            latest.final.viewer_url,
                        ),
                    )
            logger.info(
                "Yadreno Admin startup recovery skipped: admin=%s topic=%s request_id=%s status=%s",
                telegram_id,
                topic_id,
                request_id,
                hub_status.status if hub_status else None,
            )
            return

        final = await resume_active_dialog(
            telegram_id,
            api_key,
            topic_id=topic_id,
            progress_callback=None,
        )
        if final is None:
            return
        await send_yadreno_admin_final(
            bot,
            chat_id=telegram_id,
            fallback_html=_format_recovered_final(final.content),
            rich_markdown=final.rich_markdown,
            reply_markup=_recovered_final_keyboard(topic_id, final.viewer_url),
        )
    except YadrenoAdminRequestStopped:
        return
    except Exception as e:
        logger.warning(
            "Yadreno Admin startup recovery failed: admin=%s topic=%s request_id=%s error=%s",
            telegram_id,
            topic_id,
            request_id,
            e,
        )


async def recover_active_dialogs_on_startup(bot: Any) -> None:
    """Runs best-effort recovery of live Yadreno Admin tasks after a restart."""
    api_key = get_yadreno_admin_api_key()
    if not api_key:
        return
    active_requests = list_yadreno_admin_active_requests()
    if not active_requests:
        return

    try:
        _hub_headers(api_key)
    except YadrenoAdminError as error:
        if error.kind != "configuration":
            raise
        from bot.keyboards.admin_yadreno import yadreno_admin_chat_kb
        from bot.utils.yadreno_admin_errors import format_yadreno_admin_error

        for telegram_id in sorted({
            int(item["telegram_id"])
            for item in active_requests
        }):
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=format_yadreno_admin_error(error),
                    reply_markup=yadreno_admin_chat_kb(
                        YADRENO_ADMIN_CHAT_TOPIC_ID
                    ),
                    parse_mode="HTML",
                )
            except Exception as delivery_error:
                logger.warning(
                    "Yadreno Admin configuration notice delivery failed: "
                    "admin=%s error=%s",
                    telegram_id,
                    delivery_error,
                )
        return

    for item in active_requests:
        asyncio.create_task(
            _recover_one_active_dialog_on_startup(
                bot,
                api_key,
                telegram_id=int(item["telegram_id"]),
                topic_id=int(item["topic_id"]),
                request_id=int(item["request_id"]),
            )
        )
