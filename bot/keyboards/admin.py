"""
Клавиатуры для админ-панели.

Inline-клавиатуры для всех экранов администратора.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional


# ============================================================================
# НАВИГАЦИЯ
# ============================================================================

def back_button(callback: str = "back") -> InlineKeyboardButton:
    """Кнопка 'Назад'."""
    return InlineKeyboardButton(text="⬅️ Назад", callback_data=callback)


def home_button() -> InlineKeyboardButton:
    """Кнопка 'На главную'."""
    return InlineKeyboardButton(text="🈴 На главную", callback_data="start")


def cancel_button() -> InlineKeyboardButton:
    """Кнопка 'Отмена'."""
    return InlineKeyboardButton(text="❌ Отмена", callback_data="admin_servers")


def cancel_kb(callback_data: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Отмена'."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data))
    return builder.as_markup()


def back_and_home_kb(back_callback: str = "back") -> InlineKeyboardMarkup:
    """Клавиатура с кнопками 'Назад' и 'На главную'."""
    builder = InlineKeyboardBuilder()
    builder.row(back_button(back_callback), home_button())
    return builder.as_markup()


def home_only_kb() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой 'На главную'."""
    builder = InlineKeyboardBuilder()
    builder.row(home_button())
    return builder.as_markup()


# ============================================================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# ============================================================================

def admin_main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню админ-панели."""
    builder = InlineKeyboardBuilder()
    
    # Основные разделы
    builder.row(
        InlineKeyboardButton(text="🖥️ Сервера", callback_data="admin_servers")
    )
    builder.row(
        InlineKeyboardButton(text="💳 Оплаты", callback_data="admin_payments"),
        InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")
    )
    builder.row(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
    )
    
    # Настройки бота (обновление, остановка, тексты)
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="admin_bot_settings")
    )
    
    # На главную
    builder.row(home_button())
    
    return builder.as_markup()


def stop_bot_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения остановки бота."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text="✅ Точно остановить",
            callback_data="admin_stop_bot_confirm"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="admin_bot_settings"
        )
    )
    
    return builder.as_markup()


# ============================================================================
# НАСТРОЙКИ БОТА
# ============================================================================

def bot_settings_kb(has_updates: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура раздела 'Настройки бота'.
    
    Args:
        has_updates: Есть ли доступные обновления
    """
    builder = InlineKeyboardBuilder()
    
    # Проверка обновлений
    update_text = "🔍 Проверить обновления"
    if has_updates:
        update_text = "📦 Есть обновления!"
    builder.row(
        InlineKeyboardButton(text=update_text, callback_data="admin_check_updates")
    )
    
    # Обновить бота
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить бота", callback_data="admin_update_bot")
    )
    
    # Изменить тексты (заглушка)
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить тексты", callback_data="admin_edit_texts")
    )
    
    # Остановить бота
    builder.row(
        InlineKeyboardButton(text="🛑 Остановить бота", callback_data="admin_stop_bot")
    )
    
    # Навигация
    builder.row(back_button("admin_panel"), home_button())
    
    return builder.as_markup()


