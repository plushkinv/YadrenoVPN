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
    get_yadreno_admin_server_ip,
    set_yadreno_admin_server_ip,
)

logger = logging.getLogger(__name__)

HUB_URL = "https://admin.yadreno.ru"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TMP_DIR = PROJECT_ROOT / "tmp"
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


class YadrenoAdminError(RuntimeError):
    """Ошибка общения с хабом Yadreno Admin."""


class DangerousShellCommandError(ValueError):
    """Команда отклонена локальным deny-list."""


@dataclass
class YadrenoAdminFinal:
    """Финальный ответ агента."""

    content: str
    viewer_url: Optional[str] = None


@dataclass
class YadrenoAdminProgressEvent:
    """Промежуточное пользовательское событие от хаба."""

    event: str
    content: str
    slot: str = ""


ProgressCallback = Callable[[YadrenoAdminProgressEvent], Awaitable[None]]

_request_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_active_requests: dict[int, int] = {}


def get_active_request_id(telegram_id: int) -> Optional[int]:
    """Возвращает активный request_id администратора, если он есть."""
    return _active_requests.get(telegram_id)


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


async def _execute_shell(args: dict[str, Any]) -> dict[str, Optional[str]]:
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


async def _run_script(args: dict[str, Any]) -> dict[str, Optional[str]]:
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


async def _execute_sqlite(args: dict[str, Any]) -> dict[str, Optional[str]]:
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


async def _execute_sql_cli(args: dict[str, Any], binary_name: str, command_args: list[str]) -> dict[str, Optional[str]]:
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


async def _execute_sql(args: dict[str, Any]) -> dict[str, Optional[str]]:
    """Исполняет satellite_sql для sqlite/mysql/postgres."""
    db_type = str(args.get("db_type", "")).strip().lower()
    db_name = str(args.get("db_name", "")).strip()

    if db_type == "sqlite":
        return await _execute_sqlite(args)
    if db_type == "mysql":
        command_args = ["--batch", "--raw"]
        if db_name:
            command_args.append(db_name)
        return await _execute_sql_cli(args, "mysql", command_args)
    if db_type in {"postgres", "postgresql"}:
        command_args = ["--tuples-only", "--no-align"]
        if db_name:
            command_args.extend(["--dbname", db_name])
        return await _execute_sql_cli(args, "psql", command_args)
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


async def _run_tool_call(event: dict[str, Any]) -> dict[str, Optional[str]]:
    """Исполняет один tool_call хаба."""
    tool = event.get("tool")
    if tool == "satellite_execute":
        result = await _execute_shell(event.get("args") or {})
    elif tool == "satellite_write_file":
        result = await _write_file(event.get("args") or {})
    elif tool == "satellite_run_script":
        result = await _run_script(event.get("args") or {})
    elif tool == "satellite_sql":
        result = await _execute_sql(event.get("args") or {})
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


async def run_dialog(
    telegram_id: int,
    api_key: str,
    message: str,
    *,
    topic_id: int = 0,
    progress_callback: Optional[ProgressCallback] = None,
) -> YadrenoAdminFinal:
    """
    Выполняет полный цикл диалога с агентом Yadreno Admin.

    Один администратор одновременно ведёт только один запрос: это защищает от
    гонок и соответствует ограничению topic_id на стороне хаба.
    """
    async with _request_locks[telegram_id]:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            server_ip = await _get_server_ip(session)
            _, process_data = await _request_json(
                session,
                api_key,
                "POST",
                "/api/v1/satellite/process",
                json_payload={
                    "message": message,
                    "server_ip": server_ip,
                    "topic_id": topic_id,
                    "capabilities": list(SATELLITE_CAPABILITIES),
                },
            )
            if not process_data:
                raise YadrenoAdminError("Хаб вернул пустой ответ на /process")

            status = process_data.get("status")
            if status != "accepted":
                response_text = process_data.get("response_text") or f"Запрос отклонён: {status}"
                raise YadrenoAdminError(response_text)

            request_id = int(process_data["request_id"])
            _active_requests[telegram_id] = request_id
            logger.info(
                "Yadreno Admin request accepted: admin=%s request_id=%s satellite_type=%s server_ip=%s",
                telegram_id,
                request_id,
                process_data.get("satellite_type"),
                server_ip,
            )
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
                        logger.info(
                            "Yadreno Admin tool_call: admin=%s request_id=%s tool_call_id=%s tool=%s",
                            telegram_id,
                            request_id,
                            event.get("tool_call_id"),
                            event.get("tool"),
                        )
                        tool_result = await _run_tool_call(event)
                        await _request_json(
                            session,
                            api_key,
                            "POST",
                            "/api/v1/satellite/tool_result",
                            json_payload={
                                "request_id": request_id,
                                "tool_call_id": event["tool_call_id"],
                                **tool_result,
                            },
                        )
                        continue

                    event_type = event.get("event")
                    if event_type == "status":
                        await _notify_progress(event, progress_callback)
                        continue

                    if event_type == "task_update":
                        await _notify_progress(event, progress_callback)
                        continue

                    if event_type == "final":
                        return YadrenoAdminFinal(
                            content=event.get("content") or "",
                            viewer_url=event.get("viewer_url"),
                        )

                    raise YadrenoAdminError(f"Неизвестное событие хаба: {event}")
            finally:
                _active_requests.pop(telegram_id, None)


async def cancel_active_dialog(telegram_id: int, api_key: str) -> bool:
    """Отменяет активный запрос администратора, если он есть."""
    request_id = _active_requests.get(telegram_id)
    if request_id is None:
        return False

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _request_json(
            session,
            api_key,
            "POST",
            "/api/v1/satellite/cancel",
            json_payload={"request_id": request_id},
        )
    logger.info("Yadreno Admin request cancelled: admin=%s request_id=%s", telegram_id, request_id)
    return True
