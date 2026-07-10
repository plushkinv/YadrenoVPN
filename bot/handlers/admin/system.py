"""
Обработчики раздела «Настройки бота».

Управление обновлением, остановкой бота и редактированием текстов.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from config import GITHUB_REPO_URL
from bot.utils.admin import is_admin
from bot.utils.git_utils import (
    check_git_available,
    get_current_commit,
    get_current_branch,
    get_remote_url,
    set_remote_url,
    check_for_updates,
    pull_updates,
    pull_to_commit,
    force_pull_updates,
    get_last_commit_info,
    get_previous_commits_info,
    install_requirements,
    restart_bot,
)
from bot.keyboards.admin import (
    bot_settings_kb,
    bot_mode_toggle_confirm_kb,
    extensions_diagnostics_kb,
    update_confirm_kb,
    force_overwrite_confirm_kb,
    stop_bot_confirm_kb,
    back_and_home_kb,
    admin_logs_menu_kb,
    yadreno_admin_agent_kb,
    yadreno_admin_no_key_kb,
)
from bot.services.yadreno_admin import (
    YADRENO_ADMIN_CHAT_TOPIC_ID,
    YadrenoAdminError,
    YadrenoAdminUpload,
    run_dialog_with_uploads,
)
from bot.states.admin_states import AdminStates
from database.requests import get_yadreno_admin_api_key

logger = logging.getLogger(__name__)

from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.update_block import is_update_blocked, get_blocked_message, try_unblock, set_update_blocked

router = Router()


_EXTENSION_STATUS_LABELS = {
    'ok': 'найдена',
    'directory_missing': 'папка не создана',
    'not_directory': 'путь не является папкой',
}

_EXTENSION_LOAD_REASON_LABELS = {
    'not_loaded': 'загрузка ещё не выполнялась',
    'disabled': 'загрузка выключена',
    'directory_missing': 'папка не создана',
    'not_directory': 'путь не является папкой',
}

_EXTENSION_REGISTRATION_LABELS = {
    'actions': 'actions',
    'guards': 'guards',
    'page_hooks': 'hooks',
    'pricing_policies': 'pricing',
    'promo_reward_policies': 'promo rewards',
    'referral_reward_policies': 'referral rewards',
    'key_lifecycle_hooks': 'key lifecycle',
    'payment_providers': 'payment providers',
    'schemas': 'schemas',
}


# ============================================================================
# ГЛАВНОЕ МЕНЮ НАСТРОЕК
# ============================================================================

@router.callback_query(F.data == "admin_bot_settings")
async def show_bot_settings(callback: CallbackQuery, state: FSMContext):
    """Показывает меню настроек бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from bot.services.vpn_api import get_bot_mode
    mode = get_bot_mode()
    if mode == 'subscription':
        mode_label = "📡 Подписка"
        mode_desc = (
            "Бот выдаёт пользователю одну <b>subscription-ссылку</b> — "
            "клиент сам подтягивает все протоколы сервера."
        )
    else:
        mode_label = "🔑 Ключи"
        mode_desc = (
            "Бот создаёт один VLESS/VMess-клиент в одном inbound "
            "и выдаёт ссылку + JSON-конфиг."
        )

    text = (
        "⚙️ <b>Настройки бота</b>\n\n"
        f"<b>Режим работы:</b> {mode_label}\n"
        f"<i>{mode_desc}</i>\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(callback.message,
        text,
        reply_markup=bot_settings_kb(mode)
    )
    await callback.answer()


async def _show_bot_mode_confirm(callback: CallbackQuery, target: str):
    """Показывает подтверждение переключения режима работы бота."""
    if target == 'subscription':
        warning = (
            "⚠️ <b>Переключение в режим Подписка</b>\n\n"
            "При ближайших синхронизациях (≈раз в 30 минут) бот:\n"
            "• создаст клиентов во всех inbound каждого сервера для существующих ключей "
            "(с единым subId и email);\n"
            "• новые ключи будут выдаваться как <b>subscription URL</b>.\n\n"
            "Текущие пользователи продолжат работать со старыми ссылками "
            "до их замены или продления.\n\n"
            "Продолжить?"
        )
    else:
        warning = (
            "⚠️ <b>Переключение в режим Ключи</b>\n\n"
            "При ближайших синхронизациях бот:\n"
            "• оставит на каждом сервере по одному клиенту (в inbound с минимальным id) "
            "на каждый ключ;\n"
            "• остальных клиентов с тем же email — <b>удалит</b>;\n"
            "• новые ключи будут выдаваться как одна VLESS/VMess-ссылка.\n\n"
            "<b>Subscription URL у пользователей перестанут работать.</b>\n\n"
            "Продолжить?"
        )

    await safe_edit_or_send(callback.message, warning,
                            reply_markup=bot_mode_toggle_confirm_kb(target))
    await callback.answer()


