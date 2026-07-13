"""Git-backed protection for Yadreno Admin customization tool calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Optional

from database.requests import delete_setting, get_setting, set_setting

logger = logging.getLogger(__name__)

CORE_GUARD_JOURNAL_SETTING = "yadreno_admin_core_guard_journal"
CORE_GUARD_REF_PREFIX = "refs/yaa-snapshots"
CORE_GUARD_TMP_DIRNAME = "yadreno_core_guard"
MAX_DIAGNOSTIC_CHARS = 4000
MAX_REPORTED_PATHS = 20
GIT_TIMEOUT_SECONDS = 30
DEFAULT_REPOSITORY = Path(__file__).resolve().parents[2]

ToolExecutor = Callable[[], Awaitable[dict[str, Any]]]
JournalOperation = Callable[[], Any]

_repository_lock = asyncio.Lock()
_repository_guard_active = 0


@dataclass(frozen=True)
class GitCommandResult:
    """Raw result of one local Git command."""

    stdout: bytes
    stderr: bytes
    returncode: int


class CoreGuardError(RuntimeError):
    """Failure to prepare, verify or restore a protected Git worktree."""

    def __init__(
        self,
        *,
        stage: str,
        repository: Path,
        command: list[str],
        returncode: Optional[int],
        stderr: str,
        stdout: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(stderr or stdout or stage)
        self.stage = stage
        self.repository = repository
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.retryable = retryable

    def agent_message(self, *, tool_executed: bool) -> str:
        """Return actionable diagnostics without hiding the failing Git step."""
        state = "was executed" if tool_executed else "was NOT executed"
        output = (self.stderr or self.stdout or "no diagnostic output").strip()
        output = output[:MAX_DIAGNOSTIC_CHARS]
        command = " ".join(self.command) if self.command else "internal guard operation"
        return (
            f"Core Git protection failed; the tool {state}.\n"
            f"stage: {self.stage}\n"
            f"repository: {self.repository}\n"
            f"command: {command}\n"
            f"exit_code: {self.returncode if self.returncode is not None else 'not started'}\n"
            f"retryable: {'true' if self.retryable else 'false'}\n"
            f"diagnostic:\n{output}"
        )


@dataclass(frozen=True)
class GuardTransaction:
    """Prepared checkpoint for one protected tool call."""

    key: str
    request_id: int
    tool_call_id: str
    topic_id: int
    repository: Path
    baseline_head: str
    baseline_oid: str
    baseline_tree: str
    snapshot_ref: Optional[str]
    dirty: bool


@dataclass(frozen=True)
class GuardReport:
    """Post-execution verification result."""

    changed_paths: tuple[str, ...] = ()

    @property
    def rolled_back(self) -> bool:
        return bool(self.changed_paths)


def is_repository_guard_active() -> bool:
    """Return whether a protected tool currently owns the project worktree."""
    return _repository_guard_active > 0


def _transaction_key(request_id: int, tool_call_id: str) -> str:
    return f"{int(request_id)}:{tool_call_id}"


def _ref_name(request_id: int, tool_call_id: str) -> str:
    digest = hashlib.sha256(tool_call_id.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"{CORE_GUARD_REF_PREFIX}/{int(request_id)}/{digest}"


def _load_journal() -> dict[str, dict[str, Any]]:
    raw = get_setting(CORE_GUARD_JOURNAL_SETTING, "") or ""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Yadreno Admin core guard journal JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid Yadreno Admin core guard journal root")
    transactions = payload.get("transactions", {})
    if not isinstance(transactions, dict):
        raise ValueError("invalid Yadreno Admin core guard transactions map")
    return transactions


def _save_journal(transactions: dict[str, dict[str, Any]]) -> None:
    if not transactions:
        delete_setting(CORE_GUARD_JOURNAL_SETTING)
        return
    payload = {"version": 1, "transactions": transactions}
    set_setting(
        CORE_GUARD_JOURNAL_SETTING,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _set_journal_entry(key: str, entry: dict[str, Any]) -> None:
    transactions = _load_journal()
    transactions[key] = entry
    _save_journal(transactions)


def _update_journal_entry(key: str, **updates: Any) -> None:
    transactions = _load_journal()
    entry = transactions.get(key)
    if not isinstance(entry, dict):
        return
    entry.update(updates)
    transactions[key] = entry
    _save_journal(transactions)


def _delete_journal_entry(key: str) -> None:
    transactions = _load_journal()
    transactions.pop(key, None)
    _save_journal(transactions)


def _journal_operation(
    repository: Path,
    stage: str,
    operation: JournalOperation,
) -> Any:
    try:
        return operation()
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        raise CoreGuardError(
            stage=stage,
            repository=repository,
            command=[],
            returncode=None,
            stderr=f"durable guard journal operation failed: {exc}",
            retryable=isinstance(exc, (sqlite3.OperationalError, OSError)),
        ) from exc


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _is_retryable_git_error(output: str) -> bool:
    lowered = output.lower()
    return any(
        marker in lowered
        for marker in ("could not lock", "cannot lock", "index.lock", "resource temporarily unavailable")
    )


async def _run_git(
    repository: Path,
    args: list[str],
    *,
    stage: str,
    env_overrides: Optional[dict[str, str]] = None,
    timeout: int = GIT_TIMEOUT_SECONDS,
) -> GitCommandResult:
    git_binary = shutil.which("git")
    if not git_binary:
        raise CoreGuardError(
            stage=stage,
            repository=repository,
            command=["git", *args],
            returncode=None,
            stderr="git executable was not found in PATH",
            retryable=False,
        )

    env = os.environ.copy()
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(variable, None)
    if env_overrides:
        env.update(env_overrides)

    last_result: Optional[GitCommandResult] = None
    for attempt in range(2):
        try:
            process = await asyncio.create_subprocess_exec(
                git_binary,
                *args,
                cwd=str(repository),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise CoreGuardError(
                stage=stage,
                repository=repository,
                command=["git", *args],
                returncode=None,
                stderr=f"git command timed out after {timeout}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise CoreGuardError(
                stage=stage,
                repository=repository,
                command=["git", *args],
                returncode=None,
                stderr=str(exc),
                retryable=False,
            ) from exc

        last_result = GitCommandResult(stdout=stdout, stderr=stderr, returncode=process.returncode)
        if process.returncode == 0:
            return last_result

        diagnostic = _decode(stderr) or _decode(stdout)
        if attempt == 0 and _is_retryable_git_error(diagnostic):
            await asyncio.sleep(0.2)
            continue
        raise CoreGuardError(
            stage=stage,
            repository=repository,
            command=["git", *args],
            returncode=process.returncode,
            stderr=_decode(stderr),
            stdout=_decode(stdout),
            retryable=_is_retryable_git_error(diagnostic),
        )

    assert last_result is not None
    return last_result


def _temp_dir(repository: Path, *, stage: str) -> Path:
    path = repository / "tmp" / CORE_GUARD_TMP_DIRNAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CoreGuardError(
            stage=stage,
            repository=repository,
            command=[],
            returncode=None,
            stderr=f"failed to prepare core guard temp directory {path}: {exc}",
            retryable=True,
        ) from exc
    return path


def _remove_temp_index(index_path: Path) -> None:
    for path in (index_path, Path(f"{index_path}.lock")):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove core guard temp index %s: %s", path, exc)


async def _build_worktree_tree(
    repository: Path,
    *,
    baseline_head: str,
    token: str,
    stage_prefix: str,
) -> str:
    index_path = _temp_dir(repository, stage=f"{stage_prefix}.temp_dir") / f"{token}.index"
    _remove_temp_index(index_path)
    env = {"GIT_INDEX_FILE": str(index_path)}
    try:
        await _run_git(
            repository,
            ["read-tree", baseline_head],
            stage=f"{stage_prefix}.read_tree",
            env_overrides=env,
        )
        await _run_git(
            repository,
            ["add", "-A", "--", "."],
            stage=f"{stage_prefix}.add_worktree",
            env_overrides=env,
        )
        result = await _run_git(
            repository,
            ["write-tree"],
            stage=f"{stage_prefix}.write_tree",
            env_overrides=env,
        )
        return _decode(result.stdout)
    finally:
        _remove_temp_index(index_path)


async def _tree_for_object(repository: Path, oid: str, *, stage: str) -> str:
    result = await _run_git(
        repository,
        ["rev-parse", f"{oid}^{{tree}}"],
        stage=stage,
    )
    return _decode(result.stdout)


async def _prepare_transaction(
    repository: Path,
    *,
    request_id: int,
    tool_call_id: str,
    topic_id: int,
) -> GuardTransaction:
    repository = repository.resolve()
    key = _transaction_key(request_id, tool_call_id)
    token = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:24]

    head_result = await _run_git(
        repository,
        ["rev-parse", "--verify", "HEAD"],
        stage="prepare.resolve_head",
    )
    baseline_head = _decode(head_result.stdout)
    status_result = await _run_git(
        repository,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        stage="prepare.status",
    )
    dirty = bool(status_result.stdout)
    entry: dict[str, Any] = {
        "request_id": int(request_id),
        "tool_call_id": tool_call_id,
        "topic_id": int(topic_id),
        "repository": str(repository),
        "baseline_head": baseline_head,
        "baseline_oid": baseline_head,
        "baseline_tree": "",
        "snapshot_ref": None,
        "dirty": dirty,
        "phase": "preparing",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _journal_operation(
        repository,
        "prepare.write_journal",
        lambda: _set_journal_entry(key, entry),
    )

    baseline_oid = baseline_head
    snapshot_ref: Optional[str] = None
    if dirty:
        baseline_tree = await _build_worktree_tree(
            repository,
            baseline_head=baseline_head,
            token=f"{token}-baseline",
            stage_prefix="prepare.snapshot",
        )
        commit_env = {
            "GIT_AUTHOR_NAME": "Yadreno Admin Core Guard",
            "GIT_AUTHOR_EMAIL": "core-guard@yadreno.invalid",
            "GIT_COMMITTER_NAME": "Yadreno Admin Core Guard",
            "GIT_COMMITTER_EMAIL": "core-guard@yadreno.invalid",
        }
        commit_result = await _run_git(
            repository,
            [
                "commit-tree",
                baseline_tree,
                "-p",
                baseline_head,
                "-m",
                f"Yadreno Admin checkpoint {request_id}/{token}",
            ],
            stage="prepare.commit_tree",
            env_overrides=commit_env,
        )
        baseline_oid = _decode(commit_result.stdout)
        snapshot_ref = _ref_name(request_id, tool_call_id)
        _journal_operation(
            repository,
            "prepare.write_checkpoint_journal",
            lambda: _update_journal_entry(
                key,
                baseline_oid=baseline_oid,
                baseline_tree=baseline_tree,
                snapshot_ref=snapshot_ref,
                phase="checkpointing",
            ),
        )
        await _run_git(
            repository,
            ["update-ref", snapshot_ref, baseline_oid],
            stage="prepare.update_ref",
        )
    else:
        baseline_tree = await _tree_for_object(
            repository,
            baseline_oid,
            stage="prepare.clean_tree",
        )

    _journal_operation(
        repository,
        "prepare.arm_recovery_journal",
        lambda: _update_journal_entry(
            key,
            baseline_oid=baseline_oid,
            baseline_tree=baseline_tree,
            snapshot_ref=snapshot_ref,
            phase="executing",
        ),
    )
    return GuardTransaction(
        key=key,
        request_id=int(request_id),
        tool_call_id=tool_call_id,
        topic_id=int(topic_id),
        repository=repository,
        baseline_head=baseline_head,
        baseline_oid=baseline_oid,
        baseline_tree=baseline_tree,
        snapshot_ref=snapshot_ref,
        dirty=dirty,
    )


async def _nul_paths(
    repository: Path,
    args: list[str],
    *,
    stage: str,
) -> set[bytes]:
    result = await _run_git(repository, args, stage=stage)
    return {item for item in result.stdout.split(b"\0") if item}


def _validate_git_path(raw_path: bytes, repository: Path) -> Path:
    decoded = os.fsdecode(raw_path)
    relative = PurePosixPath(decoded)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise CoreGuardError(
            stage="rollback.validate_path",
            repository=repository,
            command=[],
            returncode=None,
            stderr=f"unsafe Git path returned by repository: {decoded!r}",
        )
    if relative.parts[0] == ".git":
        raise CoreGuardError(
            stage="rollback.validate_path",
            repository=repository,
            command=[],
            returncode=None,
            stderr="refusing to restore a path inside .git",
        )
    return repository.joinpath(*relative.parts)


def _remove_extra_paths(repository: Path, paths: set[bytes]) -> None:
    candidates = sorted(paths, key=lambda value: value.count(b"/"), reverse=True)
    parents: set[Path] = set()
    for raw_path in candidates:
        path = _validate_git_path(raw_path, repository)
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                path.rmdir()
        except OSError as exc:
            raise CoreGuardError(
                stage="rollback.remove_extra",
                repository=repository,
                command=[],
                returncode=None,
                stderr=f"failed to remove {path}: {exc}",
            ) from exc
        parents.add(path.parent)

    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        while parent != repository and repository in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


async def _restore_baseline_paths(
    transaction: GuardTransaction,
    paths: set[bytes],
    *,
    attempt: int,
) -> None:
    if not paths:
        return
    token = hashlib.sha256(transaction.key.encode("utf-8", errors="replace")).hexdigest()[:24]
    pathspec_file = _temp_dir(
        transaction.repository,
        stage="rollback.pathspec_temp_dir",
    ) / f"{token}-{attempt}.paths"
    try:
        try:
            pathspec_file.write_bytes(b"\0".join(sorted(paths)) + b"\0")
        except OSError as exc:
            raise CoreGuardError(
                stage="rollback.write_pathspec",
                repository=transaction.repository,
                command=[],
                returncode=None,
                stderr=f"failed to write core guard pathspec {pathspec_file}: {exc}",
                retryable=True,
            ) from exc
        await _run_git(
            transaction.repository,
            [
                "restore",
                f"--source={transaction.baseline_oid}",
                "--worktree",
                f"--pathspec-from-file={pathspec_file}",
                "--pathspec-file-nul",
            ],
            stage="rollback.restore_paths",
        )
    finally:
        try:
            pathspec_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove core guard pathspec %s: %s", pathspec_file, exc)


async def _verify_head(transaction: GuardTransaction, *, stage: str) -> None:
    result = await _run_git(
        transaction.repository,
        ["rev-parse", "--verify", "HEAD"],
        stage=stage,
    )
    current_head = _decode(result.stdout)
    if current_head != transaction.baseline_head:
        raise CoreGuardError(
            stage=stage,
            repository=transaction.repository,
            command=["git", "rev-parse", "--verify", "HEAD"],
            returncode=0,
            stderr=(
                "HEAD changed while the protected tool was running; automatic rollback was "
                "stopped to avoid overwriting a concurrent repository update"
            ),
        )


async def _reconcile(transaction: GuardTransaction) -> GuardReport:
    await _verify_head(transaction, stage="verify.head")
    reverted_paths: set[bytes] = set()
    token = hashlib.sha256(transaction.key.encode("utf-8", errors="replace")).hexdigest()[:24]

    for attempt in range(1, 4):
        current_tree = await _build_worktree_tree(
            transaction.repository,
            baseline_head=transaction.baseline_head,
            token=f"{token}-verify-{attempt}",
            stage_prefix="verify.worktree",
        )
        if current_tree == transaction.baseline_tree:
            decoded = tuple(sorted(os.fsdecode(path) for path in reverted_paths))
            return GuardReport(changed_paths=decoded)

        changed_paths = await _nul_paths(
            transaction.repository,
            [
                "diff-tree",
                "-r",
                "--no-commit-id",
                "--name-only",
                "-z",
                transaction.baseline_tree,
                current_tree,
            ],
            stage="rollback.diff_paths",
        )
        baseline_paths = await _nul_paths(
            transaction.repository,
            ["ls-tree", "-r", "--name-only", "-z", transaction.baseline_tree],
            stage="rollback.baseline_paths",
        )
        current_paths = await _nul_paths(
            transaction.repository,
            ["ls-tree", "-r", "--name-only", "-z", current_tree],
            stage="rollback.current_paths",
        )
        extra_paths = (changed_paths & current_paths) - baseline_paths
        restore_paths = changed_paths & baseline_paths
        ignore_paths = {
            path
            for path in changed_paths
            if path == b".gitignore" or path.endswith(b"/.gitignore")
        }
        if ignore_paths:
            extra_ignores = (ignore_paths & current_paths) - baseline_paths
            restore_ignores = ignore_paths & baseline_paths
            reverted_paths.update(ignore_paths)
            _remove_extra_paths(transaction.repository, extra_ignores)
            await _restore_baseline_paths(transaction, restore_ignores, attempt=attempt)
            continue

        reverted_paths.update(extra_paths)
        reverted_paths.update(restore_paths)
        _remove_extra_paths(transaction.repository, extra_paths)
        await _restore_baseline_paths(transaction, restore_paths, attempt=attempt)

    raise CoreGuardError(
        stage="rollback.verify_tree",
        repository=transaction.repository,
        command=["git", "write-tree"],
        returncode=None,
        stderr="worktree still differs from the checkpoint after three rollback passes",
    )


def _entry_to_transaction(key: str, entry: dict[str, Any]) -> GuardTransaction:
    return GuardTransaction(
        key=key,
        request_id=int(entry.get("request_id") or 0),
        tool_call_id=str(entry.get("tool_call_id") or ""),
        topic_id=int(entry.get("topic_id") or 0),
        repository=Path(str(entry.get("repository") or ".")).resolve(),
        baseline_head=str(entry.get("baseline_head") or ""),
        baseline_oid=str(entry.get("baseline_oid") or ""),
        baseline_tree=str(entry.get("baseline_tree") or ""),
        snapshot_ref=entry.get("snapshot_ref") or None,
        dirty=bool(entry.get("dirty")),
    )


def _append_rollback_notice(result: dict[str, Any], report: GuardReport) -> dict[str, Any]:
    if not report.rolled_back:
        return result
    paths = list(report.changed_paths)
    preview = ", ".join(paths[:MAX_REPORTED_PATHS])
    if len(paths) > MAX_REPORTED_PATHS:
        preview += f", ... (+{len(paths) - MAX_REPORTED_PATHS} more)"
    notice = (
        "Core protection detected Git-visible project changes and restored the exact "
        f"pre-tool state. Reverted paths ({len(paths)}): {preview}. Ignored files, local "
        "databases and system-level side effects were not reverted."
    )
    error = str(result.get("error") or "").strip()
    if error:
        result["error"] = f"{error}\n\n{notice}"
    else:
        output = str(result.get("result") or "").strip()
        result["result"] = f"{notice}\n\nTool output:\n{output}" if output else notice
    return result


async def _rollback_failure_message(
    exc: CoreGuardError,
    transaction: Optional[GuardTransaction],
    *,
    tool_executed: bool,
) -> str:
    message = exc.agent_message(tool_executed=tool_executed)
    if not tool_executed or transaction is None:
        return message

    checkpoint = transaction.snapshot_ref or f"HEAD {transaction.baseline_head}"
    try:
        status_result = await _run_git(
            transaction.repository,
            ["status", "--short", "--untracked-files=all"],
            stage="rollback.failure_status",
        )
        status = _decode(status_result.stdout) or "(clean relative to HEAD)"
    except CoreGuardError as status_error:
        status = f"unavailable: {status_error.agent_message(tool_executed=True)}"
    return (
        f"{message}\ncheckpoint_retained: {checkpoint}\n"
        f"current_git_status:\n{status[:MAX_DIAGNOSTIC_CHARS]}"
    )


async def run_with_core_guard(
    *,
    repository: Path,
    request_id: int,
    tool_call_id: str,
    topic_id: int,
    executor: ToolExecutor,
) -> dict[str, Any]:
    """Execute a tool freely, then restore any Git-visible project changes."""
    global _repository_guard_active

    key = _transaction_key(request_id, tool_call_id)
    async with _repository_lock:
        _repository_guard_active += 1
        transaction: Optional[GuardTransaction] = None
        tool_executed = False
        try:
            transaction = await _prepare_transaction(
                repository,
                request_id=request_id,
                tool_call_id=tool_call_id,
                topic_id=topic_id,
            )
            tool_executed = True
            result = await executor()
            report = await _reconcile(transaction)
            _journal_operation(
                transaction.repository,
                "verify.write_journal",
                lambda: _update_journal_entry(
                    key,
                    phase="verified",
                    changed_paths=list(report.changed_paths),
                    guard_error="",
                    deferred_restart=bool(result.get("_deferred_restart")),
                ),
            )
            return _append_rollback_notice(result, report)
        except CoreGuardError as exc:
            phase = "recovery_failed" if tool_executed else "preparation_failed"
            failure_message = await _rollback_failure_message(
                exc,
                transaction,
                tool_executed=tool_executed,
            )
            try:
                transactions = _journal_operation(
                    repository,
                    "failure.read_journal",
                    _load_journal,
                )
                if not transactions.get(key):
                    _journal_operation(
                        repository,
                        "failure.create_journal_entry",
                        lambda: _set_journal_entry(
                            key,
                            {
                                "request_id": int(request_id),
                                "tool_call_id": tool_call_id,
                                "topic_id": int(topic_id),
                                "repository": str(repository.resolve()),
                                "phase": phase,
                                "snapshot_ref": None,
                            },
                        ),
                    )
                _journal_operation(
                    repository,
                    "failure.write_journal",
                    lambda: _update_journal_entry(
                        key,
                        phase=phase,
                        guard_error=failure_message,
                    ),
                )
            except CoreGuardError as journal_error:
                journal_message = journal_error.agent_message(tool_executed=tool_executed)
                failure_message = f"{failure_message}\n\njournal_failure:\n{journal_message}"
                logger.critical(
                    "Could not persist Yadreno Admin core guard failure: %s",
                    journal_error,
                )
            log = logger.critical if tool_executed else logger.error
            log(
                "Yadreno Admin core guard failure: request_id=%s tool_call_id=%s stage=%s executed=%s error=%s",
                request_id,
                tool_call_id,
                exc.stage,
                tool_executed,
                exc,
            )
            return {"result": "", "error": failure_message}
        finally:
            _repository_guard_active -= 1


async def _delete_snapshot_ref(transaction: GuardTransaction) -> None:
    if not transaction.snapshot_ref:
        return
    await _run_git(
        transaction.repository,
        ["update-ref", "-d", transaction.snapshot_ref, transaction.baseline_oid],
        stage="finalize.delete_ref",
    )


async def finalize_core_guard(request_id: int, tool_call_id: str) -> bool:
    """Remove a verified checkpoint after the hub accepted the tool result."""
    key = _transaction_key(request_id, tool_call_id)
    async with _repository_lock:
        repository = DEFAULT_REPOSITORY
        try:
            entry = _journal_operation(
                repository,
                "finalize.read_journal",
                _load_journal,
            ).get(key)
            if not isinstance(entry, dict):
                return True
            if entry.get("phase") == "recovery_failed":
                return False
            transaction = _entry_to_transaction(key, entry)
            await _delete_snapshot_ref(transaction)
            _journal_operation(
                transaction.repository,
                "finalize.delete_journal_entry",
                lambda: _delete_journal_entry(key),
            )
        except CoreGuardError as exc:
            try:
                _journal_operation(
                    repository,
                    "finalize.record_failure",
                    lambda: _update_journal_entry(
                        key,
                        phase="recovery_failed",
                        guard_error=exc.agent_message(tool_executed=True),
                    ),
                )
            except CoreGuardError as journal_error:
                logger.critical("Failed to persist core guard finalize failure: %s", journal_error)
            logger.critical("Failed to finalize Yadreno Admin core guard: %s", exc)
            return False
        return True


async def finalize_core_guards_for_request(request_id: int) -> bool:
    """Clean recovered checkpoints when a final event proves hub acceptance."""
    async with _repository_lock:
        repository = DEFAULT_REPOSITORY
        try:
            transactions = _journal_operation(
                repository,
                "finalize_request.read_journal",
                _load_journal,
            )
        except CoreGuardError as exc:
            logger.critical("Failed to read core guards for completed request: %s", exc)
            return False

        success = True
        for key, entry in list(transactions.items()):
            if not isinstance(entry, dict):
                success = False
                logger.critical("Invalid non-object core guard journal entry %s", key)
                continue
            try:
                entry_request_id = int(entry.get("request_id") or 0)
            except (TypeError, ValueError):
                success = False
                logger.critical("Invalid request_id in core guard journal entry %s", key)
                continue
            if entry_request_id != int(request_id):
                continue
            if entry.get("phase") == "recovery_failed":
                success = False
                continue
            try:
                transaction = _entry_to_transaction(key, entry)
                await _delete_snapshot_ref(transaction)
                _journal_operation(
                    transaction.repository,
                    "finalize_request.delete_journal_entry",
                    lambda key=key: _delete_journal_entry(key),
                )
            except (CoreGuardError, TypeError, ValueError) as exc:
                success = False
                if isinstance(exc, CoreGuardError):
                    message = exc.agent_message(tool_executed=True)
                else:
                    message = f"Invalid core guard journal entry: {exc}"
                try:
                    _journal_operation(
                        repository,
                        "finalize_request.record_failure",
                        lambda key=key, message=message: _update_journal_entry(
                            key,
                            phase="recovery_failed",
                            guard_error=message,
                        ),
                    )
                except CoreGuardError as journal_error:
                    logger.critical(
                        "Failed to persist completed-request guard failure: %s",
                        journal_error,
                    )
                logger.critical("Failed to finalize recovered core guard %s: %s", key, exc)
        return success


def interrupted_tool_result(request_id: int, tool_call_id: str) -> Optional[dict[str, Any]]:
    """Return a precise result for a tool interrupted by a satellite restart."""
    try:
        entry = _journal_operation(
            DEFAULT_REPOSITORY,
            "resume.read_journal",
            _load_journal,
        ).get(_transaction_key(request_id, tool_call_id))
    except CoreGuardError as exc:
        logger.critical("Failed to read interrupted core guard journal: %s", exc)
        return {"result": "", "error": exc.agent_message(tool_executed=True)}
    if not isinstance(entry, dict):
        return None
    phase = str(entry.get("phase") or "")
    guard_error = str(entry.get("guard_error") or "").strip()
    if phase == "recovery_failed":
        message = guard_error or "Core recovery failed; the checkpoint was retained for manual repair."
    elif phase == "preparation_failed":
        message = guard_error or "The satellite restarted before the protected tool was executed."
    else:
        changed = entry.get("changed_paths") or []
        suffix = f" Reverted paths: {', '.join(map(str, changed[:MAX_REPORTED_PATHS]))}." if changed else ""
        message = (
            "The satellite restarted during or immediately after this tool call. The Git-visible "
            "project state was restored and verified before polling resumed; the original command "
            "output is unavailable. Ignored files, databases and system-level side effects may "
            f"remain.{suffix} Inspect the current state before repeating the action."
        )
    return {
        "result": "",
        "error": message,
        "_deferred_restart": bool(entry.get("deferred_restart")),
    }


async def recover_core_guards_on_startup() -> None:
    """Restore interrupted protected transactions before satellite polling resumes."""
    global _repository_guard_active

    async with _repository_lock:
        _repository_guard_active += 1
        try:
            transactions = _journal_operation(
                DEFAULT_REPOSITORY,
                "startup.read_journal",
                _load_journal,
            )
            for key, entry in list(transactions.items()):
                if not isinstance(entry, dict):
                    continue
                phase = str(entry.get("phase") or "")
                if phase in {"preparing", "checkpointing", "preparation_failed"}:
                    if phase == "checkpointing":
                        try:
                            await _delete_snapshot_ref(_entry_to_transaction(key, entry))
                        except (CoreGuardError, TypeError, ValueError) as exc:
                            if isinstance(exc, CoreGuardError):
                                message = exc.agent_message(tool_executed=False)
                            else:
                                message = f"Invalid core guard journal entry: {exc}"
                            _journal_operation(
                                DEFAULT_REPOSITORY,
                                "startup.record_checkpoint_cleanup_failure",
                                lambda: _update_journal_entry(
                                    key,
                                    phase="recovery_failed",
                                    guard_error=message,
                                ),
                            )
                            logger.critical(
                                "Failed to clean interrupted Yadreno Admin checkpoint %s: %s",
                                key,
                                exc,
                            )
                            continue
                    _journal_operation(
                        DEFAULT_REPOSITORY,
                        "startup.record_preparation_interruption",
                        lambda: _update_journal_entry(
                            key,
                            phase="preparation_failed",
                            snapshot_ref=None,
                            guard_error=(
                                str(entry.get("guard_error") or "")
                                or "The satellite restarted before the protected tool was executed."
                            ),
                        ),
                    )
                    continue
                try:
                    transaction = _entry_to_transaction(key, entry)
                    report = await _reconcile(transaction)
                    _journal_operation(
                        transaction.repository,
                        "startup.record_recovery",
                        lambda: _update_journal_entry(
                            key,
                            phase="recovered",
                            changed_paths=list(report.changed_paths),
                            guard_error="",
                        ),
                    )
                    logger.warning(
                        "Recovered interrupted Yadreno Admin core guard: request_id=%s tool_call_id=%s reverted=%s",
                        transaction.request_id,
                        transaction.tool_call_id,
                        len(report.changed_paths),
                    )
                except (CoreGuardError, TypeError, ValueError) as exc:
                    if isinstance(exc, CoreGuardError):
                        message = exc.agent_message(tool_executed=True)
                    else:
                        message = f"Invalid core guard journal entry: {exc}"
                    _journal_operation(
                        DEFAULT_REPOSITORY,
                        "startup.record_recovery_failure",
                        lambda: _update_journal_entry(
                            key,
                            phase="recovery_failed",
                            guard_error=message,
                        ),
                    )
                    logger.critical("Failed to recover Yadreno Admin core guard %s: %s", key, exc)
        finally:
            _repository_guard_active -= 1
