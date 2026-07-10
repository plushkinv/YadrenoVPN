"""
Клиент сателлит-протокола Yadreno Admin.

Сервис берёт на себя полный цикл запроса:
process → poll → tool_result → final, а также локальное исполнение tool_call.
"""
from __future__ import annotations

import asyncio
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
YADRENO_ADMIN_DEFAULT_SKILL_ID = "yadreno_vpn"
YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID = "yadreno_vpn_customization"
PROGRESS_EVENTS_CAPABILITY = "progress_events"
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
_core_mutation_shell_patterns: tuple[tuple[str, str], ...] = (
    (r"(^|[;&|]\s*)(sudo\s+)?(rm|mv|cp|install|touch|mkdir|rmdir)\b", "filesystem mutation"),
    (r"(^|[;&|]\s*)(sudo\s+)?(chmod|chown|chgrp)\b", "permission mutation"),
    (r"(^|[;&|]\s*)(sudo\s+)?(sed\s+-i|perl\s+-pi)\b", "in-place file edit"),
    (r"(^|[;&|]\s*)(sudo\s+)?(apt|apt-get|pip|npm|yarn|pnpm)\b", "package mutation"),
    (r"(^|[;&|]\s*)(sudo\s+)?systemctl\s+(start|stop|restart|reload|enable|disable)\b", "service mutation"),
    (r"(^|[;&|]\s*)git\s+(checkout|reset|clean|pull|merge|apply|am)\b", "git worktree mutation"),
    (r"(?:^|\s)(>|>>)\s*[^&]", "shell redirection write"),
    (r"(^|[;&|]\s*)tee\s+", "tee write"),
    (r"\.write_text\s*\(|\.write_bytes\s*\(|\bopen\s*\([^)]*,\s*['\"][wax]", "python file write"),
)


def is_yadreno_admin_customization_topic(topic_id: int) -> bool:
    """Return True for Yadreno Admin lanes that use the customization skill."""
    return int(topic_id) in {
        YADRENO_ADMIN_YAA_TOPIC_ID,
        YADRENO_ADMIN_CUSTOMIZATION_TOPIC_ID,
    }


def yadreno_admin_skill_id_for_topic(
    topic_id: int,
    requested_skill_id: Optional[str] = None,
) -> str:
    """Resolve the skill id sent to the hub for a local Yadreno Admin lane."""
    requested = (requested_skill_id or "").strip()
    if requested in {YADRENO_ADMIN_DEFAULT_SKILL_ID, YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID}:
        return requested
    if is_yadreno_admin_customization_topic(topic_id):
        return YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID
    return YADRENO_ADMIN_DEFAULT_SKILL_ID


def _core_policy_for_skill(skill_id: str) -> Optional[bool]:
    """Return the core-change policy payload for customization skill calls."""
    if skill_id != YADRENO_ADMIN_CUSTOMIZATION_SKILL_ID:
        return None
    return is_yadreno_admin_core_changes_enabled()


class YadrenoAdminError(RuntimeError):
    """Ошибка общения с хабом Yadreno Admin."""


class DangerousShellCommandError(ValueError):
    """Команда отклонена локальным deny-list."""


@dataclass
class YadrenoAdminFinal:
    """Финальный ответ агента."""

    content: str
    viewer_url: Optional[str] = None
    request_id: Optional[int] = None


@dataclass
class YadrenoAdminProgressEvent:
    """Промежуточное пользовательское событие от хаба."""

    event: str
    content: str
    slot: str = ""


@dataclass
class YadrenoAdminUpload:
    """Файл для отправки в Yadreno Admin upload API."""

    path: Path
    filename: str
    content_type: str = "application/octet-stream"


@dataclass
class YadrenoAdminLatest:
    """Последний non-destructive snapshot запроса."""

    request_id: int
    event: str = ""
    final: Optional[YadrenoAdminFinal] = None
    progress: Optional[YadrenoAdminProgressEvent] = None
    resume_allowed: bool = True


@dataclass
class YadrenoAdminNewChatResult:
    """Результат запроса нового чата на хабе."""

    status: str
    response_text: str = ""
    closed_session_id: Optional[int] = None


@dataclass
class YadrenoAdminHubStatus:
    """Состояние request/lane по данным hub /status."""

    status: str
    response_text: str = ""
    request_id: Optional[int] = None
    retry_after_sec: Optional[float] = None
    local_tool_running: bool = False
    local_tool_call_id: Optional[str] = None
    resume_allowed: bool = False