@router.callback_query(F.data == "admin_extensions_diagnostics")
async def show_extensions_diagnostics(callback: CallbackQuery, state: FSMContext):
    """Показывает read-only диагностику пользовательских расширений."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from bot.utils.custom_extensions import get_custom_extensions_diagnostics

    diagnostics = get_custom_extensions_diagnostics()
    await safe_edit_or_send(
        callback.message,
        _format_extensions_diagnostics(diagnostics),
        reply_markup=extensions_diagnostics_kb(),
    )
    await callback.answer()


def _format_extensions_diagnostics(diagnostics: dict) -> str:
    enabled = bool(diagnostics.get('enabled'))
    status_icon = '🟢' if enabled else '⚪'
    directory = Path(str(diagnostics.get('directory') or 'custom_extensions'))
    directory_label = _EXTENSION_STATUS_LABELS.get(
        str(diagnostics.get('directory_status') or ''),
        'неизвестно',
    )

    last_load = diagnostics.get('last_load') or {}
    loaded = list(last_load.get('loaded') or [])
    failed = dict(last_load.get('failed') or {})
    skipped = bool(last_load.get('skipped'))
    reason = str(last_load.get('reason') or '')
    reason_label = _EXTENSION_LOAD_REASON_LABELS.get(reason, reason or 'выполнена')

    files = list(diagnostics.get('files') or [])
    candidates = sum(1 for item in files if item.get('status') == 'candidate')
    invalid = sum(1 for item in files if item.get('status') == 'invalid_filename')
    ignored = sum(1 for item in files if item.get('status') == 'ignored_private')

    lines = [
        "🧩 <b>Диагностика расширений</b>",
        "",
        f"<b>Загрузка:</b> {status_icon} {'включена' if enabled else 'выключена'}",
        f"<b>Папка:</b> <code>{escape_html(directory.name)}</code> — {escape_html(directory_label)}",
        f"<b>Последняя загрузка:</b> {escape_html(reason_label) if skipped else 'выполнена'}",
        f"<b>Файлы:</b> {len(files)} всего, {candidates} к загрузке, {invalid} с ошибкой имени, {ignored} приватных",
        f"<b>Итог:</b> {len(loaded)} загружено, {len(failed)} с ошибками",
    ]

    if loaded:
        lines.extend(["", "<b>Загружены:</b>"])
        lines.extend(f"• <code>{escape_html(name)}</code>" for name in loaded[:8])
        if len(loaded) > 8:
            lines.append(f"• ещё {len(loaded) - 8}")

    if failed:
        lines.extend(["", "<b>Ошибки:</b>"])
        for filename, error in list(sorted(failed.items()))[:6]:
            short_error = str(error)[:180]
            lines.append(f"• <code>{escape_html(filename)}</code>: {escape_html(short_error)}")
        if len(failed) > 6:
            lines.append(f"• ещё {len(failed) - 6}")

    registrations = dict(diagnostics.get('registrations') or {})
    if registrations:
        lines.extend(["", "<b>Регистрации расширений:</b>"])
        for extension_name, items in list(sorted(registrations.items()))[:8]:
            summary = _format_extension_registration_summary(items)
            lines.append(f"• <code>{escape_html(extension_name)}</code>: {escape_html(summary)}")
        if len(registrations) > 8:
            lines.append(f"• ещё {len(registrations) - 8}")

    totals = dict(diagnostics.get('registry_totals') or {})
    if totals:
        lines.extend(["", "<b>Текущий registry:</b>"])
        lines.append(
            "• "
            + ", ".join(
                f"{_EXTENSION_REGISTRATION_LABELS.get(key, key)}: {value}"
                for key, value in totals.items()
            )
        )

    return "\n".join(lines)


def _format_extension_registration_summary(items: dict) -> str:
    parts: list[str] = []
    for key, label in _EXTENSION_REGISTRATION_LABELS.items():
        count = len(items.get(key) or [])
        if count:
            parts.append(f"{label} {count}")
    return ", ".join(parts) if parts else "нет зарегистрированных объектов"


@router.callback_query(F.data.startswith("admin_select_bot_mode:"))
async def admin_select_bot_mode(callback: CallbackQuery, state: FSMContext):
    """Открывает подтверждение только при выборе другого режима."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    target = callback.data.split(":", 1)[1]
    if target not in ('subscription', 'key'):
        await callback.answer("⛔ Недопустимое значение", show_alert=True)
        return

    from bot.services.vpn_api import get_bot_mode
    current = get_bot_mode()
    if target == current:
        label = "📡 Подписка" if target == 'subscription' else "🔑 Ключи"
        await callback.answer(f"Режим уже выбран: {label}")
        return

    await _show_bot_mode_confirm(callback, target)


@router.callback_query(F.data == "admin_toggle_bot_mode")
async def admin_toggle_bot_mode(callback: CallbackQuery, state: FSMContext):
    """Совместимый toggle для старых сообщений."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from bot.services.vpn_api import get_bot_mode
    current = get_bot_mode()
    target = 'key' if current == 'subscription' else 'subscription'
    await _show_bot_mode_confirm(callback, target)


@router.callback_query(F.data.startswith("admin_set_bot_mode:"))
async def admin_set_bot_mode(callback: CallbackQuery, state: FSMContext):
    """Сохраняет новый режим работы бота в settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    target = callback.data.split(":", 1)[1]
    if target not in ('subscription', 'key'):
        await callback.answer("⛔ Недопустимое значение", show_alert=True)
        return

    from database.db_settings import set_setting
    set_setting('bot_mode', target)
    logger.info(
        f"Bot mode переключён в '{target}' администратором {callback.from_user.id}"
    )
    label = "📡 Подписка" if target == 'subscription' else "🔑 Ключи"
    await callback.answer(f"✅ Режим установлен: {label}", show_alert=True)
    await show_bot_settings(callback, state)






