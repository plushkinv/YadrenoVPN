"""
Utilities for working with Git.

Functions for checking for updates, performing git pull, and restarting the bot.
"""
import subprocess
import logging
import sys
import os
from typing import Tuple, Optional, List, Dict

logger = logging.getLogger(__name__)


def get_project_root() -> str:
    """
    Gets the root directory of the project.
    
    Returns:
        Absolute path to the project root
    """
    # We rise from bot/utils/ to the root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_git_command(args: List[str], timeout: int = 30) -> Tuple[bool, str]:
    """
    Executes a git command.
    
    Args:
        args: Arguments for git (eg ['pull', 'origin', 'main'])
        timeout: Timeout in seconds
    
    Returns:
        (success, output) - success and output of the command
    """
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=get_project_root(),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0
        return success, output.strip()
    except subprocess.TimeoutExpired:
        return False, "⏱ Превышено время ожидания команды"
    except FileNotFoundError:
        return False, "❌ Git не установлен или не найден в PATH"
    except Exception as e:
        logger.error(f"Ошибка выполнения git: {e}")
        return False, f"❌ Ошибка: {e}"


def check_git_available() -> bool:
    """
    Checks git availability.
    
    Returns:
        True if git is available
    """
    success, _ = run_git_command(['--version'])
    return success


def get_current_commit() -> Optional[str]:
    """
    Gets the hash of the current commit.
    
    Returns:
        Short commit hash or None on error
    """
    success, output = run_git_command(['rev-parse', '--short', 'HEAD'])
    return output if success else None


def get_current_branch() -> Optional[str]:
    """
    Gets the name of the current branch.
    
    Returns:
        Branch name or None on error
    """
    success, output = run_git_command(['branch', '--show-current'])
    return output if success else None


def get_remote_url() -> Optional[str]:
    """
    Gets the URL of the remote repository origin.
    
    Returns:
        URL or None on error
    """
    success, output = run_git_command(['remote', 'get-url', 'origin'])
    return output if success else None


def set_remote_url(url: str) -> Tuple[bool, str]:
    """
    Sets the URL of the remote repository origin.
    
    Args:
        url: New repository URL
    
    Returns:
        (success, message)
    """
    # Checking if there is a remote origin
    success, _ = run_git_command(['remote', 'get-url', 'origin'])
    
    if success:
        # We change the existing one
        return run_git_command(['remote', 'set-url', 'origin', url])
    else:
        # Add a new one
        return run_git_command(['remote', 'add', 'origin', url])


def get_pending_commits_list() -> Tuple[bool, List[Dict[str, str]]]:
    """
    Gets a list of commits between HEAD and origin/branch.
    
    Runs git fetch before checking out.
    
    Returns:
        (success, commits) — list of dictionaries [{"hash": str, "message": str}, ...]
        from old to new (--reverse)
    """
    # Receiving updates from the server
    success, output = run_git_command(['fetch', 'origin'], timeout=60)
    if not success:
        logger.error(f"Ошибка fetch при получении списка коммитов: {output}")
        return False, []
    
    # Getting the current branch
    branch = get_current_branch()
    if not branch:
        logger.error("Не удалось определить текущую ветку")
        return False, []
    
    # Checking if the remote branch exists
    success, _ = run_git_command(['rev-parse', '--verify', f'origin/{branch}'])
    if not success:
        logger.warning(f"Удаленная ветка origin/{branch} не найдена. Обновления недоступны.")
        return True, []
        
    # We get a list of commits from old to new
    success, output = run_git_command([
        'log', f'HEAD..origin/{branch}', '--format=%H|%s', '--reverse'
    ])
    
    if not success:
        logger.error(f"Ошибка получения списка коммитов: {output}")
        return False, []
    
    if not output.strip():
        return True, []
    
    commits = []
    for line in output.strip().split('\n'):
        if '|' in line:
            parts = line.split('|', 1)
            commits.append({
                "hash": parts[0].strip(),
                "message": parts[1].strip()
            })
    
    logger.debug(f"Найдено {len(commits)} ожидающих коммитов")
    return True, commits