@dataclass
class YadrenoAdminCancelResult:
    """Результат умной отмены request/lane."""

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
    """Возвращает активный request_id администратора, если он есть."""
    key = _lane_key(telegram_id, topic_id)
    return _active_requests.get(key) or get_yadreno_admin_active_request_id(
        telegram_id,
        topic_id,
    )


def get_last_request_id(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> Optional[int]:
    """Возвращает последний request_id для ручного восстановления."""
    key = _lane_key(telegram_id, topic_id)
    return _last_requests.get(key) or get_yadreno_admin_last_request_id(
        telegram_id,
        topic_id,
    )


def is_local_request_active(
    telegram_id: int,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> bool:
    """Проверяет, ведёт ли текущий процесс polling для lane."""
    return _lane_key(telegram_id, topic_id) in _active_requests


def _remember_request(
    telegram_id: int,
    topic_id: int,
    request_id: int,
    *,
    active: bool,
) -> None:
    """Сохраняет request_id в памяти и settings для восстановления после рестарта."""
    key = _lane_key(telegram_id, topic_id)
    _last_requests[key] = request_id
    set_yadreno_admin_last_request_id(telegram_id, topic_id, request_id)
    if active:
        _active_requests[key] = request_id
        set_yadreno_admin_active_request_id(telegram_id, topic_id, request_id)


def _clear_active_request(telegram_id: int, topic_id: int) -> None:
    """Очищает active request_id в памяти и settings."""
    _active_requests.pop(_lane_key(telegram_id, topic_id), None)
    clear_yadreno_admin_active_request_id(telegram_id, topic_id)


def _clear_last_request(telegram_id: int, topic_id: int) -> None:
    """Очищает last request_id в памяти и settings."""
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
    """Сохраняет runtime-состояние локально выполняющегося tool_call."""
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
    """Удаляет runtime-состояние завершённого локального tool_call."""
    _running_tool_calls.pop((int(request_id), str(tool_call_id)), None)
    clear_yadreno_admin_tool_runtime(request_id, tool_call_id)


def _pid_is_running(pid: Any) -> bool:
    """Best-effort проверка живости локального subprocess по pid."""
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
    """Dev-only предохранитель: на Windows os.kill(pid, 0) шлёт CTRL+C."""
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
    """Возвращает локальную диагностику running tool_call для hub /cancel."""
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
    """Отклоняет катастрофически опасные shell-команды перед subprocess."""
    for pattern, reason in _dangerous_shell_patterns:
        if re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE):
            raise DangerousShellCommandError(
                f"dangerous shell command rejected: {reason}"
            )


def _resolve_tool_path(raw_path: str) -> Path:
    """Преобразует путь tool_call в абсолютный путь локального сервера."""
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


def _is_allowed_customization_write_path(path: Path) -> bool:
    return _is_inside(path, PROJECT_ROOT / "custom_extensions")


def _core_guard_reject_shell(command: str) -> Optional[str]:
    for pattern, reason in _core_mutation_shell_patterns:
        if re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE):
            return f"core changes are disabled; rejected shell mutation: {reason}"
    return None


def _normalized_sql(query: str) -> str:
    return " ".join(query.strip().rstrip(";").split())


def _is_readonly_sql(query: str) -> bool:
    normalized = _normalized_sql(query).lower()
    return normalized.startswith(("select ", "pragma ", "with ", "explain "))


def _is_allowed_page_custom_sql(query: str) -> bool:
    normalized = _normalized_sql(query)
    lowered = normalized.lower()
    if not lowered.startswith("update pages set "):
        return False
    if " where " not in lowered:
        return False
    set_part = normalized[normalized.lower().find(" set ") + 5:normalized.lower().find(" where ")]
    columns: list[str] = []
    for part in set_part.split(","):
        column = part.split("=", 1)[0].strip().strip('"`[]').lower()
        if column:
            columns.append(column)
    allowed = {
        "text_custom",
        "image_custom",
        "media_type_custom",
        "buttons_custom",
        "updated_at",
    }
    return bool(columns) and all(column in allowed for column in columns)