# ============================================================================
# РУЧНОЕ ОБНОВЛЕНИЕ БОТА (КОМАНДОЙ /UPDATE)
# ============================================================================

@router.message(Command("update"))
async def admin_update_cmd(message: Message, state: FSMContext):
    """Скрытая команда экстренного обновления для администраторов."""
    if not is_admin(message.from_user.id):
        return
        
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL and GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
        
    await safe_edit_or_send(message,
        "🔄 <b>Экстренное обновление...</b>\n\n"
        "Загружаю изменения с GitHub..."
    )
    
    success, log_message = pull_updates()
    
    if not success:
        await safe_edit_or_send(message,
            f"❌ <b>Ошибка обновления</b>\n\n{log_message}"
        )
        return
        
    logger.info(f"🔄 Бот экстренно обновлён администратором {message.from_user.id} через команду /update")
    
    await safe_edit_or_send(message,
        f"✅ <b>Обновление завершено!</b>\n\n{log_message}\n\n"
        "🔄 Перезапуск бота через 2 секунды...",
        force_new=True
    )
    
    await state.clear()
    await asyncio.sleep(2)
    
    # Устанавливаем/обновляем зависимости
    success, req_message = install_requirements()
    if not success:
        logger.error(f"Ошибка установки зависимостей: {req_message}")
        await safe_edit_or_send(message,
            f"⚠️ <b>Ошибка установки зависимостей</b>\n\n{req_message}\n\n"
            "Бот не будет перезапущен. Проверьте requirements.txt и попробуйте снова.",
            force_new=True
        )
        return
    
    restart_bot()


# ============================================================================
# ОБНОВЛЕНИЕ БОТА (ИНТЕРФЕЙС)
# ============================================================================

@router.callback_query(F.data == "admin_update_bot")
async def show_update_confirm(callback: CallbackQuery, state: FSMContext):
    """Показывает подтверждение обновления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем настроен ли GitHub
    if not GITHUB_REPO_URL:
        await safe_edit_or_send(callback.message, 
            "❌ <b>GitHub не настроен</b>\n\n"
            "Укажите URL репозитория в файле <code>config.py</code>:\n"
            "<code>GITHUB_REPO_URL = \"https://github.com/user/repo.git\"</code>",
            reply_markup=back_and_home_kb("admin_bot_settings")
        )
        await callback.answer()
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)

    # Проверяем условия разблокировки
    try_unblock()

    if is_update_blocked():
        await safe_edit_or_send(callback.message,
            get_blocked_message(),
            reply_markup=back_and_home_kb("admin_bot_settings")
        )
        await callback.answer()
        return
    
    # Показываем сообщение о проверке
    await safe_edit_or_send(callback.message, 
        "🔍 <b>Проверка обновлений...</b>\n\n"
        "Подключаюсь к GitHub..."
    )
    
    # Проверяем наличие обновлений
    success, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only = check_for_updates()
    
    if not success:
        await safe_edit_or_send(callback.message, 
            f"❌ <b>Ошибка проверки</b>\n\n{log_text}",
            reply_markup=back_and_home_kb("admin_bot_settings")
        )
        await callback.answer()
        return
    
    commit_hash = get_current_commit() or "неизвестно"
    
    if commits_behind > 0:
        branch = get_current_branch() or "main"
        target_rev = f"origin/{branch}"
    else:
        target_rev = "HEAD"
        
    last_commit = get_last_commit_info(target_rev)
    previous_commits = get_previous_commits_info(5, target_rev)
    
    # Формируем текст с коммитами
    commits_text = f"🔹 <b>Последний коммит:</b>\n``<code>\n{last_commit}\n</code>``\n"
    if previous_commits != "Нет предыдущих коммитов":
         commits_text += f"\n🔸 <b>Предыдущие 5 коммитов:</b>\n``<code>\n{previous_commits}\n</code>``"
    
    # Сохраняем данные о блокирующем коммите в FSM state
    await state.update_data(
        has_blocking=has_blocking,
        blocking_commit=blocking_commit
    )
    
    # Если обновлений нет
    if commits_behind == 0:
        await safe_edit_or_send(callback.message, 
            "✅ <b>Обновление не требуется, у вас последняя версия</b>\n\n"
            f"Текущая версия: <code>{commit_hash}</code>\n\n"
            f"{commits_text}",
            reply_markup=update_confirm_kb(has_updates=False)
        )
    elif has_blocking and blocking_commit:
        # Есть блокирующее обновление — показываем предупреждение
        # Убираем маркер ! из сообщения при отображении
        blocking_msg = blocking_commit['message'].lstrip('!')
        blocking_hash = blocking_commit['hash'][:8]
        
        await safe_edit_or_send(callback.message, 
            f"⚠️ <b>Блокирующее обновление!</b>\n\n"
            f"📦 <b>Доступно обновлений:</b> {commits_behind}\n"
            f"Текущая версия: <code>{commit_hash}</code>\n\n"
            f"🚫 Среди обновлений найден <b>блокирующий коммит</b> <code>{blocking_hash}</code>:\n"
            f"``<code>\n{blocking_msg}\n</code>``\n\n"
            f"Будет установлен <b>только этот коммит</b>. "
            f"После перезапуска вам потребуется выполнить требуемые действия, "
            f"прежде чем обновляться дальше.\n\n"
            f"{commits_text}",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=True)
        )
    elif is_beta_only:
        # Только бета-обновления
        await safe_edit_or_send(callback.message, 
            f"🧪 <b>Доступна бета-версия!</b>\n\n"
            f"📦 <b>Доступно бета-коммитов:</b> {commits_behind}\n"
            f"Текущая версия: <code>{commit_hash}</code>\n\n"
            f"{commits_text}\n\n"
            "⚠️ Это тестовая версия. Устанавливайте на свой страх и риск.",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=False, is_beta_only=True)
        )
    else:
        # Есть обычные обновления
        await safe_edit_or_send(callback.message, 
            f"📦 <b>Доступно обновлений:</b> {commits_behind}\n\n"
            f"Текущая версия: <code>{commit_hash}</code>\n\n"
            f"{commits_text}\n\n"
            "⚠️ После обновления бот автоматически перезапустится.\n"
            "Это займёт несколько секунд.",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=False, is_beta_only=False)
        )
    
    await callback.answer()


@router.callback_query(F.data == "admin_update_bot_confirm")
async def update_bot_confirmed(callback: CallbackQuery, state: FSMContext):
    """Выполняет обновление и перезапуск бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    # Получаем данные о блокирующем коммите из FSM state
    data = await state.get_data()
    has_blocking = data.get('has_blocking', False)
    blocking_commit = data.get('blocking_commit')
    
    if has_blocking and blocking_commit:
        # Блокирующее обновление — обновляем до конкретного коммита
        await safe_edit_or_send(callback.message, 
            "🔄 <b>Блокирующее обновление...</b>\n\n"
            f"Обновляю до коммита <code>{blocking_commit['hash'][:8]}</code>..."
        )
        
        success, message = pull_to_commit(blocking_commit['hash'])
    else:
        # Обычное обновление — git pull
        await safe_edit_or_send(callback.message, 
            "🔄 <b>Обновление...</b>\n\n"
            "Загружаю изменения с GitHub..."
        )
        
        success, message = pull_updates()
    
    if not success:
        await safe_edit_or_send(callback.message, 
            f"❌ <b>Ошибка обновления</b>\n\n{message}",
            reply_markup=back_and_home_kb("admin_bot_settings")
        )
        await callback.answer()
        return
    
    # Успешное обновление — показываем лог и перезапускаем
    logger.info(f"🔄 Бот обновлён администратором {callback.from_user.id}")
    
    if has_blocking:
        set_update_blocked()
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Блокирующее обновление завершено!</b>\n\n{message}\n\n"
            "⚠️ После перезапуска выполните требуемые действия перед следующим обновлением.\n\n"
            "🔄 Перезапуск бота через 2 секунды..."
        )
    else:
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Обновление завершено!</b>\n\n{message}\n\n"
            "🔄 Перезапуск бота через 2 секунды..."
        )
    
    await callback.answer("Бот перезапускается...", show_alert=True)
    
    # Очищаем FSM state
    await state.clear()
    
    # Даём время на отправку сообщения
    await asyncio.sleep(2)
    
    # Устанавливаем/обновляем зависимости
    success, req_message = install_requirements()
    if not success:
        logger.error(f"Ошибка установки зависимостей: {req_message}")
        await safe_edit_or_send(callback.message,
            f"⚠️ <b>Ошибка установки зависимостей</b>\n\n{req_message}\n\n"
            "Бот не будет перезапущен. Проверьте requirements.txt и попробуйте снова."
        )
        return
    
    # Перезапускаем бота
    restart_bot()