def update_check_result_kb(has_updates: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура результата проверки обновлений.
    
    Args:
        has_updates: Есть ли доступные обновления
    """
    builder = InlineKeyboardBuilder()
    
    if has_updates:
        builder.row(
            InlineKeyboardButton(
                text="🔄 Обновить и перезапустить",
                callback_data="admin_update_bot_confirm"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="🔍 Проверить снова", callback_data="admin_check_updates")
    )
    
    # Навигация
    builder.row(back_button("admin_bot_settings"), home_button())
    
    return builder.as_markup()


def update_confirm_kb(has_updates: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения обновления бота."""
    builder = InlineKeyboardBuilder()
    
    if has_updates:
        builder.row(
            InlineKeyboardButton(
                text="✅ Обновить и перезапустить",
                callback_data="admin_update_bot_confirm"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="admin_bot_settings"
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="admin_bot_settings"
            )
        )
    
    return builder.as_markup()


# ============================================================================
# СПИСОК СЕРВЕРОВ
# ============================================================================

def servers_list_kb(servers: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Клавиатура списка серверов.
    
    Args:
        servers: Список серверов из БД
    """
    builder = InlineKeyboardBuilder()
    
    # Кнопка обновления
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_servers_refresh")
    )
    
    # Кнопка добавления
    builder.row(
        InlineKeyboardButton(text="➕ Добавить сервер", callback_data="admin_server_add")
    )
    
    # Кнопки серверов
    for server in servers:
        status_emoji = "🟢" if server.get('is_active') else "🔴"
        text = f"{status_emoji} {server['name']}"
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"admin_server_view:{server['id']}"
            )
        )
    
    # Навигация
    builder.row(back_button("admin_panel"), home_button())
    
    return builder.as_markup()


# ============================================================================
# ПРОСМОТР СЕРВЕРА
# ============================================================================

def server_view_kb(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура просмотра сервера.
    
    Args:
        server_id: ID сервера
        is_active: Активен ли сервер
    """
    builder = InlineKeyboardBuilder()
    
    # Редактирование
    builder.row(
        InlineKeyboardButton(
            text="✏️ Изменить настройки",
            callback_data=f"admin_server_edit:{server_id}"
        )
    )
    
    # Активация/деактивация
    if is_active:
        toggle_text = "⏸️ Деактивировать"
    else:
        toggle_text = "🔄 Активировать"
    
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"admin_server_toggle:{server_id}"
        )
    )
    
    # Удаление
    builder.row(
        InlineKeyboardButton(
            text="🗑️ Удалить сервер",
            callback_data=f"admin_server_delete:{server_id}"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_servers"), home_button())
    
    return builder.as_markup()


# ============================================================================
# ДОБАВЛЕНИЕ СЕРВЕРА
# ============================================================================

def add_server_step_kb(step: int, total_steps: int = 6) -> InlineKeyboardMarkup:
    """
    Клавиатура для шага добавления сервера.
    
    Args:
        step: Текущий шаг (1-6)
        total_steps: Общее количество шагов
    """
    builder = InlineKeyboardBuilder()
    
    buttons = []
    
    # Кнопка "Назад" (кроме первого шага)
    if step > 1:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_server_add_back")
        )
    
    # Кнопка "Отмена"
    buttons.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_servers")
    )
    
    builder.row(*buttons)
    
    return builder.as_markup()


def add_server_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения добавления сервера."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="admin_server_add_save")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_server_add_back"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_servers")
    )
    
    return builder.as_markup()


def add_server_test_failed_kb() -> InlineKeyboardMarkup:
    """Клавиатура при неудачной проверке подключения."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔄 Проверить снова", callback_data="admin_server_add_test")
    )
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить всё равно", callback_data="admin_server_add_save")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_server_add_back"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_servers")
    )
    
    return builder.as_markup()


# ============================================================================
# РЕДАКТИРОВАНИЕ СЕРВЕРА
# ============================================================================

def edit_server_kb(current_param: int, total_params: int = 6) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования сервера с навигацией.
    
    Args:
        current_param: Индекс текущего параметра (0-5)
        total_params: Общее количество параметров
    """
    builder = InlineKeyboardBuilder()
    
    # Навигация (Всегда 2 кнопки в ряду)
    nav_buttons = []
    
    # Кнопка "Пред." или заглушка
    if current_param > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Пред.", callback_data="admin_server_edit_prev")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    # Кнопка "След." или заглушка
    if current_param < total_params - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ След.", callback_data="admin_server_edit_next")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    builder.row(*nav_buttons)
    
    # Кнопка "Готово"
    builder.row(
        InlineKeyboardButton(text="✅ Готово", callback_data="admin_server_edit_done")
    )
    
    return builder.as_markup()


# ============================================================================
# УДАЛЕНИЕ СЕРВЕРА
# ============================================================================

def confirm_delete_kb(server_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления сервера."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"admin_server_delete_confirm:{server_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"admin_server_view:{server_id}"
        )
    )
    
    return builder.as_markup()


# ============================================================================
# РАЗДЕЛ «ОПЛАТЫ»
# ============================================================================

