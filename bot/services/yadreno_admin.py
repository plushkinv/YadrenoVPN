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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from bot.services.yadreno_admin_core_guard import (
    finalize_core_guard,
    finalize_core_guards_for_request,
    interrupted_tool_result,
    run_with_core_guard,
)
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

HUB_URL = "https://admin.yadreno.ru"
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
SATELLITE_CAPABILITIES: tuple[str, ...] = (PROGRESS_EVENTS_CAPABILITY,)
PUBLIC_IP_URLS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
)

_server_ip_cache: Optional[str] = None
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


def is_yadreno_admin_broadcast_topic(topic_id: int) -> bool:
    """Return True only for the structured broadcast editor lane."""
    return int(topic_id) == YADRENO_ADMIN_BROADCAST_TOPIC_ID


def _capabilities_for_skill(skill_id: str) -> list[str]:
    """Advertise the editor capability only inside its isolated skill."""
    capabilities = list(SATELLITE_CAPABILITIES)
    if skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
        capabilities.append(BROADCAST_EDITOR_CAPABILITY)
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


def build_agent_env_context(core_changes_allowed: bool | None) -> dict[str, Any]:
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
        "core_changes_allowed": True if core_changes_allowed is None else bool(core_changes_allowed),
        "custom_extensions_loader_enabled": bool(custom_extensions_loader_enabled),
    }