@router.callback_query(F.data == "admin_force_overwrite")
async def show_force_overwrite(callback: CallbackQuery, state: FSMContext):
    """Показывает предупреждение перед принудительной перезаписью."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем настроен ли GitHub
    if not GITHUB_REPO_URL:
        await safe_edit_or_send(callback.message, 
            "❌ <b>GitHub не настроен</b>\n\n"
            "Укажите URL репозитория в файле <code>config.py</code>:\n"
            "<code>GITHUB_REPO_URL = \"https://github.com/user/repo.git\"</code>",
            reply_markup=back_and_home_kb("admin_bot_settings")
        )
        await callback.answer()
        return
        
    await safe_edit_or_send(callback.message, 
        "⚠️ <b>ПРИНУДИТЕЛЬНАЯ ПЕРЕЗАПИСЬ</b>\n\n"
        f"Все файлы бота (кроме конфигурации и баз данных) будут перезаписаны оригинальными файлами из репозитория:\n<code>{GITHUB_REPO_URL}</code>\n\n"
        "🛑 *Внимание: Все ваши локальные изменения в коде будут безвозвратно потеряны!*\n\n"
        "Вы действительно хотите продолжить?",
        reply_markup=force_overwrite_confirm_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_force_overwrite_confirm")
async def force_overwrite_confirmed(callback: CallbackQuery, state: FSMContext):
    """Выполняет принудительную перезапись и перезапуск бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL and GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    await safe_edit_or_send(callback.message, 
        "🔄 <b>Принудительная перезапись...</b>\n\n"
        "Связываюсь с репозиторием и проверяю обновления..."
    )
    
    # Проверяем наличие блокирующих коммитов перед перезаписью
    from bot.utils.git_utils import get_pending_commits_list, find_first_blocking_commit
    
    success_fetch, pending_commits = get_pending_commits_list()
    blocking_commit = find_first_blocking_commit(pending_commits) if success_fetch else None
    
    if blocking_commit:
        # Есть блокирующий коммит — обновляемся только до него (через reset --hard)
        success, message = pull_to_commit(blocking_commit['hash'])
        
        if not success:
            await safe_edit_or_send(callback.message, 
                f"❌ <b>Ошибка перезаписи</b>\n\n{message}",
                reply_markup=back_and_home_kb("admin_bot_settings")
            )
            await callback.answer()
            return
        
        # Ставим блокировку обновлений
        set_update_blocked()
        
        blocking_msg = blocking_commit['message'].lstrip('!')
        blocking_hash = blocking_commit['hash'][:8]
        
        logger.info(f"🔄 Принудительная перезапись до блокирующего коммита {blocking_hash} администратором {callback.from_user.id}")
        
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Обновлено до блокирующего коммита!</b>\n\n{message}\n\n"
            f"🚫 Среди обновлений найден <b>блокирующий коммит</b> <code>{blocking_hash}</code>:\n"
            f"<code>\n{blocking_msg}\n</code>\n\n"
            "⚠️ После перезапуска выполните требуемые действия перед следующим обновлением.\n\n"
            "🔄 Перезапуск бота через 2 секунды..."
        )
    else:
        # Нет блокирующих коммитов — полная перезапись
        success, message = force_pull_updates()
        
        if not success:
            await safe_edit_or_send(callback.message, 
                f"❌ <b>Ошибка перезаписи</b>\n\n{message}",
                reply_markup=back_and_home_kb("admin_bot_settings")
            )
            await callback.answer()
            return
        
        logger.info(f"🔄 Бот принудительно перезаписан администратором {callback.from_user.id}")
        
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Успешно!</b>\n\n{message}\n\n"
            "🔄 Перезапуск бота через 2 секунды..."
        )
    
    await callback.answer("Бот перезапускается...", show_alert=True)
    
    # Очищаем FSM state
    await state.clear()
    
    # Даём время на отправку сообщения
    await asyncio.sleep(2)
    
    # Устанавливаем/обновляем зависимости
    success, req_message = install_requirements()
    if not success:
        logger.error(f"Ошибка установки зависимостей: {req_message}")
        await safe_edit_or_send(callback.message,
            f"⚠️ <b>Ошибка установки зависимостей</b>\n\n{req_message}\n\n"
            "Бот не будет перезапущен. Проверьте requirements.txt и попробуйте снова."
        )
        return
    
    # Перезапускаем бота
    restart_bot()