def payments_menu_kb(stars_enabled: bool, crypto_enabled: bool) -> InlineKeyboardMarkup:
    """
    Главное меню раздела оплат.
    
    Args:
        stars_enabled: Включены ли Telegram Stars
        crypto_enabled: Включены ли крипто-платежи
    """
    builder = InlineKeyboardBuilder()
    
    # Toggle для Stars
    stars_status = "✅" if stars_enabled else "❌"
    builder.row(
        InlineKeyboardButton(
            text=f"⭐ Telegram Stars: {stars_status}",
            callback_data="admin_payments_toggle_stars"
        )
    )
    
    # Toggle для Crypto (без отдельной кнопки настроек)
    crypto_status = "✅" if crypto_enabled else "❌"
    builder.row(
        InlineKeyboardButton(
            text=f"💰 Крипто-платежи: {crypto_status}",
            callback_data="admin_payments_toggle_crypto"
        )
    )
    
    # Тарифы
    builder.row(
        InlineKeyboardButton(
            text="📋 Тарифы",
            callback_data="admin_tariffs"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_panel"), home_button())
    
    return builder.as_markup()


def crypto_setup_kb(step: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для шага настройки крипто-платежей.
    
    Args:
        step: Текущий шаг (1 = ссылка, 2 = ключ)
    """
    builder = InlineKeyboardBuilder()
    
    buttons = []
    
    if step > 1:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_crypto_setup_back")
        )
    
    buttons.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_payments")
    )
    
    builder.row(*buttons)
    
    return builder.as_markup()


def crypto_setup_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения настройки крипто."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить и включить", callback_data="admin_crypto_setup_save")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_crypto_setup_back"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_payments")
    )
    
    return builder.as_markup()


def edit_crypto_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования крипто-настроек с навигацией.
    
    Args:
        current_param: Индекс текущего параметра
        total_params: Общее количество параметров
    """
    builder = InlineKeyboardBuilder()
    
    # Навигация (Всегда 2 кнопки в ряду)
    nav_buttons = []
    
    # Кнопка "Пред." или заглушка
    if current_param > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Пред.", callback_data="admin_crypto_edit_prev")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    # Кнопка "След." или заглушка
    if current_param < total_params - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ След.", callback_data="admin_crypto_edit_next")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    builder.row(*nav_buttons)
    
    # Кнопка "Готово"
    builder.row(
        InlineKeyboardButton(text="✅ Готово", callback_data="admin_crypto_edit_done")
    )
    
    return builder.as_markup()


def crypto_management_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    """
    Меню управления крипто-платежами.
    
    Args:
        is_enabled: Включены ли крипто-платежи сейчас
    """
    builder = InlineKeyboardBuilder()
    
    # Toggle включения/выключения
    status_text = "🟢 Выключить" if is_enabled else "⚪ Включить"
    builder.row(
        InlineKeyboardButton(
            text=status_text,
            callback_data="admin_crypto_mgmt_toggle"
        )
    )
    
    # Изменить ссылку на товар
    builder.row(
        InlineKeyboardButton(
            text="🔗 Изменить ссылку на товар",
            callback_data="admin_crypto_mgmt_edit_url"
        )
    )
    
    # Изменить секретный ключ
    builder.row(
        InlineKeyboardButton(
            text="🔐 Изменить секретный ключ",
            callback_data="admin_crypto_mgmt_edit_secret"
        )
    )
    
    # Назад и На главную
    builder.row(back_button("admin_payments"), home_button())
    
    return builder.as_markup()


# ============================================================================
# ТАРИФЫ
# ============================================================================

def tariffs_list_kb(tariffs: List[Dict[str, Any]], include_hidden: bool = True) -> InlineKeyboardMarkup:
    """
    Клавиатура списка тарифов.
    
    Args:
        tariffs: Список тарифов из БД
        include_hidden: Показывать скрытые тарифы
    """
    builder = InlineKeyboardBuilder()
    
    # Кнопка добавления
    builder.row(
        InlineKeyboardButton(text="➕ Добавить тариф", callback_data="admin_tariff_add")
    )
    
    # Кнопки тарифов
    for tariff in tariffs:
        status_emoji = "🟢" if tariff.get('is_active') else "🔴"
        price = tariff['price_cents'] / 100
        text = f"{status_emoji} {tariff['name']} — ${price:.2f}"
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"admin_tariff_view:{tariff['id']}"
            )
        )
    
    # Навигация
    builder.row(back_button("admin_payments"), home_button())
    
    return builder.as_markup()


def tariff_view_kb(tariff_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура просмотра тарифа.
    
    Args:
        tariff_id: ID тарифа
        is_active: Активен ли тариф
    """
    builder = InlineKeyboardBuilder()
    
    # Редактирование
    builder.row(
        InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=f"admin_tariff_edit:{tariff_id}"
        )
    )
    
    # Скрыть/показать
    if is_active:
        toggle_text = "👁️‍🗨️ Скрыть"
    else:
        toggle_text = "👁️ Показать"
    
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"admin_tariff_toggle:{tariff_id}"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_tariffs"), home_button())
    
    return builder.as_markup()


def add_tariff_step_kb(step: int, total_steps: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для шага добавления тарифа.
    
    Args:
        step: Текущий шаг (1-N)
        total_steps: Общее количество шагов
    """
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_tariffs")
    )
    
    return builder.as_markup()


def add_tariff_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения добавления тарифа."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="admin_tariff_add_save")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_tariffs")
    )
    
    return builder.as_markup()