def _is_allowed_settings_sql(query: str) -> bool:
    normalized = _normalized_sql(query).lower()
    if normalized.startswith("update settings set ") and " where " in normalized:
        return True
    if normalized.startswith("insert or replace into settings"):
        return True
    return False


def _core_guard_reject_sql(query: str) -> Optional[str]:
    if ";" in query.strip().rstrip(";"):
        return "core changes are disabled; multiple SQL statements are rejected"
    if _is_readonly_sql(query):
        return None
    if _is_allowed_page_custom_sql(query) or _is_allowed_settings_sql(query):
        return None
    return "core changes are disabled; rejected direct core SQL mutation"


def _get_timeout(args: dict[str, Any], default: int = 60) -> int:
    """Читает timeout из аргументов tool_call с безопасным дефолтом."""
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
    """Best-effort определяет публичный IP сервера через внешние сервисы."""
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
    """Определяет публичный IP сервера без обращения к config.py."""
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await _detect_public_server_ip_with_session(
            session,
            use_cache=use_cache,
        )


async def _get_server_ip(session: aiohttp.ClientSession) -> str:
    """Возвращает публичный IP сателлита: settings → autodetect → ''."""
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
    Делает HTTP-запрос к хабу с retry и возвращает статус + JSON.

    204 обрабатывается отдельно, потому что у long-polling это штатный ответ.
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
                        f"Хаб вернул HTTP {response.status}: {body[:500]}"
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

    raise YadrenoAdminError(f"Не удалось связаться с хабом Yadreno Admin: {last_error}")


async def _request_multipart(
    session: aiohttp.ClientSession,
    api_key: str,
    path: str,
    *,
    fields: dict[str, Any],
    uploads: list[YadrenoAdminUpload],
    file_field: str,
) -> tuple[int, Optional[dict]]:
    """Делает multipart-запрос к upload API хаба с retry."""
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
                        f"Хаб вернул HTTP {response.status}: {body[:500]}"
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

    raise YadrenoAdminError(f"Не удалось загрузить файл в Yadreno Admin: {last_error}")


async def _execute_shell(
    args: dict[str, Any],
    runtime: Optional[_ToolRuntimeContext] = None,
) -> dict[str, Optional[str]]:
    """Исполняет satellite_execute на сервере, где запущен бот."""
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
    """Исполняет satellite_write_file: пишет content в явно переданный path."""
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
    """Исполняет satellite_run_script через временный .sh в tmp/."""
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
    """Форматирует табличный SQL-ответ в компактный текст."""
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
    """Исполняет sqlite-запрос по db_path/db_name."""
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
    Исполняет SQL через локальный CLI.

    Учётные данные намеренно не хранятся в коде: mysql/psql сами используют
    окружение и локальные конфиги пользователя процесса.
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
    """Исполняет satellite_sql для sqlite/mysql/postgres."""
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
    """Пишет audit log по локально исполненному tool_call."""
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