# ============================================================================
# ИЗМЕНЕНИЕ ТЕКСТОВ (ЗАГЛУШКА)
# ============================================================================

# ============================================================================
# ИЗМЕНЕНИЕ ТЕКСТОВ
# ============================================================================

from bot.states.admin_states import AdminStates

@router.callback_query(F.data == "admin_edit_texts")
async def edit_texts_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора текста для редактирования."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.keyboards.admin import back_and_home_kb
    
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(text="📝 Главная страница", callback_data="edit_text:main"))
    builder.row(InlineKeyboardButton(text="📝 Справка (текст)", callback_data="edit_text:help"))
    builder.row(InlineKeyboardButton(text="📝 Текст перед оплатой", callback_data="edit_text:prepayment"))
    builder.row(InlineKeyboardButton(text="📝 Текст выдачи ключа", callback_data="edit_text:key_delivery"))
    builder.row(InlineKeyboardButton(text="📢 Ссылка: Новости", callback_data="edit_link:news"))
    builder.row(InlineKeyboardButton(text="💬 Ссылка: Поддержка", callback_data="edit_link:support"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_bot_settings"))
    
    await safe_edit_or_send(callback.message, 
        "✏️ <b>Редактирование текстов</b>\n\n"
        "Выберите, что хотите изменить:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования конкретного текста через универсальный редактор."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from bot.handlers.admin.message_editor import show_message_editor
    
    key = callback.data.split(":")[1]
    
    # Белый список допустимых ключей — защита от инъекции произвольного ключа настроек
    ALLOWED_KEYS = {
        'main',
        'help',
        'prepayment',
        'key_delivery',
    }
    
    if key not in ALLOWED_KEYS:
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    # Тексты справки для каждого ключа
    help_texts = {
        'main': (
            "📝 <b>Справка: Текст главной страницы</b>\n\n"
            "Чтобы изменить текст, вернитесь и просто отправьте боту новое сообщение с нужным текстом.\n"
            "Вы можете прикрепить фото/видео.\n\n"
            "Переменные:\n"
            "• <code>%тарифы%</code> — список тарифов с ценами\n"
            "• <code>%без_тарифов%</code> — не добавлять тарифы"
        ),
        'key_delivery': (
            "📝 <b>Справка: Текст выдачи ключа</b>\n\n"
            "Формат: <b>только текст</b> (без фото).\n\n"
            "Переменные:\n"
            "• <code>%ключ_для_копирования%</code> — ссылка или ключ в моноширинном виде для копирования\n"
            "• <code>%ключ_ссылка%</code> — чистая ссылка без code/pre, кликабельная для HTTP/HTTPS подписки\n"
            "• <code>%ключ_ссылка_url%</code> — URL-кодированная ссылка для URL-кнопок\n\n"
            "Можно использовать один тег или оба сразу."
        ),
    }
    
    current_allowed_types = ['text'] if key == 'key_delivery' else ['text', 'photo', 'video', 'animation']
    
    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback='admin_edit_texts',
        help_text=help_texts.get(key),
        allowed_types=current_allowed_types,
    )
    await callback.answer()


# ============================================================================
# РЕДАКТИРОВАНИЕ КНОПОК-ССЫЛОК (НОВОСТИ, ПОДДЕРЖКА) в JSON страницы help
# ============================================================================

import json

def _get_help_button(btn_id: str) -> dict:
    from database.requests import get_page
    row = get_page('help')
    if not row:
        return {}
    buttons_json = row.get('buttons_custom') or row.get('buttons_default', '[]')
    if not buttons_json:
        buttons_json = '[]'
    try:
        buttons = json.loads(buttons_json)
        for btn in buttons:
            if btn.get('id') == btn_id:
                return btn
    except Exception:
        pass
    return {}

def _update_help_button(btn_id: str, updates: dict) -> None:
    from database.requests import get_page, update_page_custom
    row = get_page('help')
    if not row:
        return
    buttons_json = row.get('buttons_custom') or row.get('buttons_default', '[]')
    if not buttons_json:
        buttons_json = '[]'
    try:
        buttons = json.loads(buttons_json)
        found = False
        for btn in buttons:
            if btn.get('id') == btn_id:
                btn.update(updates)
                found = True
                break
        if found:
            update_page_custom('help', buttons=json.dumps(buttons, ensure_ascii=False))
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error updating help button: {e}")


@router.callback_query(F.data.startswith("edit_link:"))
async def edit_link_menu(callback: CallbackQuery, state: FSMContext):
    """Меню редактирования кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    btn_id = f"btn_{link_type}"
    btn_data = _get_help_button(btn_id)
    
    current_url = btn_data.get('action_value', 'Не задано')
    is_hidden = btn_data.get('is_hidden', False)
    
    # Label хранится с эмодзи '📢 ' или '💬 ', попробуем отрезать, если есть
    raw_label = btn_data.get('label', 'Новости' if link_type == 'news' else 'Поддержка')
    button_name = raw_label[2:] if raw_label.startswith('📢 ') or raw_label.startswith('💬 ') else raw_label
    
    # Названия для заголовка
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    hidden_status = "👁️ Скрыта" if is_hidden else "👁️‍🗨️ Показывается"
    
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(
        text="🔗 Изменить ссылку",
        callback_data=f"edit_link_url:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"{'👁️‍🗨️ Показать' if is_hidden else '👁️ Скрыть'} кнопку",
        callback_data=f"toggle_link_hidden:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"✏️ Название: {button_name}",
        callback_data=f"edit_link_name:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data="admin_edit_texts"
    ))
    
    await safe_edit_or_send(callback.message, 
        f"🔗 <b>Редактирование: {titles[link_type]}</b>\n\n"
        f"📍 <b>Ссылка:</b> <code>{current_url}</code>\n"
        f"🏷 <b>Название кнопки:</b> {button_name}\n"
        f"👀 <b>Статус:</b> {hidden_status}",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_link_url:"))
async def edit_link_url_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования URL ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.keyboards.admin import cancel_kb
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    btn_id = f"btn_{link_type}"
    btn_data = _get_help_button(btn_id)
    current_url = btn_data.get('action_value', 'Не задано')
    
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    await state.set_state(AdminStates.waiting_for_link_url)
    await state.update_data(editing_btn_id=btn_id, return_to=f"edit_link:{link_type}", editing_message=callback.message)
    
    await safe_edit_or_send(callback.message, 
        f"🔗 <b>Изменение ссылки: {titles[link_type]}</b>\n\n"
        f"📜 <b>Текущая ссылка:</b>\n<code>{current_url}</code>\n\n"
        f"👇 Отправьте новую ссылку (должна начинаться с http:// или https://):",
        reply_markup=cancel_kb(f"edit_link:{link_type}")
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_link_url, ~F.text.startswith('/'))
async def edit_link_url_save(message: Message, state: FSMContext):
    """Сохранение новой ссылки."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.keyboards.admin import back_and_home_kb, cancel_kb
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    btn_id = data.get('editing_btn_id')
    return_to = data.get('return_to', 'admin_edit_texts')
    editing_message = data.get('editing_message')
    
    if not btn_id:
        await state.clear()
        await safe_edit_or_send(message, "❌ Ошибка состояния.", force_new=True)
        return
    
    new_value = get_message_text_for_storage(message, 'plain')
    
    # Валидация URL
    if not new_value.startswith(('http://', 'https://')):
        await safe_edit_or_send(message,
            "❌ <b>Ошибка:</b> Ссылка должна начинаться с <code>http://</code> или <code>https://</code>\n\n"
            f"Вы ввели: <code>{new_value}</code>\n\n"
            "Попробуйте ещё раз или нажмите Отмена.",
            reply_markup=cancel_kb(return_to)
        )
        return
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except Exception:
        pass
    
    _update_help_button(btn_id, {'action_type': 'url', 'action_value': new_value})
    await state.clear()
    
    # Перерисовываем сообщение
    if editing_message:
        try:
            await safe_edit_or_send(editing_message,
                f"✅ <b>Ссылка сохранена!</b>\n\n<code>{new_value}</code>",
                reply_markup=back_and_home_kb(return_to)
            )
        except Exception:
            await safe_edit_or_send(message,
                f"✅ <b>Ссылка сохранена!</b>\n\n<code>{new_value}</code>",
                reply_markup=back_and_home_kb(return_to),
                force_new=True
            )
    else:
        await safe_edit_or_send(message,
            f"✅ <b>Ссылка сохранена!</b>\n\n<code>{new_value}</code>",
            reply_markup=back_and_home_kb(return_to),
            force_new=True
        )


@router.callback_query(F.data.startswith("toggle_link_hidden:"))
async def toggle_link_hidden(callback: CallbackQuery, state: FSMContext):
    """Переключение видимости кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    btn_id = f"btn_{link_type}"
    btn_data = _get_help_button(btn_id)
    current_status = btn_data.get('is_hidden', False)
    
    _update_help_button(btn_id, {'is_hidden': not current_status})
    
    # Возвращаемся в меню редактирования ссылки
    await edit_link_menu(callback, state)


@router.callback_query(F.data.startswith("edit_link_name:"))
async def edit_link_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования названия кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.keyboards.admin import cancel_kb
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    btn_id = f"btn_{link_type}"
    btn_data = _get_help_button(btn_id)
    
    raw_label = btn_data.get('label', 'Новости' if link_type == 'news' else 'Поддержка')
    current_name = raw_label[2:] if raw_label.startswith('📢 ') or raw_label.startswith('💬 ') else raw_label
    
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    await state.set_state(AdminStates.waiting_for_link_button_name)
    await state.update_data(editing_btn_id=btn_id, link_type=link_type)
    
    await safe_edit_or_send(callback.message, 
        f"✏️ <b>Изменение названия кнопки: {titles[link_type]}</b>\n\n"
        f"🏷 <b>Текущее название:</b> {current_name}\n\n"
        f"👇 Отправьте новое название для кнопки (максимум 30 символов):",
        reply_markup=cancel_kb(f"edit_link:{link_type}")
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_link_button_name)
async def edit_link_name_save(message: Message, state: FSMContext):
    """Сохранение нового названия кнопки-ссылки."""
    from bot.keyboards.admin import back_and_home_kb
    
    data = await state.get_data()
    btn_id = data.get('editing_btn_id')
    link_type = data.get('link_type')
    
    if not btn_id:
        await state.clear()
        await safe_edit_or_send(message, "❌ Ошибка состояния.", force_new=True)
        return
    
    from bot.utils.text import get_message_text_for_storage
    
    new_name = get_message_text_for_storage(message, 'plain')[:30]
    
    if len(new_name) < 1:
        await safe_edit_or_send(message,
            "❌ <b>Название не может быть пустым</b>\n\n"
            "Попробуйте ещё раз или нажмите Отмена.",
            reply_markup=back_and_home_kb(f"edit_link:{link_type}" if link_type else "admin_edit_texts")
        )
        return
    
    new_label = f"📢 {new_name}" if link_type == 'news' else f"💬 {new_name}"
    _update_help_button(btn_id, {'label': new_label})
    
    await state.clear()
    
    await safe_edit_or_send(message,
        f"✅ <b>Название сохранено!</b>\n\n{new_name}",
        reply_markup=back_and_home_kb(f"edit_link:{link_type}" if link_type else "admin_edit_texts"),
        force_new=True
    )




# ============================================================================
# ОСТАНОВКА БОТА
# ============================================================================

@router.callback_query(F.data == "admin_stop_bot")
async def show_stop_bot_confirm(callback: CallbackQuery, state: FSMContext):
    """Показывает окно подтверждения остановки бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await safe_edit_or_send(callback.message, 
        "🛑 <b>Остановка бота</b>\n\n"
        "Вы уверены, что хотите остановить бот?\n\n"
        "⚠️ Бот перестанет отвечать на сообщения пользователей "
        "до следующего ручного запуска.",
        reply_markup=stop_bot_confirm_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stop_bot_confirm")
async def stop_bot_confirmed(callback: CallbackQuery, state: FSMContext):
    """Подтверждение остановки бота — останавливает polling."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await safe_edit_or_send(callback.message, 
        "🛑 <b>Бот останавливается...</b>\n\n"
        "Спасибо за использование!"
    )
    await callback.answer("Бот останавливается...", show_alert=True)
    
    logger.info(f"🛑 Бот остановлен администратором {callback.from_user.id}")
    
    # Даём время на отправку сообщения
    await asyncio.sleep(1)
    
    # Завершаем работу скрипта
    sys.exit(0)


# ============================================================================
# СКАЧИВАНИЕ ЛОГОВ
# ============================================================================

@router.callback_query(F.data == "admin_logs_menu")
async def show_logs_menu(callback: CallbackQuery, state: FSMContext):
    """Меню скачивания логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    await safe_edit_or_send(callback.message, 
        "📥 <b>Скачивание логов</b>\n\n"
        "Выберите какие логи хотите скачать:",
        reply_markup=admin_logs_menu_kb()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_download_log_full")
async def download_log_full(callback: CallbackQuery, state: FSMContext):
    """Скачивание полного лога."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    log_path = "logs/bot.log"
    if not os.path.exists(log_path):
        await callback.answer("Файл логов не найден.", show_alert=True)
        return
    
    # Отвечаем на коллбек до отправки файла, чтобы избежать таймаута
    await callback.answer()
    
    await callback.message.answer_document(
        document=FSInputFile(log_path, filename="bot.log"),
        caption="📄 Полный лог бота"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_download_log_errors")
async def download_log_errors(callback: CallbackQuery, state: FSMContext):
    """Скачивание лога с ошибками."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    log_path = "logs/bot.log"
    error_log_path = "logs/errors.log"
    
    if not os.path.exists(log_path):
        await callback.answer("Файл логов не найден.", show_alert=True)
        return
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f_in, open(error_log_path, 'w', encoding='utf-8') as f_out:
            capturing = False
            for line in f_in:
                # Начало новой записи в логе формата [2026-...
                if line.startswith('['):
                    if ' [ERROR] ' in line or ' [WARNING] ' in line or ' [CRITICAL] ' in line or ' [EXCEPTION] ' in line:
                        capturing = True
                        f_out.write(line)
                    else:
                        capturing = False
                elif capturing:
                    # Строки traceback
                    f_out.write(line)
    except Exception as e:
        logger.error(f"Ошибка при формировании лога ошибок: {e}")
        await callback.answer("Ошибка при обработке логов.", show_alert=True)
        return
    
    if not os.path.exists(error_log_path) or os.path.getsize(error_log_path) == 0:
        await callback.answer("Ошибок не найдено! 🎉", show_alert=True)
        return
    
    # Отвечаем на коллбек до отправки файла, чтобы избежать таймаута
    await callback.answer()
        
    await callback.message.answer_document(
        document=FSInputFile(error_log_path, filename="errors.log"),
        caption="⚠️ Лог ошибок и предупреждений"
    )


@router.callback_query(F.data == "admin_send_log_to_yadreno")
async def send_log_to_yadreno_admin(callback: CallbackQuery, state: FSMContext):
    """Отправляет bot.log в Yadreno Admin для анализа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    api_key = get_yadreno_admin_api_key()
    if not api_key:
        await callback.answer()
        await safe_edit_or_send(
            callback.message,
            "🤖 <b>Yadreno Admin</b>\n\nСначала укажите api_key.",
            reply_markup=yadreno_admin_no_key_kb(),
        )
        return

    log_path = Path("logs/bot.log")
    if not log_path.exists():
        await callback.answer("Файл логов не найден.", show_alert=True)
        return

    await callback.answer()
    from bot.handlers.admin.yadreno_admin import (
        _YadrenoProgressRenderer,
        _activate_yadreno_chat_lane,
        _format_final_response,
    )

    await _activate_yadreno_chat_lane(state, YADRENO_ADMIN_CHAT_TOPIC_ID)
    status_message = await safe_edit_or_send(
        callback.message,
        "🤖 <b>Yadreno Admin</b>\n\n⏳ Отправляю bot.log и запускаю анализ...",
        reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_CHAT_TOPIC_ID),
    )

    progress = _YadrenoProgressRenderer(
        status_message,
        topic_id=YADRENO_ADMIN_CHAT_TOPIC_ID,
    )
    try:
        final = await run_dialog_with_uploads(
            callback.from_user.id,
            api_key,
            (
                "Проанализируй приложенный полный лог bot.log. "
                "Найди ошибки, предупреждения, вероятные причины и предложи конкретные действия."
            ),
            [YadrenoAdminUpload(log_path, "bot.log", "text/plain")],
            topic_id=YADRENO_ADMIN_CHAT_TOPIC_ID,
            progress_callback=progress.handle,
        )
        await safe_edit_or_send(
            progress.final_target,
            _format_final_response(final.content, final.viewer_url),
            reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_CHAT_TOPIC_ID),
        )
    except YadrenoAdminError as e:
        await safe_edit_or_send(
            progress.final_target,
            f"❌ <b>Yadreno Admin недоступен</b>\n\n{escape_html(str(e))}",
            reply_markup=yadreno_admin_agent_kb(YADRENO_ADMIN_CHAT_TOPIC_ID),
        )


@router.callback_query(F.data == "admin_clear_logs_confirm")
async def confirm_clear_logs(callback: CallbackQuery, state: FSMContext):
    """Показывает предупреждение перед очисткой логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.keyboards.admin import back_button
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да, очистить", callback_data="admin_clear_logs_do"))
    builder.row(back_button("admin_logs_menu"))
    
    await safe_edit_or_send(callback.message,
        "🧹 <b>Очистка логов</b>\n\n"
        "Вы уверены, что хотите полностью стереть старые файлы логов и очистить текущие <code>bot.log</code> и <code>errors.log</code>?\n"
        "Это безвозвратное действие.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_clear_logs_do")
async def do_clear_logs(callback: CallbackQuery, state: FSMContext):
    """Очищает файлы логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    try:
        import glob
        
        # Очищаем текущие файлы
        for log_path in ["logs/bot.log", "logs/errors.log"]:
            if os.path.exists(log_path):
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.write("") 
                    
        # Удаляем старые лог-файлы (bot.log.1, bot.log.2, и т.д.)
        for old_log in glob.glob("logs/bot.log.*"):
            if os.path.exists(old_log):
                try:
                    os.remove(old_log)
                except Exception as e:
                    logger.error(f"Не удалось удалить старый лог {old_log}: {e}")
                
        await callback.answer("🧹 Логи успешно очищены!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при очистке логов: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    
    await show_logs_menu(callback, state)