def build_agent_env_context_for_topic(
    topic_id: int,
    skill_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return compact environment context for the resolved Yadreno Admin lane."""
    effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
    return build_agent_env_context(_core_policy_for_skill(effective_skill_id))


def _message_has_agent_env_context(message: str) -> bool:
    return '"env":{' in message or '"env": {' in message


def _with_agent_env_context(message: str, env_context: dict[str, Any]) -> str:
    """Prefix a user request with compact service context unless it already has it."""
    if _message_has_agent_env_context(message):
        return message
    payload = {"env": env_context}
    return (
        "Служебный контекст:\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"{message}"
    )


class YadrenoAdminError(RuntimeError):
    """Error communicating with the Yadreno Admin hub."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.user_message = user_message


class DangerousShellCommandError(ValueError):
    """The command was rejected by the local deny-list."""


@dataclass
class YadrenoAdminFinal:
    """Agent's final response."""

    content: str
    viewer_url: Optional[str] = None
    request_id: Optional[int] = None


@dataclass
class YadrenoAdminProgressEvent:
    """Intermediate user event from the hub."""

    event: str
    content: str
    slot: str = ""


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


@dataclass
class YadrenoAdminNewChatResult:
    """Result of a new chat request on the hub."""

    status: str
    response_text: str = ""
    closed_session_id: Optional[int] = None


@dataclass
class YadrenoAdminHubStatus:
    """Request/lane status according to hub /status."""

    status: str
    response_text: str = ""
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
    request_id: Optional[int] = None
    retry_after_sec: Optional[float] = None


ProgressCallback = Callable[[YadrenoAdminProgressEvent], Awaitable[None]]
RequestLaneKey = tuple[int, int]

_request_locks: dict[RequestLaneKey, asyncio.Lock] = defaultdict(asyncio.Lock)
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
    """Checks whether the current process is polling for lane."""
    return _lane_key(telegram_id, topic_id) in _active_requests


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


def _clear_active_request(telegram_id: int, topic_id: int) -> None:
    """Clears active request_id in memory and settings."""
    _active_requests.pop(_lane_key(telegram_id, topic_id), None)
    clear_yadreno_admin_active_request_id(telegram_id, topic_id)


def _clear_last_request(telegram_id: int, topic_id: int) -> None:
    """Clears last request_id in memory and settings."""
    _last_requests.pop(_lane_key(telegram_id, topic_id), None)
    clear_yadreno_admin_last_request_id(telegram_id, topic_id)


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
    headers = {"Authorization": f"Bearer {api_key}"}
    delays = RETRY_CONFIG.get("delays", [1, 3, 9])
    max_attempts = RETRY_CONFIG.get("max_attempts", 3)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with session.request(
                method,
                f"{HUB_URL}{path}",
                headers=headers,
                json=json_payload,
            ) as response:
                if allow_no_content and response.status == 204:
                    return response.status, None
                if response.status >= 400:
                    body = await response.text()
                    raise YadrenoAdminError(
                        f"Хаб вернул HTTP {response.status}: {body[:500]}",
                        status_code=response.status,
                    )
                data = await response.json()
                return response.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError, YadrenoAdminError) as e:
            last_error = e
            if attempt >= max_attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)]
            logger.warning(
                "Ошибка запроса к Yadreno Admin (%s %s), попытка %s/%s: %s",
                method,
                path,
                attempt,
                max_attempts,
                e,
            )
            await asyncio.sleep(delay)

    raise YadrenoAdminError(
        f"Не удалось связаться с хабом Yadreno Admin: {last_error}",
        status_code=(
            last_error.status_code
            if isinstance(last_error, YadrenoAdminError)
            else None
        ),
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
        raise _incompatible_broadcast_hub(
            f"capability discovery failed ({error.status_code or 'network'})"
        ) from error
    if not isinstance(data, dict):
        raise _incompatible_broadcast_hub("empty capability response")
    capabilities = data.get("capabilities")
    allowed_skills = data.get("allowed_skill_ids")
    if data.get("satellite_type") != YADRENO_ADMIN_SATELLITE_TYPE:
        raise _incompatible_broadcast_hub("wrong satellite_type")
    if not isinstance(capabilities, list) or BROADCAST_EDITOR_CAPABILITY not in capabilities:
        raise _incompatible_broadcast_hub("broadcast_editor_v1 is absent")
    if not isinstance(allowed_skills, list) or skill_id not in allowed_skills:
        raise _incompatible_broadcast_hub("broadcast skill is not allowed")


def _validate_broadcast_hub_response(data: dict[str, Any], skill_id: str) -> None:
    """Reject fallback to a general skill after broadcast negotiation."""
    if skill_id != YADRENO_ADMIN_BROADCAST_SKILL_ID:
        return
    if data.get("satellite_type") != YADRENO_ADMIN_SATELLITE_TYPE:
        raise _incompatible_broadcast_hub("response satellite_type mismatch")
    if data.get("skill_id") != skill_id:
        raise _incompatible_broadcast_hub("response skill_id mismatch")


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
    headers = {"Authorization": f"Bearer {api_key}"}
    delays = RETRY_CONFIG.get("delays", [1, 3, 9])
    max_attempts = RETRY_CONFIG.get("max_attempts", 3)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
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
                f"{HUB_URL}{path}",
                headers=headers,
                data=form,
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise YadrenoAdminError(
                        f"Хаб вернул HTTP {response.status}: {body[:500]}",
                        status_code=response.status,
                    )
                return response.status, await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, YadrenoAdminError) as e:
            last_error = e
            if attempt >= max_attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)]
            logger.warning(
                "Ошибка upload-запроса к Yadreno Admin (%s), попытка %s/%s: %s",
                path,
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

    raise YadrenoAdminError(
        f"Не удалось загрузить файл в Yadreno Admin: {last_error}",
        status_code=(
            last_error.status_code
            if isinstance(last_error, YadrenoAdminError)
            else None
        ),
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
) -> dict[str, Optional[str]]:
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

    def _run() -> dict[str, Optional[str]]:
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query)
                if cursor.description:
                    columns = [item[0] for item in cursor.description]
                    rows = cursor.fetchall()
                    return {"result": _format_sql_rows(rows, columns), "error": None}
                conn.commit()
                return {"result": f"OK, rows_affected={cursor.rowcount}", "error": None}
        except Exception as e:
            return {"result": "", "error": str(e)}

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
) -> dict[str, Optional[str]]:
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


def _log_tool_audit(event: dict[str, Any], tool_result: dict[str, Optional[str]]) -> None:
    """Writes an audit log for a locally executed tool_call."""
    args = event.get("args") or {}
    tool = str(event.get("tool") or "")
    result = tool_result.get("result") or ""
    error = tool_result.get("error") or ""
    status = "error" if error else "ok"
    details = ""

    if tool == "satellite_write_file":
        details = f" path={args.get('path') or ''}"
    elif tool == "satellite_run_script":
        details = f" tmp_dir={TMP_DIR}"

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

    runtime = _runtime_context_from_event(event, topic_id=topic_id)
    _remember_tool_runtime(runtime)

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