def edit_tariff_kb(current_param: int, total_params: int) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования тарифа с навигацией.
    
    Args:
        current_param: Индекс текущего параметра
        total_params: Общее количество параметров
    """
    builder = InlineKeyboardBuilder()
    
    # Навигация (Всегда 2 кнопки в ряду)
    nav_buttons = []
    
    # Кнопка "Пред." или заглушка
    if current_param > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Пред.", callback_data="admin_tariff_edit_prev")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    # Кнопка "След." или заглушка
    if current_param < total_params - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ След.", callback_data="admin_tariff_edit_next")
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(text="—", callback_data="noop")
        )
    
    builder.row(*nav_buttons)
    
    # Кнопка "Готово"
    builder.row(
        InlineKeyboardButton(text="✅ Готово", callback_data="admin_tariff_edit_done")
    )
    
    return builder.as_markup()


# ============================================================================
# РАССЫЛКА
# ============================================================================

# Названия фильтров для отображения
BROADCAST_FILTERS = {
    'all': '👤 Все пользователи',
    'active': '✅ С активными ключами',
    'inactive': '❌ Без активных ключей',
    'never_paid': '🆕 Никогда не покупали',
    'expired': '🚫 Ключ истёк',
}


def broadcast_main_kb(
    has_message: bool,
    current_filter: str,
    broadcast_in_progress: bool,
    user_count: int
) -> InlineKeyboardMarkup:
    """
    Главное меню рассылки.
    
    Args:
        has_message: Есть ли сохранённое сообщение
        current_filter: Текущий выбранный фильтр
        broadcast_in_progress: Идёт ли рассылка сейчас
        user_count: Количество пользователей по текущему фильтру
    """
    builder = InlineKeyboardBuilder()
    
    # === Блок сообщения ===
    msg_status = "✅" if has_message else "❌"
    builder.row(
        InlineKeyboardButton(
            text=f"✉️ Сообщение: {msg_status}",
            callback_data="broadcast_edit_message"
        ),
        InlineKeyboardButton(
            text="👁️ Превью",
            callback_data="broadcast_preview"
        )
    )
    
    # === Фильтры (радио-кнопки) ===
    for filter_key, filter_name in BROADCAST_FILTERS.items():
        radio = "🔘" if filter_key == current_filter else "⚪"
        builder.row(
            InlineKeyboardButton(
                text=f"{radio} {filter_name}",
                callback_data=f"broadcast_filter:{filter_key}"
            )
        )
    
    # === Кнопка запуска рассылки ===
    if broadcast_in_progress:
        builder.row(
            InlineKeyboardButton(
                text="⏳ Рассылка в процессе...",
                callback_data="broadcast_in_progress"
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=f"🚀 Начать рассылку ({user_count} чел.)",
                callback_data="broadcast_start"
            )
        )
    
    # === Разделитель ===
    builder.row(
        InlineKeyboardButton(text="─────────────────", callback_data="noop")
    )
    
    # === Автоуведомления ===
    builder.row(
        InlineKeyboardButton(
            text="⏰ Настройки автоуведомлений",
            callback_data="broadcast_notifications"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_panel"), home_button())
    
    return builder.as_markup()


def broadcast_confirm_kb(user_count: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения рассылки."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=f"✅ Да, разослать ({user_count} чел.)",
            callback_data="broadcast_confirm"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="admin_broadcast"
        )
    )
    
    return builder.as_markup()


def broadcast_notifications_kb(days: int) -> InlineKeyboardMarkup:
    """Клавиатура настройки автоуведомлений."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=f"📅 За сколько дней: {days}",
            callback_data="broadcast_notify_days"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📝 Текст уведомления",
            callback_data="broadcast_notify_text"
        )
    )
    
    builder.row(back_button("admin_broadcast"), home_button())
    
    return builder.as_markup()