def _core_guard_reject_tool(
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
            return "core changes are disabled; empty write path rejected"
        try:
            path = _resolve_tool_path(raw_path)
        except Exception as e:
            return f"core changes are disabled; invalid write path: {e}"
        if not _is_allowed_customization_write_path(path):
            return (
                "core changes are disabled; file writes are allowed only under "
                "custom_extensions/"
            )
        return None
    if tool == "satellite_execute":
        return _core_guard_reject_shell(str(args.get("command", "")).strip())
    if tool == "satellite_run_script":
        script = str(args.get("script_body", "")).strip()
        return (
            _core_guard_reject_shell(script)
            or "core changes are disabled; run_script is blocked in customization mode"
        )
    if tool == "satellite_sql":
        return _core_guard_reject_sql(str(args.get("query", "")).strip())
    return None


async def _run_tool_call(
    event: dict[str, Any],
    *,
    topic_id: int = YADRENO_ADMIN_CHAT_TOPIC_ID,
) -> dict[str, Optional[str]]:
    """Исполняет один tool_call хаба."""
    tool = event.get("tool")
    args = event.get("args") or {}
    runtime = _runtime_context_from_event(event, topic_id=topic_id)
    _remember_tool_runtime(runtime)
    guard_error = _core_guard_reject_tool(str(tool or ""), args, topic_id=topic_id)
    if guard_error:
        result = {"result": "", "error": guard_error}
    elif tool == "satellite_execute":
        result = await _execute_shell(args, runtime=runtime)
    elif tool == "satellite_write_file":
        result = await _write_file(args)
    elif tool == "satellite_run_script":
        result = await _run_script(args, runtime=runtime)
    elif tool == "satellite_sql":
        result = await _execute_sql(args, runtime=runtime)
    else:
        result = {"result": "", "error": f"unknown tool {tool}"}

    _log_tool_audit(event, result)
    return result


async def _notify_progress(
    event: dict[str, Any],
    progress_callback: Optional[ProgressCallback],
) -> None:
    """Передаёт status/task_update в UI-слой и не роняет агентский цикл."""
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
    """Единый poll/tool/final цикл для text и upload запросов."""
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
                    tool_result = {
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
                        tool_result = await _run_tool_call(event, topic_id=topic_id)
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
                    if tool_started_here:
                        clear_yadreno_admin_tool_call_started(request_id, tool_call_id)
                finally:
                    if tool_started_here:
                        _clear_tool_runtime(request_id, tool_call_id)
                continue

            event_type = event.get("event")
            if event_type in {"status", "task_update"}:
                await _notify_progress(event, progress_callback)
                continue

            if event_type == "final":
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
    Выполняет полный цикл диалога с агентом Yadreno Admin.

    Один администратор одновременно ведёт только один запрос в одном lane
    Yadreno Admin. Обычный чат и /yaa используют разные topic_id.
    """
    key = _lane_key(telegram_id, topic_id)
    async with _request_locks[key]:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            server_ip = await _get_server_ip(session)
            effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
            core_changes_allowed = _core_policy_for_skill(effective_skill_id)
            payload: dict[str, Any] = {
                "message": message,
                "server_ip": server_ip,
                "topic_id": topic_id,
                "skill_id": effective_skill_id,
                "capabilities": list(SATELLITE_CAPABILITIES),
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
    """Отправляет файлы в Yadreno Admin и ждёт финальный ответ агента."""
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
            server_ip = await _get_server_ip(session)
            effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
            core_changes_allowed = _core_policy_for_skill(effective_skill_id)
            is_batch = len(uploads) > 1
            path = (
                "/api/v1/satellite/upload_batch"
                if is_batch
                else "/api/v1/satellite/upload"
            )
            fields: dict[str, str | int] = {
                "message": message,
                "topic_id": topic_id,
                "server_ip": server_ip,
                "skill_id": effective_skill_id,
                "capabilities": ",".join(SATELLITE_CAPABILITIES),
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
    """Восстанавливает polling активного запроса после рестарта локального бота."""
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
    """Преобразует snapshot хаба в локальную структуру без tool_call."""
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
    """Преобразует ответ hub /status в локальную структуру."""
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
    """Показывает read-only /status как progress-событие без запуска /poll."""
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
    """Читает hub /status и применяет безопасные локальные cleanup-решения."""
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
    """Читает последний snapshot через /latest, не consume-ит /poll."""
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
    """Просит хаб закрыть активную satellite-сессию, если lane свободна."""
    timeout = aiohttp.ClientTimeout(total=20)
    effective_skill_id = yadreno_admin_skill_id_for_topic(topic_id, skill_id)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        _, data = await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/new_chat",
            json_payload={"topic_id": topic_id, "skill_id": effective_skill_id},
        )
    if not data:
        raise YadrenoAdminError("Хаб вернул пустой ответ на /new_chat")

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
    """Отменяет активный запрос администратора, если он есть."""
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


def _format_recovered_final(content: str, viewer_url: Optional[str]) -> str:
    """Форматирует финал, доставленный фоновым startup recovery."""
    response = content or "Готово."
    if viewer_url:
        from bot.utils.text import escape_html

        response += f'\n\n<a href="{escape_html(viewer_url)}">Полная версия ответа</a>'
    return response


async def _recover_one_active_dialog_on_startup(
    bot: Any,
    api_key: str,
    *,
    telegram_id: int,
    topic_id: int,
    request_id: int,
) -> None:
    """Проверяет один active request после рестарта и продолжает только live-задачи."""
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
                        text=_format_recovered_final(
                            latest.final.content,
                            latest.final.viewer_url,
                        ),
                        parse_mode="HTML",
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
            text=_format_recovered_final(final.content, final.viewer_url),
            parse_mode="HTML",
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
    """Запускает best-effort восстановление live Yadreno Admin задач после рестарта."""
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
