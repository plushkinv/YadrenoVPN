"""
Обработчики раздела «Настройки бота».

Управление обновлением, остановкой бота и редактированием текстов.
"""
import asyncio
import logging
import sys
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

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
    get_recent_commits,
    restart_bot,
)
from bot.keyboards.admin import (
    bot_settings_kb,
    update_check_result_kb,
    update_confirm_kb,
    stop_bot_confirm_kb,
    back_and_home_kb,
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ГЛАВНОЕ МЕНЮ НАСТРОЕК
# ============================================================================

@router.callback_query(F.data == "admin_bot_settings")
async def show_bot_settings(callback: CallbackQuery, state: FSMContext):
    """Показывает меню настроек бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Информация о текущей версии
    commit = get_current_commit() or "неизвестно"
    branch = get_current_branch() or "неизвестно"
    
    # Проверяем настроен ли GitHub
    github_status = "✅ Настроен" if GITHUB_REPO_URL else "❌ Не настроен"
    
    text = (
        "⚙️ *Настройки бота*\n\n"
        f"📌 Версия: `{commit}`\n"
        f"🌿 Ветка: `{branch}`\n"
        f"🔗 GitHub: {github_status}\n\n"
        "Выберите действие:"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=bot_settings_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ПРОВЕРКА ОБНОВЛЕНИЙ
# ============================================================================

@router.callback_query(F.data == "admin_check_updates")
async def check_updates_handler(callback: CallbackQuery, state: FSMContext):
    """Проверяет наличие обновлений на GitHub."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем наличие git
    if not check_git_available():
        await callback.message.edit_text(
            "❌ *Ошибка*\n\n"
            "Git не установлен или не найден в PATH.\n"
            "Установите Git для использования автообновления.",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Проверяем настроен ли GitHub URL
    if not GITHUB_REPO_URL:
        await callback.message.edit_text(
            "❌ *GitHub не настроен*\n\n"
            "Укажите URL репозитория в файле `config.py`:\n"
            "`GITHUB_REPO_URL = \"https://github.com/user/repo.git\"`",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        success, msg = set_remote_url(GITHUB_REPO_URL)
        if not success:
            await callback.message.edit_text(
                f"❌ *Ошибка настройки remote*\n\n{msg}",
                reply_markup=back_and_home_kb("admin_bot_settings"),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
    
    # Показываем сообщение о проверке
    await callback.message.edit_text(
        "🔍 *Проверка обновлений...*\n\n"
        "Подключаюсь к GitHub...",
        parse_mode="Markdown"
    )
    
    # Проверяем обновления
    success, commits_behind, log_text = check_for_updates()
    
    if not success:
        await callback.message.edit_text(
            f"❌ *Ошибка проверки*\n\n{log_text}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    has_updates = commits_behind > 0
    
    await callback.message.edit_text(
        f"🔍 *Результат проверки*\n\n{log_text}",
        reply_markup=update_check_result_kb(has_updates),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОБНОВЛЕНИЕ БОТА
# ============================================================================

@router.callback_query(F.data == "admin_update_bot")
async def show_update_confirm(callback: CallbackQuery, state: FSMContext):
    """Показывает подтверждение обновления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем настроен ли GitHub
    if not GITHUB_REPO_URL:
        await callback.message.edit_text(
            "❌ *GitHub не настроен*\n\n"
            "Укажите URL репозитория в файле `config.py`:\n"
            "`GITHUB_REPO_URL = \"https://github.com/user/repo.git\"`",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    # Показываем сообщение о проверке
    await callback.message.edit_text(
        "🔍 *Проверка обновлений...*\n\n"
        "Подключаюсь к GitHub...",
        parse_mode="Markdown"
    )
    
    # Проверяем наличие обновлений
    success, commits_behind, log_text = check_for_updates()
    
    if not success:
        await callback.message.edit_text(
            f"❌ *Ошибка проверки*\n\n{log_text}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    commit = get_current_commit() or "неизвестно"
    recent = get_recent_commits(3)
    
    # Если обновлений нет
    if commits_behind == 0:
        await callback.message.edit_text(
            "✅ *Обновление не требуется, у вас последняя версия*\n\n"
            f"Текущая версия: `{commit}`\n\n"
            f"Последние коммиты:\n```\n{recent}\n```",
            reply_markup=update_confirm_kb(has_updates=False),
            parse_mode="Markdown"
        )
    else:
        # Есть обновления
        await callback.message.edit_text(
            "🔄 *Обновление бота*\n\n"
            f"Текущая версия: `{commit}`\n\n"
            f"Последние коммиты:\n```\n{recent}\n```\n\n"
            "⚠️ После обновления бот автоматически перезапустится.\n"
            "Это займёт несколько секунд.",
            reply_markup=update_confirm_kb(has_updates=True),
            parse_mode="Markdown"
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
    
    await callback.message.edit_text(
        "🔄 *Обновление...*\n\n"
        "Загружаю изменения с GitHub...",
        parse_mode="Markdown"
    )
    
    # Выполняем git pull
    success, message = pull_updates()
    
    if not success:
        await callback.message.edit_text(
            f"❌ *Ошибка обновления*\n\n{message}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Успешное обновление - показываем лог и перезапускаем
    logger.info(f"🔄 Бот обновлён администратором {callback.from_user.id}")
    
    await callback.message.edit_text(
        f"✅ *Обновление завершено!*\n\n{message}\n\n"
        "🔄 Перезапуск бота через 2 секунды...",
        parse_mode="Markdown"
    )
    await callback.answer("Бот перезапускается...", show_alert=True)
    
    # Даём время на отправку сообщения
    await asyncio.sleep(2)
    
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
    
    builder.row(InlineKeyboardButton(text="📝 Главная страница", callback_data="edit_text:main_page_text"))
    builder.row(InlineKeyboardButton(text="📝 Справка (текст)", callback_data="edit_text:help_page_text"))
    builder.row(InlineKeyboardButton(text="📢 Ссылка: Новости", callback_data="edit_text:news_channel_link"))
    builder.row(InlineKeyboardButton(text="💬 Ссылка: Поддержка", callback_data="edit_text:support_channel_link"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_bot_settings"))
    
    await callback.message.edit_text(
        "✏️ *Редактирование текстов*\n\n"
        "Выберите, что хотите изменить:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования конкретного текста."""
    from database.requests import get_setting
    from bot.keyboards.admin import cancel_kb
    
    key = callback.data.split(":")[1]
    
    # Названия для заголовка
    titles = {
        'main_page_text': 'Текст главной страницы',
        'help_page_text': 'Текст страницы справки',
        'news_channel_link': 'Ссылка на канал новостей',
        'support_channel_link': 'Ссылка на чат поддержки',
    }
    
    current_value = get_setting(key, "Не задано")
    
    # Спец. сообщение для новостей (реклама)
    ad_text = ""
    if key == 'news_channel_link':
        ad_text = (
            "\n\n📢 *Прокачай свой канал с @Ya\_FooterBot*\n\n"
            "Автоматические подписи в три клика:\n"
            "• 🔄 Автоматическая ротация подписей\n"
            "• ⏱ Удаление постов по таймеру\n"
            "• 📈 Курсы валют и биржевые сводки\n\n"
            "Легко, быстро, эффективно!"
        )
    
    await state.set_state(AdminStates.waiting_for_text)
    await state.update_data(editing_key=key)
    
    await callback.message.edit_text(
        f"✏️ *Редактирование: {titles.get(key, key)}*\n\n"
        f"📜 *Текущее значение:*\n"
        f"```\n{current_value}\n```\n\n"
        f"👇 Отправьте новое значение сообщением (или нажмите Отмена).{ad_text}",
        reply_markup=cancel_kb("admin_edit_texts"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_text)
async def edit_text_save(message: Message, state: FSMContext):
    """Сохранение нового значения текста."""
    from database.requests import set_setting
    from bot.keyboards.admin import back_and_home_kb, cancel_kb
    
    data = await state.get_data()
    key = data.get('editing_key')
    
    if not key:
        await state.clear()
        await message.answer("❌ Ошибка состояния.")
        return
    
    # Для ссылок используем сырой текст, для остальных — md_text (чтобы сохранить форматирование)
    if key in ('news_channel_link', 'support_channel_link'):
        new_value = message.text.strip()
    else:
        # md_text экранирует для MarkdownV2
        new_value = message.md_text.strip() if message.md_text else message.text.strip()
    
    # Валидация для ссылок: должны начинаться с http:// или https://
    if key in ('news_channel_link', 'support_channel_link'):
        if not new_value.startswith(('http://', 'https://')):
            await message.answer(
                "❌ *Ошибка:* Ссылка должна начинаться с `http://` или `https://`\n\n"
                f"Вы ввели: `{new_value}`\n\n"
                "Попробуйте ещё раз или нажмите Отмена.",
                reply_markup=cancel_kb("admin_edit_texts"),
                parse_mode="Markdown"
            )
            return
    
    # Сохраняем
    set_setting(key, new_value)
    
    await state.clear()
    
    await message.answer(
        f"✅ *Значение сохранено!*\n\n{new_value}",
        reply_markup=back_and_home_kb("admin_edit_texts"),
        parse_mode="Markdown"
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
    
    await callback.message.edit_text(
        "🛑 *Остановка бота*\n\n"
        "Вы уверены, что хотите остановить бот?\n\n"
        "⚠️ Бот перестанет отвечать на сообщения пользователей "
        "до следующего ручного запуска.",
        reply_markup=stop_bot_confirm_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stop_bot_confirm")
async def stop_bot_confirmed(callback: CallbackQuery, state: FSMContext):
    """Подтверждение остановки бота — останавливает polling."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🛑 *Бот останавливается...*\n\n"
        "Спасибо за использование!",
        parse_mode="Markdown"
    )
    await callback.answer("Бот останавливается...", show_alert=True)
    
    logger.info(f"🛑 Бот остановлен администратором {callback.from_user.id}")
    
    # Даём время на отправку сообщения
    await asyncio.sleep(1)
    
    # Завершаем работу скрипта
    sys.exit(0)