def broadcast_back_kb() -> InlineKeyboardMarkup:
    """Клавиатура возврата к рассылке."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast")
    )
    return builder.as_markup()


def broadcast_notify_back_kb() -> InlineKeyboardMarkup:
    """Клавиатура возврата к настройкам уведомлений."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_notifications")
    )
    return builder.as_markup()


# ============================================================================
# РАЗДЕЛ «ПОЛЬЗОВАТЕЛИ»
# ============================================================================

# Фильтры пользователей (такие же как в рассылке)
USERS_FILTERS = {
    'all': '👤 Все',
    'active': '✅ Активные',
    'inactive': '❌ Неактивные',
    'never_paid': '🆕 Новые',
    'expired': '🚫 Истёкшие',
}


def users_menu_kb(stats: Dict[str, int]) -> InlineKeyboardMarkup:
    """
    Главное меню раздела пользователей.
    
    Args:
        stats: Статистика пользователей по фильтрам
    """
    builder = InlineKeyboardBuilder()
    
    # Кнопка "Все пользователи"
    builder.row(
        InlineKeyboardButton(
            text=f"📋 Все пользователи ({stats.get('total', 0)})",
            callback_data="admin_users_list"
        )
    )
    
    # Кнопка "Выбрать пользователя"
    builder.row(
        InlineKeyboardButton(
            text="🔍 Выбрать пользователя",
            callback_data="admin_users_select"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_panel"), home_button())
    
    return builder.as_markup()


def users_list_kb(
    users: List[Dict[str, Any]], 
    page: int, 
    total_pages: int,
    current_filter: str = 'all'
) -> InlineKeyboardMarkup:
    """
    Клавиатура списка пользователей с пагинацией и фильтрами.
    
    Args:
        users: Список пользователей на текущей странице
        page: Номер текущей страницы (начиная с 0)
        total_pages: Общее количество страниц
        current_filter: Текущий фильтр
    """
    builder = InlineKeyboardBuilder()
    
    # Фильтры в одну строку
    filter_buttons = []
    for filter_key, filter_name in USERS_FILTERS.items():
        # Выделяем активный фильтр
        text = f"🔹{filter_name}" if filter_key == current_filter else filter_name
        filter_buttons.append(
            InlineKeyboardButton(
                text=text,
                callback_data=f"admin_users_filter:{filter_key}"
            )
        )
    # Разбиваем на 2 ряда по 2-3 кнопки
    builder.row(*filter_buttons[:3])
    builder.row(*filter_buttons[3:])
    
    # Список пользователей
    for user in users:
        username = user.get('username')
        telegram_id = user.get('telegram_id')
        
        if username:
            text = f"@{username}"
        else:
            text = f"ID: {telegram_id}"
        
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"admin_user_view:{telegram_id}"
            )
        )
    
    # Пагинация
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(text="◀️", callback_data=f"admin_users_page:{page - 1}")
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(text="▶️", callback_data=f"admin_users_page:{page + 1}")
            )
        builder.row(*nav_buttons)
    
    # Навигация
    builder.row(back_button("admin_users"), home_button())
    
    return builder.as_markup()


def user_view_kb(
    telegram_id: int, 
    vpn_keys: List[Dict[str, Any]], 
    is_banned: bool
) -> InlineKeyboardMarkup:
    """
    Клавиатура просмотра пользователя.
    
    Args:
        telegram_id: Telegram ID пользователя
        vpn_keys: Список VPN-ключей пользователя
        is_banned: Забанен ли пользователь
    """
    builder = InlineKeyboardBuilder()
    
    # VPN-ключи (каждый как кнопка-ссылка)
    for key in vpn_keys:
        key_id = key['id']
        
        # Формируем название ключа согласно ТЗ
        if key.get('custom_name'):
            key_name = key['custom_name']
        else:
            # Формат: первые_4_символа...последние_4_символа от client_uuid
            uuid = key.get('client_uuid') or ''
            if len(uuid) >= 8:
                key_name = f"{uuid[:4]}...{uuid[-4:]}"
            else:
                key_name = uuid or f"Ключ #{key_id}"
        
        # Статус ключа
        expires_at = key.get('expires_at')
        if expires_at:
            # Считаем что istёк если expires_at < now (нужна проверка в коде)
            status = "🔑"
        else:
            status = "🔑"
        
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {key_name}",
                callback_data=f"admin_key_view:{key_id}"
            )
        )
    
    # Добавить ключ
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить ключ",
            callback_data=f"admin_user_add_key:{telegram_id}"
        )
    )
    
    # Бан/разбан
    if is_banned:
        ban_text = "✅ Разблокировать"
    else:
        ban_text = "🚫 Заблокировать"
    
    builder.row(
        InlineKeyboardButton(
            text=ban_text,
            callback_data=f"admin_user_toggle_ban:{telegram_id}"
        )
    )
    
    # Навигация
    builder.row(back_button("admin_users_list"), home_button())
    
    return builder.as_markup()


