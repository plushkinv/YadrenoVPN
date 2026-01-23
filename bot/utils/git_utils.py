"""
Утилиты для работы с Git.

Функции для проверки обновлений, выполнения git pull и перезапуска бота.
"""
import subprocess
import logging
import sys
import os
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)


def get_project_root() -> str:
    """
    Получает корневую директорию проекта.
    
    Returns:
        Абсолютный путь к корню проекта
    """
    # Поднимаемся от bot/utils/ к корню
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_git_command(args: List[str], timeout: int = 30) -> Tuple[bool, str]:
    """
    Выполняет git-команду.
    
    Args:
        args: Аргументы для git (например ['pull', 'origin', 'main'])
        timeout: Таймаут в секундах
    
    Returns:
        (success, output) - успех и вывод команды
    """
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=get_project_root(),
            capture_output=True,
            text=True,
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
    Проверяет доступность git.
    
    Returns:
        True если git доступен
    """
    success, _ = run_git_command(['--version'])
    return success


def get_current_commit() -> Optional[str]:
    """
    Получает хеш текущего коммита.
    
    Returns:
        Короткий хеш коммита или None при ошибке
    """
    success, output = run_git_command(['rev-parse', '--short', 'HEAD'])
    return output if success else None


def get_current_branch() -> Optional[str]:
    """
    Получает имя текущей ветки.
    
    Returns:
        Имя ветки или None при ошибке
    """
    success, output = run_git_command(['branch', '--show-current'])
    return output if success else None


def get_remote_url() -> Optional[str]:
    """
    Получает URL удалённого репозитория origin.
    
    Returns:
        URL или None при ошибке
    """
    success, output = run_git_command(['remote', 'get-url', 'origin'])
    return output if success else None


def set_remote_url(url: str) -> Tuple[bool, str]:
    """
    Устанавливает URL удалённого репозитория origin.
    
    Args:
        url: Новый URL репозитория
    
    Returns:
        (success, message)
    """
    # Проверяем, есть ли remote origin
    success, _ = run_git_command(['remote', 'get-url', 'origin'])
    
    if success:
        # Меняем существующий
        return run_git_command(['remote', 'set-url', 'origin', url])
    else:
        # Добавляем новый
        return run_git_command(['remote', 'add', 'origin', url])


def check_for_updates() -> Tuple[bool, int, str]:
    """
    Проверяет наличие обновлений на сервере.
    
    Returns:
        (success, commits_behind, log_text)
        - success: успешно ли выполнена проверка
        - commits_behind: количество коммитов позади
        - log_text: лог новых коммитов или сообщение об ошибке
    """
    # Получаем обновления с сервера
    success, output = run_git_command(['fetch', 'origin'], timeout=60)
    if not success:
        return False, 0, f"Ошибка fetch: {output}"
    
    # Получаем текущую ветку
    branch = get_current_branch()
    if not branch:
        return False, 0, "Не удалось определить текущую ветку"
    
    # Считаем количество коммитов позади
    success, output = run_git_command([
        'rev-list', '--count', f'HEAD..origin/{branch}'
    ])
    
    if not success:
        return False, 0, f"Ошибка подсчёта коммитов: {output}"
    
    try:
        commits_behind = int(output.strip())
    except ValueError:
        return False, 0, f"Неверный формат: {output}"
    
    if commits_behind == 0:
        return True, 0, "✅ Бот уже обновлён до последней версии"
    
    # Получаем лог новых коммитов
    success, log_output = run_git_command([
        'log', '--oneline', f'HEAD..origin/{branch}', '-n', '10'
    ])
    
    log_text = f"📦 Доступно обновлений: {commits_behind}\n\n"
    if success and log_output:
        log_text += "Последние изменения:\n```\n" + log_output + "\n```"
    
    return True, commits_behind, log_text


def pull_updates() -> Tuple[bool, str]:
    """
    Выполняет git pull для обновления кода.
    
    Returns:
        (success, message)
    """
    # Проверяем на наличие локальных изменений
    success, status = run_git_command(['status', '--porcelain'])
    if success and status.strip():
        return False, "❌ Есть локальные изменения. Сделайте commit или stash перед обновлением."
    
    # Выполняем pull
    success, output = run_git_command(['pull', 'origin'], timeout=120)
    
    if not success:
        if 'conflict' in output.lower():
            return False, "❌ Конфликт слияния. Требуется ручное разрешение."
        return False, f"❌ Ошибка обновления:\n{output}"
    
    return True, f"✅ Обновление успешно!\n\n{output}"


def get_recent_commits(limit: int = 5) -> str:
    """
    Получает последние коммиты.
    
    Args:
        limit: Количество коммитов
    
    Returns:
        Форматированный список коммитов
    """
    success, output = run_git_command([
        'log', '--oneline', '-n', str(limit)
    ])
    
    if success and output:
        return output
    return "Не удалось получить историю коммитов"


def restart_bot() -> None:
    """
    Перезапускает бота, заменяя текущий процесс.
    
    Использует os.execv для замены текущего процесса новым.
    """
    logger.info("🔄 Перезапуск бота...")
    
    # Получаем путь к Python и аргументы запуска
    python = sys.executable
    script = os.path.join(get_project_root(), 'main.py')
    
    # Заменяем текущий процесс новым
    os.execv(python, [python, script])
