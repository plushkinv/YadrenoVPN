"""
Система миграций базы данных.

Миграции применяются автоматически при запуске бота.
Каждая миграция имеет уникальный номер версии.

INITIAL_VERSION — версия, на которой произведено сжатие миграций.
Все миграции до этой версии включены в migration_initial().
Новые инкрементальные миграции добавляются в словарь MIGRATIONS.
"""
import sqlite3
import logging
import json
from .connection import get_db

logger = logging.getLogger(__name__)


def _add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """
    Добавляет колонку в таблицу, игнорируя ошибку если колонка уже существует.
    Используется в миграциях для идемпотентного добавления колонок.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info(f"Колонка {column_def.split()[0]} уже существует в {table} — пропускаем")
        else:
            raise


# Версия, на которой произведено сжатие (migration_initial создаёт БД этой версии)
INITIAL_VERSION = 21

# Текущая версия схемы БД (инкрементируется при добавлении новых миграций)
LATEST_VERSION = 37


def _my_keys_item_template() -> str:
    """Скрытый дефолт формата одного ключа на странице «Мои ключи»."""
    return (
        "%статус%<b>%имяключа%</b> - %трафик% - до %датаокончания%\n"
        "     📍%сервер% - %инбаунд% (%протокол%)"
    )


def _my_keys_page_text() -> str:
    """Дефолтный текст страницы списка ключей."""
    return (
        "🔑 <b>Мои ключи</b>\n\n"
        "%списокключей%\n\n"
        "Выберите ключ для управления:"
    )


def _my_keys_page_buttons() -> str:
    """Дефолтные кнопки страницы списка ключей."""
    return json.dumps([
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _my_keys_empty_page_text() -> str:
    """Дефолтный текст пустой страницы «Мои ключи»."""
    return (
        "🔑 <b>Мои ключи</b>\n\n"
        "У вас пока нет VPN-ключей.\n\n"
        "Нажмите «Купить ключ», чтобы приобрести доступ! 🚀"
    )


def _my_keys_empty_page_buttons() -> str:
    """Дефолтные кнопки пустой страницы «Мои ключи»."""
    return json.dumps([
        {"id": "btn_buy_key",   "label": "💳 Купить ключ", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_buy"},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _renew_payment_page_text() -> str:
    """Дефолтный текст страницы выбора способа оплаты при продлении."""
    return (
        "💳 <b>Продление ключа</b>\n\n"
        "🔑 Ключ: <b>%имяключа%</b>\n\n"
        "Выберите способ оплаты:"
    )


def _renew_payment_page_buttons() -> str:
    """Дефолтные кнопки страницы выбора способа оплаты при продлении."""
    return json.dumps([
        {"id": "btn_renew_pay_crypto",  "label": "🪙 Оплатить USDT",              "color": "secondary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_stars",   "label": "⭐ Оплатить звёздами",          "color": "secondary",   "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cards",   "label": "💳 TG payments",                "color": "secondary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_qr",      "label": "📱 ЮКасса (QR/СБП)",            "color": "secondary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_wata",    "label": "🌊 WATA (Карта/СБП)",           "color": "secondary",   "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_platega", "label": "💸 Platega (СБП)",              "color": "secondary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cardlink", "label": "🔗 Cardlink (Карта/СБП)",      "color": "secondary",   "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_demo",    "label": "🏦 Демо оплата (РФ карта)",     "color": "secondary",   "row": 7, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_balance", "label": "💎 Использовать баланс",        "color": "secondary",   "row": 8, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_back",        "label": "⬅️ Назад",                     "color": "secondary", "row": 9, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main",         "label": "🈴 На главную",                "color": "secondary", "row": 9, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _empty_page_buttons() -> str:
    """Дефолт без кнопок страницы."""
    return '[]'


def _home_only_page_buttons() -> str:
    """Дефолтная кнопка возврата на главную."""
    return json.dumps([
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _referral_new_ref_notification_text() -> str:
    """Скрытый дефолт уведомления рефоводу о новом реферале."""
    return (
        "👥 <b>Новый реферал</b>\n\n"
        "По вашей ссылке зарегистрировался пользователь.\n\n"
        "👤 Имя: <b>%имя%</b>\n"
        "🔗 Логин: %логин%\n"
        "📊 Уровень: <b>%уровень%</b>"
    )


def _referral_purchase_notification_text() -> str:
    """Скрытый дефолт уведомления рефоводу о покупке реферала."""
    return (
        "💳 <b>Покупка реферала</b>\n\n"
        "Пользователь <b>%имя%</b> (%логин%) оплатил тариф.\n\n"
        "🎫 Тариф: <b>%тариф%</b>\n"
        "💵 Сумма: <b>%сумма%</b>\n"
        "⏳ Срок: <b>%дней%</b>\n"
        "🎁 Ваш бонус: <b>%вознаграждение%</b>\n"
        "📊 Уровень: <b>%уровень%</b>"
    )


def _key_navigation_page_buttons() -> str:
    """Статические кнопки навигации после операций с ключом."""
    return json.dumps([
        {"id": "btn_help",      "label": "📄 Инструкция", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
        {"id": "btn_my_keys",   "label": "🔑 Мои ключи", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _key_details_page_buttons() -> str:
    """Статическая навигация карточки ключа."""
    return json.dumps([
        {"id": "btn_my_keys",   "label": "🔑 Мои ключи", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _renew_payment_unavailable_buttons() -> str:
    """Кнопки страницы, когда способы продления недоступны."""
    return json.dumps([
        {"id": "btn_renew_back", "label": "⬅️ Назад", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main",  "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _key_details_page_text() -> str:
    """Дефолт карточки конкретного ключа."""
    return "%информацияключа%\n%историяопераций%"


def _key_show_unconfigured_page_text() -> str:
    """Дефолт страницы показа ещё не настроенного ключа."""
    return (
        "📋 <b>Показать ключ</b>\n\n"
        "⚠️ Ключ ещё не создан на сервере.\n"
        "Обратитесь в поддержку."
    )


def _renew_payment_unavailable_page_text() -> str:
    """Дефолт страницы недоступного продления."""
    return (
        "💳 <b>Продление ключа</b>\n\n"
        "😔 Способы оплаты временно недоступны.\n"
        "Попробуйте позже."
    )


def _key_replace_server_select_page_text() -> str:
    """Дефолт выбора сервера для замены ключа."""
    return (
        "🔄 <b>Замена ключа</b>\n\n"
        "%данныеэкрана%\n\n"
        "Выберите сервер:"
    )


def _key_replace_inbound_select_page_text() -> str:
    """Дефолт выбора протокола для замены ключа."""
    return (
        "🖥️ <b>Выбор протокола</b>\n\n"
        "%данныеэкрана%\n\n"
        "Выберите протокол:"
    )


def _key_replace_confirm_page_text() -> str:
    """Дефолт подтверждения замены ключа."""
    return (
        "⚠️ <b>Подтверждение замены</b>\n\n"
        "%данныезамены%\n\n"
        "Вы уверены?"
    )


def _key_rename_prompt_page_text() -> str:
    """Дефолт запроса нового имени ключа."""
    return (
        "✏️ <b>Переименование ключа</b>\n\n"
        "%данныеключа%\n\n"
        "Введите новое название для ключа (макс. 30 символов):\n"
        "<i>(Отправьте любой текст)</i>"
    )


def _new_key_server_select_page_text() -> str:
    """Дефолт выбора сервера после оплаты."""
    return (
        "🎉 <b>Оплата прошла успешно!</b>\n\n"
        "%данныеэкрана%"
    )


def _new_key_inbound_select_page_text() -> str:
    """Дефолт выбора протокола после оплаты."""
    return (
        "🖥️ <b>Выбор протокола</b>\n\n"
        "%данныеэкрана%\n\n"
        "Выберите протокол:"
    )


def _new_key_no_servers_page_text() -> str:
    """Дефолт страницы отсутствия серверов после оплаты."""
    return (
        "🎉 <b>Оплата прошла успешно!</b>\n\n"
        "⚠️ К сожалению, сейчас нет доступных серверов.\n"
        "Пожалуйста, свяжитесь с поддержкой."
    )


def _key_runtime_page_defaults() -> dict:
    """Дефолты страниц ключей, редактируемых только через /yaa."""
    return {
        'key_details': (_key_details_page_text(), _key_details_page_buttons()),
        'key_show_unconfigured': (_key_show_unconfigured_page_text(), _key_navigation_page_buttons()),
        'renew_payment_unavailable': (_renew_payment_unavailable_page_text(), _renew_payment_unavailable_buttons()),
        'key_replace_server_select': (_key_replace_server_select_page_text(), _empty_page_buttons()),
        'key_replace_inbound_select': (_key_replace_inbound_select_page_text(), _empty_page_buttons()),
        'key_replace_confirm': (_key_replace_confirm_page_text(), _empty_page_buttons()),
        'key_rename_prompt': (_key_rename_prompt_page_text(), _empty_page_buttons()),
        'new_key_server_select': (_new_key_server_select_page_text(), _home_only_page_buttons()),
        'new_key_inbound_select': (_new_key_inbound_select_page_text(), _empty_page_buttons()),
        'new_key_no_servers': (_new_key_no_servers_page_text(), _home_only_page_buttons()),
    }


def get_current_version() -> int:
    """
    Получает текущую версию схемы БД.
    
    Returns:
        int: Номер версии (0 если таблица версий не существует)
    """
    with get_db() as conn:
        # Проверяем существование таблицы schema_version
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if not cursor.fetchone():
            return 0
        
        cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        return row["version"] if row else 0


def set_version(conn: sqlite3.Connection, version: int) -> None:
    """
    Устанавливает версию схемы БД.
    
    Args:
        conn: Соединение с БД
        version: Номер версии
    """
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


# ═══════════════════════════════════════════════════════════════════════════════
# Начальная миграция (сжатие v1–v21)
# ═══════════════════════════════════════════════════════════════════════════════

def migration_initial(conn: sqlite3.Connection) -> None:
    """
    Начальная миграция: создаёт полную актуальную схему БД (v21).
    
    Вызывается только при новой установке (version = 0).
    Сжимает миграции v1–v21 в одну функцию.
    
    Таблицы:
    - schema_version: версия схемы
    - settings: глобальные настройки бота
    - users: пользователи Telegram
    - tariffs: тарифные планы
    - tariff_groups: группы тарифов
    - servers: VPN-серверы (3X-UI)
    - server_groups: связь серверов с группами (many-to-many)
    - vpn_keys: ключи/подписки пользователей
    - payments: история оплат
    - notification_log: лог уведомлений
    - referral_levels: уровни реферальной системы
    - referral_stats: статистика по рефералам
    - pages: страницы пользовательского интерфейса
    """
    logger.info("Создание БД (актуальная схема v21)...")

    # ── schema_version ────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """)

    # ── settings ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    default_settings = [
        ('broadcast_filter', 'all'),
        ('broadcast_in_progress', '0'),
        ('notification_days', '3'),
        ('notification_text',
         '⚠️ <b>Ваш VPN-ключ %имяключа% скоро истекает!</b>\n\n'
         'Через %дней% дней закончится срок действия вашего ключа.\n\n'
         'Продлите подписку, чтобы сохранить доступ к VPN без перерыва!'),
        ('trial_enabled', '0'),
        ('trial_tariff_id', ''),
        ('cards_enabled', '0'),
        ('cards_provider_token', ''),
        ('yookassa_qr_enabled', '0'),
        ('yookassa_shop_id', ''),
        ('yookassa_secret_key', ''),
        ('crypto_enabled', '0'),
        ('crypto_item_url', ''),
        ('crypto_secret_key', ''),
        ('wata_enabled', '0'),
        ('wata_jwt_token', ''),

        ('stars_enabled', '0'),
        ('demo_payment_enabled', '0'),
        ('traffic_notification_text',
         '⚠️ По ключу <b>{keyname}</b> осталось {percent}% трафика ({used} из {limit})'),
        ('monthly_traffic_reset_enabled', '0'),
        ('referral_enabled', '0'),
        ('referral_reward_type', 'days'),
        ('referral_new_ref_notifications_enabled', '0'),
        ('referral_new_ref_notification_text', _referral_new_ref_notification_text()),
        ('referral_purchase_notifications_enabled', '0'),
        ('referral_purchase_notification_text', _referral_purchase_notification_text()),
        ('referral_notification_levels', '1'),
        ('usd_rub_rate', '9500'),
        ('update_blocked', '0'),
        ('daily_tasks_time', '03:00'),
        ('update_check_time', '12:00'),
        ('display_timezone', 'Europe/Moscow'),
        ('my_keys_item_template', _my_keys_item_template()),
        # Режим работы бота для новых установок — Subscription
        # (бот выдаёт subscription URL, ключи во всех inbound с единым subId).
        # На существующих ботах migration_28 ставит 'key' — там уже есть рабочие
        # одиночные ключи, и режим менять нельзя без явного действия админа.
        ('bot_mode', 'subscription'),
    ]
    for key, value in default_settings:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # ── users ─────────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_banned INTEGER DEFAULT 0,
            is_bot_blocked INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_trial INTEGER DEFAULT 0,
            referral_code TEXT,
            referred_by INTEGER REFERENCES users(id),
            personal_balance INTEGER DEFAULT 0,
            referral_coefficient REAL DEFAULT 1.0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_is_bot_blocked ON users(is_bot_blocked)")

    # ── tariffs ───────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            price_stars INTEGER NOT NULL,
            display_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            price_rub INTEGER DEFAULT 0,
            traffic_limit_gb INTEGER DEFAULT 0,
            group_id INTEGER DEFAULT 1,
            max_ips INTEGER DEFAULT 1
        )
    """)

    # Скрытый тариф для админских ключей
    conn.execute("""
        INSERT INTO tariffs (name, duration_days, price_cents, price_stars, display_order, is_active)
        SELECT 'Admin Tariff', 365, 0, 0, 999, 0
        WHERE NOT EXISTS (SELECT 1 FROM tariffs WHERE name = 'Admin Tariff')
    """)

    # ── tariff_groups ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO tariff_groups (id, name, sort_order)
        VALUES (1, 'Основная', 1)
    """)

    # ── servers ───────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            web_base_path TEXT NOT NULL,
            login TEXT NOT NULL,
            password TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            protocol TEXT DEFAULT 'https',
            api_token TEXT,
            panel_version TEXT,
            panel_api_profile TEXT,
            panel_checked_at TEXT
        )
    """)

    # ── server_groups ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS server_groups (
            server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            group_id  INTEGER NOT NULL REFERENCES tariff_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (server_id, group_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_server_groups_group ON server_groups(group_id)")

    # ── vpn_keys ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vpn_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            server_id INTEGER,
            tariff_id INTEGER NOT NULL,
            panel_inbound_id INTEGER,
            client_uuid TEXT,
            panel_email TEXT,
            custom_name TEXT,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            traffic_updated_at DATETIME,
            traffic_notified_pct INTEGER DEFAULT 100,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_id ON vpn_keys(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_expires_at ON vpn_keys(expires_at)")

    # ── payments ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER,
            order_id TEXT NOT NULL UNIQUE,
            payment_type TEXT,
            amount_cents INTEGER,
            amount_stars INTEGER,
            period_days INTEGER,
            status TEXT DEFAULT 'paid',
            paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            yookassa_payment_id TEXT,
            wata_link_id TEXT,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)")

    # ── notification_log ──────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            sent_at DATE NOT NULL,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id)
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_unique ON notification_log(vpn_key_id, sent_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_log_vpn_key ON notification_log(vpn_key_id)")

    # ── referral_levels ───────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level_number INTEGER NOT NULL UNIQUE,
            percent INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    """)
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (1, 10, 1)")
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (2, 5, 0)")
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (3, 2, 0)")

    # ── referral_stats ────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referral_id INTEGER NOT NULL,
            level INTEGER NOT NULL,
            total_payments_count INTEGER DEFAULT 0,
            total_reward_cents INTEGER DEFAULT 0,
            total_reward_days INTEGER DEFAULT 0,
            FOREIGN KEY (referrer_id) REFERENCES users(id),
            FOREIGN KEY (referral_id) REFERENCES users(id),
            UNIQUE (referrer_id, referral_id, level)
        )
    """)

    # ── pages ─────────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            page_key         TEXT PRIMARY KEY,
            text_default     TEXT NOT NULL DEFAULT '',
            image_default    TEXT,
            buttons_default  TEXT NOT NULL DEFAULT '[]',
            text_custom      TEXT,
            image_custom     TEXT,
            updated_at       TIMESTAMP,
            buttons_custom   TEXT
        )
    """)

    # Дефолтные данные страниц (тексты в HTML, кнопки в JSON)
    page_defaults = {
        'main': {
            'text': (
                "🔐 <b>Добро пожаловать в VPN-бот!</b>\n\n"
                "Быстрый, безопасный и анонимный доступ к интернету.\n"
                "Без логов, без ограничений, без проблем! 🚀\n\n"
                "%тарифы%"
            ),
            'buttons': json.dumps([
                {"id": "btn_my_keys",  "label": "🔑 Мои ключи",         "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
                {"id": "btn_buy_key",  "label": "💳 Купить ключ",        "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_buy"},
                {"id": "btn_trial",    "label": "🎁 Пробная подписка",   "color": "secondary", "row": 1, "col": 0, "is_hidden": True,  "action_type": "internal", "action_value": "cmd_trial"},
                {"id": "btn_referral", "label": "🔗 Реферальная ссылка",  "color": "secondary", "row": 2, "col": 0, "is_hidden": True,  "action_type": "internal", "action_value": "cmd_referral"},
                {"id": "btn_help",     "label": "❓ Справка",             "color": "secondary", "row": 2, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
            ], ensure_ascii=False),
        },
        'help': {
            'text': (
                "🔐 Этот бот предоставляет доступ к VPN-сервису.\n\n"
                "<b>Как это работает:</b>\n"
                "1. Купите ключ через раздел «Купить ключ»\n\n"
                "2. Установите VPN-клиент для вашего устройства:\n\n"
                "Hiddify или v2rayNG или V2Box\n"
                "Подробная инструкция по настройке VPN👇 https://telegra.ph/Kak-nastroit-VPN-Gajd-za-2-minuty-01-23\n\n"
                "3. Импортируйте ключ в приложение\n\n"
                "4. Подключайтесь и наслаждайтесь! 🚀\n\n"
                "---\n"
                "Разработчик @plushkin_blog\n"
                "---"
            ),
            'buttons': json.dumps([
                {"id": "btn_news",      "label": "📢 Новости",    "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "url", "action_value": "https://t.me/plushkin_blog"},
                {"id": "btn_support",   "label": "💬 Поддержка",  "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "url", "action_value": "https://t.me/plushkin_chat"},
                {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'trial': {
            'text': (
                "🎁 <b>Пробная подписка</b>\n\n"
                "Хотите попробовать наш VPN бесплатно?\n\n"
                "Мы предлагаем пробный период, чтобы вы могли убедиться в качестве "
                "и скорости нашего сервиса.\n\n"
                "<b>Что входит в пробный доступ:</b>\n"
                "• Полный доступ к VPN без ограничений по сайтам\n"
                "• Высокая скорость соединения\n"
                "• Несколько протоколов на выбор\n\n"
                "Нажмите кнопку ниже, чтобы активировать пробный доступ прямо сейчас!\n\n"
                "<i>Пробный период предоставляется один раз на аккаунт.</i>"
            ),
            'buttons': json.dumps([
                {"id": "btn_activate_trial", "label": "✅ Активировать",  "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_activate_trial"},
                {"id": "btn_back_main",      "label": "🈴 На главную",   "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'prepayment': {
            'text': (
                "💳 <b>Купить ключ</b>\n\n"
                "🔐 <b>Что вы получаете:</b>\n"
                "• Доступ к нескольким серверам и протоколам\n"
                "• 1 ключ = 1 устройство (одновременное подключение)\n"
                "• Лимит трафика: до 1 ТБ в месяц (сброс каждые 30 дней)\n\n"
                "⚠️ <b>Важно знать:</b>\n"
                "• Средства не возвращаются — услуга считается оказанной в момент получения ключа\n"
                "• Мы не даём никаких гарантий бесперебойной работы сервиса в будущем\n"
                "• Мы не можем гарантировать, что данная технология останется рабочей\n\n"
                "<i>Приобретая ключ, вы соглашаетесь с этими условиями.</i>"
            ),
            'buttons': json.dumps([
                {"id": "btn_pay_crypto",  "label": "🪙 Оплатить USDT",          "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_stars",   "label": "⭐ Оплатить звёздами",      "color": "primary",   "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_cards",   "label": "💳 Оплатить картой",        "color": "primary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_qr",      "label": "📱 QR-оплата (Карта/СБП)",  "color": "primary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_wata",    "label": "🌊 Оплата WATA (Карта/СБП)", "color": "primary",  "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_demo",    "label": "🏦 Демо оплата (РФ карта)", "color": "primary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_balance", "label": "💎 Использовать баланс",    "color": "primary",   "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_back_main",   "label": "🈴 На главную",             "color": "secondary", "row": 7, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'renew_payment': {
            'text': _renew_payment_page_text(),
            'buttons': _renew_payment_page_buttons(),
        },
        'my_keys': {
            'text': _my_keys_page_text(),
            'buttons': _my_keys_page_buttons(),
        },
        'my_keys_empty': {
            'text': _my_keys_empty_page_text(),
            'buttons': _my_keys_empty_page_buttons(),
        },
        'referral': {
            'text': (
                "👥 <b>Реферальная система</b>\n\n"
                "📎 Ваша реферальная ссылка:\n"
                "<code>%ссылка%</code>\n\n"
                "━━━━━━━━━━━━━━━\n"
                "📝 <b>Условия:</b>\n"
                "Приглашённые пользователи регистрируются по вашей ссылке. "
                "Когда они оплачивают подписку, вы получаете реферальное вознаграждение.\n\n"
                "━━━━━━━━━━━━━━━\n"
                "%статистика%"
            ),
            'buttons': json.dumps([
                {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'key_delivery': {
            'text': (
                "✅ <b>Ваш VPN-ключ!</b>\n\n"
                "%ключ%\n"
                "☝️ Нажмите, чтобы скопировать.\n\n"
                "📱 <b>Инструкция:</b>\n"
                "1. Скопируйте ссылку или отсканируйте QR-код.\n"
                "2. Импортируйте в свой клиент. Какой именно клиент подходит, смотри в инструкции по кнопке ниже.\n"
                "3. Нажмите подключиться!"
            ),
            'buttons': json.dumps([
                {"id": "btn_help",      "label": "📄 Инструкция",  "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
                {"id": "btn_my_keys",   "label": "🔑 Мои ключи",  "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
                {"id": "btn_back_main", "label": "🈴 На главную",  "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
    }
    for page_key, (text_default, buttons_default) in _key_runtime_page_defaults().items():
        page_defaults[page_key] = {
            'text': text_default,
            'buttons': buttons_default,
        }

    for page_key, data in page_defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default) VALUES (?, ?, ?)",
            (page_key, data['text'], data['buttons'])
        )

    logger.info("БД создана (актуальная схема v21)")


# ═══════════════════════════════════════════════════════════════════════════════
# Инкрементальные миграции (добавляются ниже по мере развития проекта)
# ═══════════════════════════════════════════════════════════════════════════════

# Пример добавления новой миграции:
#
def migration_22(conn):
    """
    Миграция v22: удаление стандартного режима крипто-оплаты.
    
    - Удаляет настройку crypto_integration_mode из settings
    - Удаляет колонку external_id из таблицы tariffs
    """
    # 1. Удаляем настройку crypto_integration_mode
    conn.execute("DELETE FROM settings WHERE key = 'crypto_integration_mode'")
    
    # 2. Удаляем колонку external_id из tariffs
    # ALTER TABLE DROP COLUMN поддерживается с SQLite 3.35.0 (март 2021)
    # Фоллбэк через пересоздание таблицы для старых версий
    try:
        conn.execute("ALTER TABLE tariffs DROP COLUMN external_id")
        logger.info("Колонка external_id удалена через DROP COLUMN")
    except Exception as e:
        if "no such column" in str(e).lower():
            # Колонки уже нет — всё ок
            logger.info("Колонка external_id уже отсутствует — пропускаем")
        else:
            # Старый SQLite — пересоздаём таблицу без external_id
            logger.info(f"DROP COLUMN не поддерживается ({e}), пересоздаём таблицу tariffs")
            conn.execute("""
                CREATE TABLE tariffs_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    duration_days INTEGER NOT NULL,
                    price_cents INTEGER NOT NULL,
                    price_stars INTEGER NOT NULL,
                    display_order INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    price_rub INTEGER DEFAULT 0,
                    traffic_limit_gb INTEGER DEFAULT 0,
                    group_id INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                INSERT INTO tariffs_new (id, name, duration_days, price_cents, price_stars,
                                         display_order, is_active, price_rub, traffic_limit_gb, group_id)
                SELECT id, name, duration_days, price_cents, price_stars,
                       display_order, is_active, price_rub, traffic_limit_gb, group_id
                FROM tariffs
            """)
            conn.execute("DROP TABLE tariffs")
            conn.execute("ALTER TABLE tariffs_new RENAME TO tariffs")
            logger.info("Таблица tariffs пересоздана без external_id")
    
    logger.info("Миграция v22 применена: стандартный режим крипто-оплаты удалён")


def migration_23(conn):
    """
    Миграция v23: добавление платёжного метода WATA.

    - Добавляет настройки wata_enabled и wata_jwt_token
    - Добавляет колонку wata_link_id в таблицу payments
    - Добавляет кнопку btn_pay_wata в дефолтную раскладку страницы prepayment
    """
    # 1. Настройки WATA
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wata_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wata_jwt_token', '')")

    # 2. Колонка wata_link_id для отслеживания платежей WATA
    _add_column(conn, "payments", "wata_link_id TEXT")

    # 3. Обновляем buttons_default страницы prepayment — вставляем btn_pay_wata после btn_pay_qr
    cursor = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'prepayment'")
    row = cursor.fetchone()
    if row:
        try:
            buttons = json.loads(row['buttons_default'])
        except (json.JSONDecodeError, TypeError):
            buttons = []

        existing_ids = {b.get('id') for b in buttons if isinstance(b, dict)}
        if 'btn_pay_wata' not in existing_ids:
            # Находим строку btn_pay_qr и вставляем wata после него, сдвигая остальные строки
            qr_row = None
            for b in buttons:
                if isinstance(b, dict) and b.get('id') == 'btn_pay_qr':
                    qr_row = b.get('row', 0)
                    break

            if qr_row is None:
                # Нет btn_pay_qr — вставляем перед btn_back_main или в конец
                max_row = max((b.get('row', 0) for b in buttons if isinstance(b, dict)), default=-1)
                new_row = max_row + 1
                # Если последняя кнопка — btn_back_main, вставляем перед ней
                for b in buttons:
                    if isinstance(b, dict) and b.get('id') == 'btn_back_main':
                        new_row = b.get('row', new_row)
                        b['row'] = new_row + 1
                        break
                buttons.append({
                    "id": "btn_pay_wata",
                    "label": "🌊 Оплата WATA (Карта/СБП)",
                    "color": "primary",
                    "row": new_row,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })
            else:
                # Сдвигаем все строки > qr_row вниз на 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('row', 0) > qr_row:
                        b['row'] = b['row'] + 1
                buttons.append({
                    "id": "btn_pay_wata",
                    "label": "🌊 Оплата WATA (Карта/СБП)",
                    "color": "primary",
                    "row": qr_row + 1,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })

            conn.execute(
                "UPDATE pages SET buttons_default = ? WHERE page_key = 'prepayment'",
                (json.dumps(buttons, ensure_ascii=False),)
            )
            logger.info("Кнопка btn_pay_wata добавлена в дефолтную раскладку prepayment")

    logger.info("Миграция v23 применена: добавлен платёжный метод WATA")


def migration_24(conn):
    """
    Миграция v24: добавление платёжного метода Platega (СБП/Карта).

    - Добавляет настройки platega_enabled, platega_merchant_id, platega_secret
    - Добавляет колонку platega_transaction_id в таблицу payments
    - Добавляет кнопку btn_pay_platega в дефолтную раскладку страницы prepayment
    """
    # 1. Настройки Platega
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_merchant_id', '')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_secret', '')")

    # 2. Колонка platega_transaction_id для отслеживания платежей Platega
    _add_column(conn, "payments", "platega_transaction_id TEXT")

    # 3. Обновляем buttons_default страницы prepayment — вставляем btn_pay_platega после btn_pay_wata
    cursor = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'prepayment'")
    row = cursor.fetchone()
    if row:
        try:
            buttons = json.loads(row['buttons_default'])
        except (json.JSONDecodeError, TypeError):
            buttons = []

        existing_ids = {b.get('id') for b in buttons if isinstance(b, dict)}
        if 'btn_pay_platega' not in existing_ids:
            wata_row = None
            for b in buttons:
                if isinstance(b, dict) and b.get('id') == 'btn_pay_wata':
                    wata_row = b.get('row', 0)
                    break

            if wata_row is None:
                # Нет btn_pay_wata — вставляем перед btn_back_main или в конец
                max_row = max((b.get('row', 0) for b in buttons if isinstance(b, dict)), default=-1)
                new_row = max_row + 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('id') == 'btn_back_main':
                        new_row = b.get('row', new_row)
                        b['row'] = new_row + 1
                        break
                buttons.append({
                    "id": "btn_pay_platega",
                    "label": "💸 Оплата Platega (СБП)",
                    "color": "primary",
                    "row": new_row,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })
            else:
                # Сдвигаем все строки > wata_row вниз на 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('row', 0) > wata_row:
                        b['row'] = b['row'] + 1
                buttons.append({
                    "id": "btn_pay_platega",
                    "label": "💸 Оплата Platega (СБП)",
                    "color": "primary",
                    "row": wata_row + 1,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })

            conn.execute(
                "UPDATE pages SET buttons_default = ? WHERE page_key = 'prepayment'",
                (json.dumps(buttons, ensure_ascii=False),)
            )
            logger.info("Кнопка btn_pay_platega добавлена в дефолтную раскладку prepayment")

    logger.info("Миграция v24 применена: добавлен платёжный метод Platega (СБП)")


def migration_25(conn):
    """
    Миграция v25: добавление платёжного метода Cardlink (Карта/СБП, cardlink.link).

    - Добавляет настройки cardlink_enabled, cardlink_shop_id, cardlink_api_token
    - Добавляет колонку cardlink_bill_id в таблицу payments
    - Добавляет кнопку btn_pay_cardlink в дефолтную раскладку страницы prepayment
    """
    # 1. Настройки Cardlink
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_shop_id', '')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_api_token', '')")

    # 2. Колонка cardlink_bill_id для отслеживания платежей Cardlink
    _add_column(conn, "payments", "cardlink_bill_id TEXT")

    # 3. Обновляем buttons_default страницы prepayment — вставляем btn_pay_cardlink после btn_pay_platega
    cursor = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'prepayment'")
    row = cursor.fetchone()
    if row:
        try:
            buttons = json.loads(row['buttons_default'])
        except (json.JSONDecodeError, TypeError):
            buttons = []

        existing_ids = {b.get('id') for b in buttons if isinstance(b, dict)}
        if 'btn_pay_cardlink' not in existing_ids:
            platega_row = None
            for b in buttons:
                if isinstance(b, dict) and b.get('id') == 'btn_pay_platega':
                    platega_row = b.get('row', 0)
                    break

            if platega_row is None:
                max_row = max((b.get('row', 0) for b in buttons if isinstance(b, dict)), default=-1)
                new_row = max_row + 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('id') == 'btn_back_main':
                        new_row = b.get('row', new_row)
                        b['row'] = new_row + 1
                        break
                buttons.append({
                    "id": "btn_pay_cardlink",
                    "label": "🔗 Оплата Cardlink (Карта/СБП)",
                    "color": "primary",
                    "row": new_row,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })
            else:
                for b in buttons:
                    if isinstance(b, dict) and b.get('row', 0) > platega_row:
                        b['row'] = b['row'] + 1
                buttons.append({
                    "id": "btn_pay_cardlink",
                    "label": "🔗 Оплата Cardlink (Карта/СБП)",
                    "color": "primary",
                    "row": platega_row + 1,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })

            conn.execute(
                "UPDATE pages SET buttons_default = ? WHERE page_key = 'prepayment'",
                (json.dumps(buttons, ensure_ascii=False),)
            )
            logger.info("Кнопка btn_pay_cardlink добавлена в дефолтную раскладку prepayment")

    logger.info("Миграция v25 применена: добавлен платёжный метод Cardlink (Карта/СБП)")


def migration_26(conn):
    """
    Миграция v26: добавление настроек времени ежедневных задач и проверки обновлений.
    """
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_tasks_time', '03:00')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('update_check_time', '12:00')")
    logger.info("Миграция v26 применена: добавлены настройки daily_tasks_time и update_check_time")


def migration_27(conn):
    """
    Миграция v27: добавление колонки api_token в servers для поддержки 3x-ui v3.0+.

    На v3.0+ панель требует CSRF-токен на всех POST-запросах, но имеет альтернативу —
    Bearer-токен через заголовок Authorization, который полностью обходит CSRF.
    Бот автоматически вытягивает токен через GET /panel/setting/getApiToken после
    первого успешного логина на v3.0+ панель и сохраняет его в это поле.
    Для v2.x панелей поле остаётся NULL — используется старый cookie-flow.
    """
    _add_column(conn, "servers", "api_token TEXT")
    logger.info("Миграция v27 применена: добавлена колонка servers.api_token для 3x-ui v3.0+")


def migration_28(conn):
    """
    Миграция v28: введение режима Subscription.

    - Добавляет vpn_keys.sub_id — идентификатор подписки (общий для всех клиентов
      с этим email на одном сервере). NULL для legacy ключей (режим Keys).
    - Создаёт индекс (server_id, panel_email) для быстрого поиска клиентов
      одной подписки в синхронизации.
    - Устанавливает bot_mode='key' для существующих ботов: они уже работают
      с одиночными ключами, и менять режим без явного решения админа нельзя.
      На новых установках migration_initial кладёт 'subscription' раньше —
      INSERT OR IGNORE ниже не перезапишет его.
    """
    _add_column(conn, "vpn_keys", "sub_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vpn_keys_server_email "
        "ON vpn_keys(server_id, panel_email)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_mode', 'key')"
    )
    logger.info("Миграция v28 применена: добавлено vpn_keys.sub_id, индекс server_email, bot_mode='key' (legacy upgrade)")


def migration_29(conn):
    """
    Миграция v29: обычный стиль дефолтных кнопок главной страницы.

    Меняет только pages.buttons_default для страницы main.
    pages.buttons_custom не трогается: пользовательские настройки остаются
    пользовательскими и имеют приоритет при рендеринге.
    """
    row = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'main'").fetchone()
    if not row:
        logger.info("Миграция v29: страница main не найдена, пропускаем")
        return

    try:
        buttons = json.loads(row["buttons_default"] or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning("Миграция v29: buttons_default страницы main не является JSON, пропускаем")
        return

    if not isinstance(buttons, list):
        logger.warning("Миграция v29: buttons_default страницы main не является списком, пропускаем")
        return

    changed = False
    for button in buttons:
        if not isinstance(button, dict):
            continue
        if button.get("id") in {"btn_my_keys", "btn_buy_key"} and button.get("color") != "secondary":
            button["color"] = "secondary"
            changed = True

    if changed:
        conn.execute(
            "UPDATE pages SET buttons_default = ? WHERE page_key = 'main'",
            (json.dumps(buttons, ensure_ascii=False),)
        )
        logger.info("Миграция v29: дефолтные кнопки main переведены в обычный стиль")
    else:
        logger.info("Миграция v29: дефолтные кнопки main уже в обычном стиле")

def migration_30(conn):
    """
    Миграция v30: добавление поля max_ips в таблицу tariffs.
    """
    try:
        from config import DEFAULT_LIMIT_IP
        default_val = DEFAULT_LIMIT_IP
    except ImportError:
        default_val = 1

    _add_column(conn, "tariffs", f"max_ips INTEGER DEFAULT {default_val}")
    logger.info(f"Миграция v30 применена: добавлено поле max_ips в таблицу tariffs (по умолчанию {default_val})")


def migration_31(conn):
    """
    Миграция v31: перенос выбора способа оплаты при продлении в таблицу pages.

    Создаёт страницу renew_payment с дефолтным текстом и системными кнопками.
    Кастомные поля text_custom/image_custom/buttons_custom не изменяются.
    """
    text_default = _renew_payment_page_text()
    buttons_default = _renew_payment_page_buttons()

    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('renew_payment', ?, ?)
        """,
        (text_default, buttons_default)
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'renew_payment'
        """,
        (text_default, buttons_default)
    )
    logger.info("Миграция v31 применена: добавлена страница renew_payment")


def migration_32(conn):
    """
    Миграция v32: кеш диагностики панели 3x-ui.

    panel_version хранит определённую версию панели, panel_api_profile — выбранный
    профиль API ('legacy_inbounds' или 'clients_api'), panel_checked_at — время
    последней успешной проверки.
    """
    _add_column(conn, "servers", "panel_version TEXT")
    _add_column(conn, "servers", "panel_api_profile TEXT")
    _add_column(conn, "servers", "panel_checked_at TEXT")
    logger.info("Миграция v32 применена: добавлены поля диагностики 3x-ui в servers")


def migration_33(conn):
    """
    Миграция v33: перенос страницы «Мои ключи» в таблицу pages.

    Создаёт страницы my_keys/my_keys_empty и скрытую настройку формата одного
    ключа. Кастомные поля страниц не изменяются.
    """
    page_defaults = {
        'my_keys': (_my_keys_page_text(), _my_keys_page_buttons()),
        'my_keys_empty': (_my_keys_empty_page_text(), _my_keys_empty_page_buttons()),
    }

    for page_key, (text_default, buttons_default) in page_defaults.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
            VALUES (?, ?, ?)
            """,
            (page_key, text_default, buttons_default),
        )
        conn.execute(
            """
            UPDATE pages
            SET text_default = ?,
                buttons_default = ?
            WHERE page_key = ?
            """,
            (text_default, buttons_default, page_key),
        )

    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('my_keys_item_template', ?)
        """,
        (_my_keys_item_template(),),
    )
    logger.info("Миграция v33 применена: добавлены страницы my_keys/my_keys_empty")


def migration_34(conn):
    """
    Миграция v34: перенос дополнительных пользовательских экранов ключей в pages.

    Обновляет только дефолтные поля. Кастомные текст, картинка и кнопки
    администраторов остаются без изменений.
    """
    for page_key, (text_default, buttons_default) in _key_runtime_page_defaults().items():
        conn.execute(
            """
            INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
            VALUES (?, ?, ?)
            """,
            (page_key, text_default, buttons_default),
        )
        conn.execute(
            """
            UPDATE pages
            SET text_default = ?,
                buttons_default = ?
            WHERE page_key = ?
            """,
            (text_default, buttons_default, page_key),
        )

    logger.info("Миграция v34 применена: добавлены пользовательские страницы ключей")

def migration_35(conn):
    """
    Миграция v35: флаг недоступности пользователя для сообщений бота.

    Поле is_bot_blocked помечает пользователей, которые заблокировали бота в Telegram.
    Такие пользователи исключаются из массовых отправок до нового обращения в бот.
    """
    _add_column(conn, "users", "is_bot_blocked INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_is_bot_blocked ON users(is_bot_blocked)")
    logger.info("Миграция v35 применена: добавлен флаг is_bot_blocked")


def migration_36(conn):
    """
    Миграция v36: скрытая настройка часового пояса отображения дат.

    SQLite продолжает хранить даты в UTC, настройка влияет только на вывод в Telegram.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('display_timezone', 'Europe/Moscow')
        """
    )
    logger.info("Миграция v36 применена: добавлена настройка display_timezone")


def migration_37(conn):
    """
    Миграция v37: скрытые уведомления рефоводу и имя пользователя.

    first_name/last_name нужны для плейсхолдера %имя% в уведомлениях.
    Настройки уведомлений остаются скрытыми и меняются только через БД.
    """
    _add_column(conn, "users", "first_name TEXT")
    _add_column(conn, "users", "last_name TEXT")

    defaults = [
        ('referral_new_ref_notifications_enabled', '0'),
        ('referral_new_ref_notification_text', _referral_new_ref_notification_text()),
        ('referral_purchase_notifications_enabled', '0'),
        ('referral_purchase_notification_text', _referral_purchase_notification_text()),
        ('referral_notification_levels', '1'),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    logger.info("Миграция v37 применена: добавлены скрытые уведомления рефералки")


MIGRATIONS = {
    22: migration_22,
    23: migration_23,
    24: migration_24,
    25: migration_25,
    26: migration_26,
    27: migration_27,
    28: migration_28,
    29: migration_29,
    30: migration_30,
    31: migration_31,
    32: migration_32,
    33: migration_33,
    34: migration_34,
    35: migration_35,
    36: migration_36,
    37: migration_37,
}



def run_migrations() -> None:
    """
    Запускает все необходимые миграции.
    
    Логика:
    - version = 0 (новая установка): вызывает migration_initial → ставит LATEST_VERSION
    - version = LATEST_VERSION: ничего не делает
    - version < INITIAL_VERSION: ошибка (нужно обновить через промежуточную версию)
    - version >= INITIAL_VERSION: применяет инкрементальные миграции из MIGRATIONS
    """
    try:
        current = get_current_version()
        
        if current >= LATEST_VERSION:
            logger.info(f"✅ БД соответствует версии {LATEST_VERSION}. Миграция не требуется.")
            return
        
        # Защита: БД на промежуточной версии, которую нельзя обновить сжатыми миграциями
        if 0 < current < INITIAL_VERSION:
            raise RuntimeError(
                f"Версия БД ({current}) ниже минимально поддерживаемой ({INITIAL_VERSION}). "
                f"Сначала обновите бот до промежуточной версии, чтобы БД мигрировала до v{INITIAL_VERSION}."
            )
        
        logger.info(f"🔄 Требуется миграция БД с версии {current} до {LATEST_VERSION}")
        
        with get_db() as conn:
            # Новая установка — создаём БД с нуля
            if current == 0:
                migration_initial(conn)
                set_version(conn, INITIAL_VERSION)
                current = INITIAL_VERSION
            
            # Инкрементальные миграции (22, 23, ...)
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