def user_ban_confirm_kb(telegram_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения бана/разбана.
    
    Args:
        telegram_id: Telegram ID пользователя
        is_banned: Текущий статус (True = забанен)
    """
    builder = InlineKeyboardBuilder()
    
    if is_banned:
        confirm_text = "✅ Да, разблокировать"
    else:
        confirm_text = "🚫 Да, заблокировать"
    
    builder.row(
        InlineKeyboardButton(
            text=confirm_text,
            callback_data=f"admin_user_ban_confirm:{telegram_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"admin_user_view:{telegram_id}"
        )
    )
    
    return builder.as_markup()


def key_view_kb(key_id: int, user_telegram_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура управления VPN-ключом.
    
    Args:
        key_id: ID ключа
        user_telegram_id: Telegram ID владельца (для возврата)
    """
    builder = InlineKeyboardBuilder()
    
    # Продлить
    builder.row(
        InlineKeyboardButton(
            text="📅 Продлить",
            callback_data=f"admin_key_extend:{key_id}"
        )
    )
    
    # Сбросить трафик
    builder.row(
        InlineKeyboardButton(
            text="🔄 Сбросить трафик",
            callback_data=f"admin_key_reset_traffic:{key_id}"
        )
    )
    
    # Изменить лимит
    builder.row(
        InlineKeyboardButton(
            text="📊 Изменить лимит трафика",
            callback_data=f"admin_key_change_traffic:{key_id}"
        )
    )
    
    # Навигация
    builder.row(
        back_button(f"admin_user_view:{user_telegram_id}"),
        home_button()
    )
    
    return builder.as_markup()


def add_key_server_kb(servers: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора сервера для нового ключа.
    
    Args:
        servers: Список активных серверов
    """
    builder = InlineKeyboardBuilder()
    
    for server in servers:
        builder.row(
            InlineKeyboardButton(
                text=f"🖥️ {server['name']}",
                callback_data=f"admin_add_key_server:{server['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_user_add_key_cancel")
    )
    
    return builder.as_markup()


def add_key_inbound_kb(inbounds: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора inbound для нового ключа.
    
    Args:
        inbounds: Список inbound-подключений
    """
    builder = InlineKeyboardBuilder()
    
    for inbound in inbounds:
        inbound_id = inbound.get('id')
        protocol = inbound.get('protocol', 'unknown')
        remark = inbound.get('remark', f'Inbound #{inbound_id}')
        
        builder.row(
            InlineKeyboardButton(
                text=f"🔌 {remark} ({protocol})",
                callback_data=f"admin_add_key_inbound:{inbound_id}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_add_key_back"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_user_add_key_cancel")
    )
    
    return builder.as_markup()


def add_key_step_kb(step: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для шагов добавления ключа (трафик, дни).
    
    Args:
        step: Текущий шаг
    """
    builder = InlineKeyboardBuilder()
    
    buttons = []
    if step > 1:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_add_key_back")
        )
    buttons.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_user_add_key_cancel")
    )
    
    builder.row(*buttons)
    
    return builder.as_markup()


def add_key_confirm_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения создания ключа."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Создать ключ", callback_data="admin_add_key_confirm")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_add_key_back"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_user_add_key_cancel")
    )
    
    return builder.as_markup()


def users_input_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены ввода."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_users")
    )
    return builder.as_markup()


def key_action_cancel_kb(key_id: int, user_telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура отмены действия с ключом."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_key_view:{key_id}")
    )
    return builder.as_markup()

