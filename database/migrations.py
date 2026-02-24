"""
Система миграций базы данных.

Миграции применяются автоматически при запуске бота.
Каждая миграция имеет уникальный номер версии.
"""
import sqlite3
import logging
from .connection import get_db

logger = logging.getLogger(__name__)

# Текущая версия схемы БД
LATEST_VERSION = 4


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


def migration_1(conn: sqlite3.Connection) -> None:
    """
    Миграция v1: Полная структура БД.
    
    Создаёт таблицы:
    - schema_version: версия схемы
    - settings: глобальные настройки бота
    - users: пользователи Telegram
    - tariffs: тарифные планы
    - servers: VPN-серверы (3X-UI)
    - vpn_keys: ключи/подписки пользователей
    - payments: история оплат
    - notification_log: лог уведомлений
    """
    logger.info("Применение миграции v1...")

    # Таблица версий схемы
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL  -- Номер версии схемы БД
        )
    """)
    
    # Глобальные настройки бота
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,  -- Уникальное название настройки
            value TEXT             -- Значение
        )
    """)

    # Дефолтные настройки
    default_settings = [
        ('broadcast_filter', 'all'),  # Фильтр по умолчанию: все пользователи
        ('broadcast_in_progress', '0'),  # Флаг активной рассылки
        ('notification_days', '3'),  # За сколько дней уведомлять
        ('notification_text', '''⚠️ **Ваш VPN-ключ скоро истекает!**

Через {days} дней закончится срок действия вашего ключа.

Продлите подписку, чтобы сохранить доступ к VPN без перерыва!'''),
        ('main_page_text', (
            "🔐 *Добро пожаловать в VPN\\-бот\\!*\n"
            "Быстрый, безопасный и анонимный доступ к интернету\\.\n"
            "Без логов, без ограничений, без проблем\\! 🚀\n"
        )),
        ('help_page_text', (
            "🔐 Этот бот предоставляет доступ к VPN\\-сервису\\.\n\n"
            "*Как это работает:*\n"
            "1\\. Купите ключ через раздел «Купить ключ»\n\n"
            "2\\. Установите VPN\\-клиент для вашего устройства:\n\n"
            "Hiddify или v2rayNG или V2Box\n"
            "Подробная инструкция по настройке VPN👇 https://telegra\\.ph/Kak\\-nastroit\\-VPN\\-Gajd\\-za\\-2\\-minuty\\-01\\-23\n\n"
            "3\\. Импортируйте ключ в приложение\n\n"
            "4\\. Подключайтесь и наслаждайтесь\\! 🚀\n\n"
            "\\-\\-\\-\n"
            "Разработчик @plushkin\\_blog\n"
            "\\-\\-\\-"
        )),
        ('news_channel_link', 'https://t.me/YadrenoRu'),
        ('support_channel_link', 'https://t.me/YadrenoChat'),
    ]
    for key, value in default_settings:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    
    # Пользователи Telegram
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            is_banned INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
    
    # Тарифные планы
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            price_stars INTEGER NOT NULL,
            external_id INTEGER,
            display_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )
    """)
    
    # Создаём скрытый тариф для админских ключей
    conn.execute("""
        INSERT INTO tariffs (name, duration_days, price_cents, price_stars, external_id, display_order, is_active)
        SELECT 'Admin Tariff', 365, 0, 0, 0, 999, 0
        WHERE NOT EXISTS (SELECT 1 FROM tariffs WHERE name = 'Admin Tariff')
    """)

    # VPN-серверы
    conn.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            web_base_path TEXT NOT NULL,
            login TEXT NOT NULL,
            password TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)
    
    # VPN-ключи
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
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_id ON vpn_keys(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_expires_at ON vpn_keys(expires_at)")
    
    # История оплат
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER NOT NULL,
            order_id TEXT NOT NULL UNIQUE,
            payment_type TEXT NOT NULL,
            amount_cents INTEGER,
            amount_stars INTEGER,
            period_days INTEGER NOT NULL,
            status TEXT DEFAULT 'paid',
            paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)")

    # Лог уведомлений
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
    
    logger.info("Миграция v1 применена")


def migration_2(conn: sqlite3.Connection) -> None:
    """
    Миграция v2: Разрешаем NULL в таблице payments для tariff_id, period_days и payment_type.
    
    Это необходимо, чтобы не фиксировать тариф и тип оплаты при создании pending-ордера,
    так как пользователь выбирает их непосредственно при оплате.
    """
    logger.info("Применение миграции v2 (Make payments fields nullable)...")
    
    # 1. Создаём новую таблицу (tariff_id, period_days, payment_type теперь без NOT NULL)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER,  -- Теперь NULLABLE
            order_id TEXT NOT NULL UNIQUE,
            payment_type TEXT,  -- Теперь NULLABLE
            amount_cents INTEGER,
            amount_stars INTEGER,
            period_days INTEGER, -- Теперь NULLABLE
            status TEXT DEFAULT 'paid',
            paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    
    # 2. Копируем данные
    conn.execute("""
        INSERT INTO payments_new (id, vpn_key_id, user_id, tariff_id, order_id, payment_type, 
                                 amount_cents, amount_stars, period_days, status, paid_at)
        SELECT id, vpn_key_id, user_id, tariff_id, order_id, payment_type, 
               amount_cents, amount_stars, period_days, status, paid_at
        FROM payments
    """)
    
    # 3. Удаляем старую таблицу
    conn.execute("DROP TABLE payments")
    
    # 4. Переименовываем новую таблицу
    conn.execute("ALTER TABLE payments_new RENAME TO payments")
    
    # 5. Пересоздаём индексы
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)")
    
    logger.info("Миграция v2 применена")


def migration_3(conn: sqlite3.Connection) -> None:
    """
    Миграция v3: Функция «Пробная подписка».

    Изменения:
    - Добавляет колонку used_trial в таблицу users (флаг использования пробного периода)
    - Добавляет настройки trial_enabled, trial_tariff_id, trial_page_text в settings
    """
    logger.info("Применение миграции v3 (Пробная подписка)...")

    # Добавляем колонку used_trial в таблицу users (если не существует)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN used_trial INTEGER DEFAULT 0")
        logger.info("Колонка used_trial добавлена в таблицу users")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info("Колонка used_trial уже существует")
        else:
            # Если ошибка другая — пробрасываем её
            raise
    except Exception as e:
        logger.error(f"Ошибка миграции v3: {e}")
        raise

    # Дефолтный текст для страницы пробной подписки (MarkdownV2)
    trial_page_text_default = (
        "🎁 *Пробная подписка*\n\n"
        "Хотите попробовать наш VPN бесплатно?\n\n"
        "Мы предлагаем пробный период, чтобы вы могли убедиться в качестве "
        "и скорости нашего сервиса\\.\n\n"
        "*Что входит в пробный доступ:*\n"
        "• Полный доступ к VPN без ограничений по сайтам\n"
        "• Высокая скорость соединения\n"
        "• Несколько протоколов на выбор\n\n"
        "Нажмите кнопку ниже, чтобы активировать пробный доступ прямо сейчас\!\n\n"
        "_Пробный период предоставляется один раз на аккаунт\._"
    )

    # Настройки пробной подписки
    trial_settings = [
        ('trial_enabled', '0'),          # Выключено по умолчанию
        ('trial_tariff_id', ''),          # Тариф не задан
        ('trial_page_text', trial_page_text_default),  # Текст по умолчанию
    ]
    for key, value in trial_settings:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    logger.info("Миграция v3 применена")


def migration_4(conn: sqlite3.Connection) -> None:
    """
    Миграция v4: Оплата российскими картами.
    
    - Добавляет поле price_rub (цена в рублях) в таблицу tariffs
    - Добавляет настройки cards_enabled и cards_provider_token
    """
    logger.info("Применение миграции v4...")

    # Добавляем price_rub в tariffs (если его еще нет)
    try:
        conn.execute("ALTER TABLE tariffs ADD COLUMN price_rub INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Игнорируем ошибку, если колонка уже существует

    # Добавляем новые настройки
    card_settings = [
        ('cards_enabled', '0'),          # Выключено по умолчанию
        ('cards_provider_token', ''),    # Токен провайдера пустой
    ]
    for key, value in card_settings:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    logger.info("Миграция v4 применена")


MIGRATIONS = {
    1: migration_1,
    2: migration_2,
    3: migration_3,
    4: migration_4,
}


def run_migrations() -> None:
    """
    Запускает все необходимые миграции.
    
    Проверяет текущую версию и применяет все миграции от текущей до LATEST_VERSION.
    """
    try:
        current = get_current_version()
        
        if current >= LATEST_VERSION:
            logger.info(f"✅ БД соответствует версии {LATEST_VERSION}. Миграция не требуется.")
            return
        
        logger.info(f"🔄 Требуется миграция БД с версии {current} до {LATEST_VERSION}")
        
        with get_db() as conn:
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)
        
        logger.info(f"✅ Миграция успешная : БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