async def _poll_until_final(
    session: aiohttp.ClientSession,
    api_key: str,
    *,
    telegram_id: int,
    topic_id: int,
    request_id: int,
    satellite_type: str | None = None,
    server_ip: str = "",
    progress_callback: Optional[ProgressCallback] = None,
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
    final_received = False
    try:
        while True:
            status_code, event = await _request_json(
                session,
                api_key,
                "GET",
                f"/api/v1/satellite/poll?request_id={request_id}&timeout=30",
                allow_no_content=True,
            )
            if status_code == 204:
                continue
            if not event:
                raise YadrenoAdminError("Хаб вернул пустое событие")

            if event.get("event") == "tool_call":
                tool_call_id = str(event.get("tool_call_id") or "")
                tool_started_here = False
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
                    await _request_json(
                        session,
                        api_key,
                        "POST",
                        "/api/v1/satellite/tool_result",
                        json_payload={
                            "request_id": request_id,
                            "tool_call_id": tool_call_id,
                            **tool_result,
                        },
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
                    viewer_url=event.get("viewer_url"),
                    request_id=request_id,
                )

            raise YadrenoAdminError(f"Неизвестное событие хаба: {event}")
    finally:
        if final_received:
            _clear_active_request(telegram_id, topic_id)


async def run_dialog(
    telegram_id: int,
    api_key: str,
    message: str,
    *,
    topic_id: int = 0,
    skill_id: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> YadrenoAdminFinal:
    """
    Performs a full cycle of dialogue with the Yadreno Admin agent.

    One administrator simultaneously handles only one request in one lane
    Yadreno Admin. Regular chat and /yaa use different topic_ids.
    """
    key = _lane_key(telegram_id, topic_id)
    async with _request_locks[key]:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
            await _ensure_broadcast_hub_support(session, api_key, effective_skill_id)
            server_ip = await _get_server_ip(session)
            core_changes_allowed = _core_policy_for_skill(effective_skill_id)
            agent_message = (
                message
                if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
                else _with_agent_env_context(
                    message,
                    build_agent_env_context(core_changes_allowed),
                )
            )
            payload: dict[str, Any] = {
                "message": agent_message,
                "server_ip": server_ip,
                "topic_id": topic_id,
                "skill_id": effective_skill_id,
                "capabilities": _capabilities_for_skill(effective_skill_id),
            }
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

            _validate_broadcast_hub_response(process_data, effective_skill_id)
            status = process_data.get("status")
            if status != "accepted":
                response_text = process_data.get("response_text") or f"Запрос отклонён: {status}"
                raise YadrenoAdminError(response_text)

            request_id = int(process_data["request_id"])
            return await _poll_until_final(
                session,
                api_key,
                telegram_id=telegram_id,
                topic_id=topic_id,
                request_id=request_id,
                satellite_type=process_data.get("satellite_type"),
                server_ip=server_ip,
                progress_callback=progress_callback,
            )


async def run_dialog_with_uploads(
    telegram_id: int,
    api_key: str,
    message: str,
    uploads: list[YadrenoAdminUpload],
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    skill_id: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
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
            progress_callback=progress_callback,
        )

    key = _lane_key(telegram_id, topic_id)
    async with _request_locks[key]:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
            await _ensure_broadcast_hub_support(session, api_key, effective_skill_id)
            server_ip = await _get_server_ip(session)
            core_changes_allowed = _core_policy_for_skill(effective_skill_id)
            agent_message = (
                message
                if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID
                else _with_agent_env_context(
                    message,
                    build_agent_env_context(core_changes_allowed),
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
                "capabilities": ",".join(_capabilities_for_skill(effective_skill_id)),
            }
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

            _validate_broadcast_hub_response(upload_data, effective_skill_id)
            status = upload_data.get("status")
            if status != "accepted":
                response_text = upload_data.get("response_text") or f"Загрузка отклонена: {status}"
                raise YadrenoAdminError(response_text)

            request_id = int(upload_data["request_id"])
            return await _poll_until_final(
                session,
                api_key,
                telegram_id=telegram_id,
                topic_id=topic_id,
                request_id=request_id,
                satellite_type=upload_data.get("satellite_type"),
                server_ip=server_ip,
                progress_callback=progress_callback,
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
    async with _request_locks[key]:
        request_id = get_active_request_id(telegram_id, topic_id)
        if request_id is None:
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
                progress_callback=progress_callback,
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
        resume_allowed=status in {
            "running",
            "cancel_requested",
            "accepted_running",
        },
    )


def _latest_from_hub_status(
    request_id: int,
    hub_status: YadrenoAdminHubStatus,
) -> YadrenoAdminLatest:
    """Shows read-only /status as a progress event without running /poll."""
    text = hub_status.response_text or "Состояние задачи пока не определено."
    if hub_status.status in {"orphan_suspected", "orphan_confirmed"}:
        text += "\n\nНажмите «Отмена», чтобы выполнить безопасную двухфазную проверку."
    if hub_status.status == "unsafe_unknown":
        text += "\n\nЛокальные флаги не очищены."
    return YadrenoAdminLatest(
        request_id=request_id,
        event="hub_status",
        progress=YadrenoAdminProgressEvent(
            event="status",
            content=text,
            slot="hub_status",
        ),
        resume_allowed=hub_status.resume_allowed,
    )


async def fetch_dialog_status(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
    request_id: Optional[int] = None,
) -> Optional[YadrenoAdminHubStatus]:
    """Reads hub /status and applies secure local cleanup solutions."""
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
    if hub_status.status == "idle":
        _clear_active_request(telegram_id, topic_id)
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
        if hub_status is not None and hub_status.status == "idle":
            _clear_last_request(telegram_id, topic_id)
            return None
        if hub_status is not None and not hub_status.resume_allowed:
            return _latest_from_hub_status(request_id, hub_status)
        return YadrenoAdminLatest(
            request_id=request_id,
            resume_allowed=hub_status.resume_allowed if hub_status else True,
        )
    latest = _latest_from_event(request_id, event)
    if hub_status is not None and hub_status.status == "idle" and latest.final is None:
        _clear_last_request(telegram_id, topic_id)
        return None
    if hub_status is not None:
        latest.resume_allowed = hub_status.resume_allowed
    if latest.final is not None:
        _clear_active_request(telegram_id, topic_id)
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
        await _ensure_broadcast_hub_support(session, api_key, effective_skill_id)
        payload: dict[str, Any] = {
            "topic_id": topic_id,
            "skill_id": effective_skill_id,
        }
        if effective_skill_id == YADRENO_ADMIN_BROADCAST_SKILL_ID:
            payload["capabilities"] = _capabilities_for_skill(effective_skill_id)
        _, data = await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/new_chat",
            json_payload=payload,
        )
    if not data:
        raise YadrenoAdminError("Хаб вернул пустой ответ на /new_chat")

    _validate_broadcast_hub_response(data, effective_skill_id)
    result = YadrenoAdminNewChatResult(
        status=str(data.get("status") or ""),
        response_text=str(data.get("response_text") or ""),
        closed_session_id=data.get("closed_session_id"),
    )
    if result.status == "ok":
        _clear_active_request(telegram_id, topic_id)
        _clear_last_request(telegram_id, topic_id)
    return result


async def cancel_active_dialog(
    telegram_id: int,
    api_key: str,
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> YadrenoAdminCancelResult:
    """Cancels an active admin request, if any."""
    request_id = get_active_request_id(telegram_id, topic_id)
    if request_id is None:
        return YadrenoAdminCancelResult(
            status="idle",
            response_text="Активного запроса нет.",
        )

    local_diag = get_local_tool_diagnostics(request_id, topic_id)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        _, data = await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/cancel",
            json_payload={
                "request_id": request_id,
                "topic_id": topic_id,
                **local_diag,
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
    if hub_status.status in {"orphan_cleared", "idle"}:
        _clear_active_request(telegram_id, topic_id)
        _clear_last_request(telegram_id, topic_id)
    return YadrenoAdminCancelResult(
        status=hub_status.status,
        response_text=hub_status.response_text,
        request_id=request_id,
        retry_after_sec=hub_status.retry_after_sec,
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
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=_format_recovered_final(latest.final.content),
                        parse_mode="HTML",
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
        await bot.send_message(
            chat_id=telegram_id,
            text=_format_recovered_final(final.content),
            parse_mode="HTML",
            reply_markup=_recovered_final_keyboard(topic_id, final.viewer_url),
        )
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
    for item in list_yadreno_admin_active_requests():
        asyncio.create_task(
            _recover_one_active_dialog_on_startup(
                bot,
                api_key,
                telegram_id=int(item["telegram_id"]),
                topic_id=int(item["topic_id"]),
                request_id=int(item["request_id"]),
            )
        )