def find_first_blocking_commit(commits: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    Finds the first blocking commit in the list.
    
    A blocking commit is one whose message begins with '!'.
    Pure function, no git operations.
    
    Args:
        commits: List of commits from get_pending_commits_list()
    
    Returns:
        Dictionary {"hash": ..., "message": ...} or None if there are no blockers
    """
    for commit in commits:
        if commit.get("message", "").startswith("!"):
            return commit
    return None


def pull_to_commit(commit_hash: str) -> Tuple[bool, str]:
    """
    Updates code to a specific commit via git reset --hard.
    
    DOES NOT restart - this is the responsibility of the calling code.
    
    Args:
        commit_hash: Full hash of the commit to update
    
    Returns:
        (success, message) - the result of the operation
    """
    try:
        success, output = run_git_command(['reset', '--hard', commit_hash], timeout=120)
        if not success:
            logger.error(f"Ошибка pull_to_commit({commit_hash}): {output}")
            return False, f"❌ Ошибка обновления до коммита {commit_hash[:8]}:\n{output}"
        
        commit_info = get_last_commit_info('HEAD')
        logger.info(f"✅ Успешно обновлено до блокирующего коммита {commit_hash[:8]}")
        return True, f"✅ Обновление до блокирующего коммита завершено!\n\n🔹 Текущий коммит:\n<pre>{commit_info}</pre>"
    except Exception as e:
        logger.error(f"Исключение в pull_to_commit({commit_hash}): {e}", exc_info=True)
        return False, f"❌ Критическая ошибка: {e}"


def check_for_updates() -> Tuple[bool, int, str, bool, Optional[Dict[str, str]], bool]:
    """
    Checks for updates on the server.
    
    Returns:
        (success, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only)
        - success: whether the check was successful
        - commits_behind: number of commits behind
        - log_text: log of new commits or error message
        - has_blocking: whether there is a blocking commit among the pending ones
        - blocking_commit: dictionary {"hash": ..., "message": ...} of the first blocker or None
        - is_beta_only: whether all pending commits are beta (starting with '?')
    """
    # We get a list of pending commits (does fetch inside)
    success, pending_commits = get_pending_commits_list()
    if not success:
        return False, 0, "Ошибка получения списка коммитов", False, None, False
    
    commits_behind = len(pending_commits)
    
    if commits_behind == 0:
        return True, 0, "✅ Бот уже обновлён до последней версии", False, None, False
    
    # Looking for a blocking commit
    blocking_commit = find_first_blocking_commit(pending_commits)
    has_blocking = blocking_commit is not None
    
    # We check on the beta version (start with '?')
    is_beta_only = all(c.get("message", "").startswith("?") for c in pending_commits)
    
    if has_blocking:
        logger.info(f"⚠️ Обнаружен блокирующий коммит: {blocking_commit['hash'][:8]} — {blocking_commit['message']}")
    
    # We get the current branch for the log
    branch = get_current_branch() or 'main'
    
    # We get a log of new commits
    success_log, log_output = run_git_command([
        'log', '--format=%h %B', f'HEAD..origin/{branch}', '-n', '10'
    ])
    
    log_text = f"📦 Доступно обновлений: {commits_behind}\n\n"
    if success_log and log_output:
        log_text += "Последние изменения:\n<pre>" + log_output + "</pre>"
    
    return True, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only


def pull_updates() -> Tuple[bool, str]:
    """
    Performs a git pull to update the code.
    
    Returns:
        (success, message) - the message contains information about the commit
    """
    success, status = run_git_command(['status', '--porcelain'])
    if success and status.strip():
        return False, "❌ Есть локальные изменения. Сделайте commit или stash перед обновлением."
    
    success, output = run_git_command(['pull', 'origin'], timeout=120)
    
    if not success:
        if 'conflict' in output.lower():
            return False, "❌ Конфликт слияния. Требуется ручное разрешение."
        return False, f"❌ Ошибка обновления:\n{output}"
    
    commit_info = get_last_commit_info('HEAD')
    return True, f"✅ Обновление успешно!\n\n🔹 Последний коммит:\n<pre>{commit_info}</pre>"


def force_pull_updates() -> Tuple[bool, str]:
    """
    Performs a forced git fetch and reset, completely overwriting local changes.
    
    The function itself does NOT check for blocking commits - that is the responsibility of the calling code
    (the handler in system.py checks for blocking commits before calling).
    Always updates to the latest version of origin/branch.
    
    Returns:
        (success, message)
    """
    # Download all changes
    success, output = run_git_command(['fetch', 'origin'], timeout=120)
    if not success:
        return False, f"❌ Ошибка fetch:\n{output}"
    
    branch = get_current_branch()
    if not branch:
        branch = "main"
        
    # Force a reset to a remote branch - blocking markers are ignored
    success, output = run_git_command(['reset', '--hard', f'origin/{branch}'], timeout=120)
    if not success:
        return False, f"❌ Ошибка принудительного обновления:\n{output}"
        
    commit_info = get_last_commit_info('HEAD')
    return True, f"✅ Принудительное обновление успешно завершено!\nВсе файлы перезаписаны из репозитория.\n\n🔹 Актуальный коммит:\n<pre>{commit_info}</pre>"


def get_last_commit_info(revision: str = 'HEAD') -> str:
    """Gets information about the last commit."""
    success, output = run_git_command([
        'log', '--format=%h %B', '-n', '1', revision
    ])
    if success and output:
        return output
    return "Не удалось получить информацию о последнем коммите"


def get_previous_commits_info(limit: int = 5, revision: str = 'HEAD') -> str:
    """Retrieves previous commits, skipping the last one."""
    success, output = run_git_command([
        'log', '--format=%h %B', '--skip=1', '-n', str(limit), revision
    ])
    if success and output:
        return output
    return "Нет предыдущих коммитов"


def install_requirements() -> Tuple[bool, str]:
    """
    Installs/updates dependencies from requirements.txt.

    Uses pip install --upgrade to change versions correctly
    packages and their dependencies.

    Returns:
        (success, message) - installation result
    """
    project_root = get_project_root()
    requirements_path = os.path.join(project_root, 'requirements.txt')

    if not os.path.exists(requirements_path):
        logger.warning("requirements.txt не найден, пропускаем установку зависимостей")
        return True, "requirements.txt не найден"

    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--upgrade', '-r', requirements_path],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=300
        )

        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Ошибка установки зависимостей: {error_output}")
            return False, f"❌ Ошибка установки зависимостей:\n{error_output}"

        logger.info("✅ Зависимости успешно обновлены")
        return True, "✅ Зависимости обновлены"

    except subprocess.TimeoutExpired:
        logger.error("Таймаут установки зависимостей (300 сек)")
        return False, "❌ Превышено время ожидания установки зависимостей"
    except Exception as e:
        logger.error(f"Исключение при установке зависимостей: {e}")
        return False, f"❌ Ошибка: {e}"


def restart_bot() -> None:
    """
    Restarts the bot, replacing the current process.

    Uses os.execv to replace the current process with a new one.
    """
    logger.info("🔄 Перезапуск бота...")
    
    # Getting the path to Python and launch arguments
    python = sys.executable
    script = os.path.join(get_project_root(), 'main.py')
    
    # Replace the current process with a new one
    os.execv(python, [python, script])
