"""
Database migration system.

Migrations are applied automatically when the bot is launched.
Each migration has a unique version number.

INITIAL_VERSION — the version on which migrations were compressed.
All migrations prior to this version are included in migration_initial().
New incremental migrations are added to the MIGRATIONS dictionary.
"""
import sqlite3
import logging
import json
import re
from .connection import get_db

logger = logging.getLogger(__name__)


def _add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """
    Adds a column to the table, ignoring the error if the column already exists.
    Used in migrations to idempotently add columns.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info(f"Колонка {column_def.split()[0]} уже существует в {table} — пропускаем")
        else:
            raise


# The version on which the compression was performed (migration_initial creates a database of this version)
INITIAL_VERSION = 21

# Current version of the database schema (incremented when new migrations are added)
LATEST_VERSION = 72


def _my_keys_item_template() -> str:
    """Hidden default of one key format on the “My Keys” page."""
    return (
        "%ключ_статус%<b>%ключ_имя%</b> - %ключ_трафик% - до %ключ_дата_окончания%\n"
        "     📍%ключ_сервер% - %ключ_инбаунд% (%ключ_протокол%)"
    )


def _my_keys_page_text() -> str:
    """Default text of the key list page."""
    return (
        "🔑 <b>Мои ключи</b>\n\n"
        "%список_ключей%\n\n"
        "Выберите ключ для управления:"
    )


def _my_keys_page_buttons() -> str:
    """Default buttons on the key list page."""
    return json.dumps([
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _custom_profile_page_text() -> str:
    """Default custom page for your personal account."""
    return (
        "👤 <b>Личный кабинет</b>\n\n"
        "%профиль%\n\n"
        "━━━━━━━━━━━━━━━\n"
        "%ключи_сводка%"
    )


def _custom_profile_page_buttons() -> str:
    """Default buttons on the personal account page."""
    return json.dumps([
        {"id": "btn_profile_my_keys", "label": "🔑 Мои ключи", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
        {"id": "btn_profile_buy", "label": "💳 Купить ключ", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_buy"},
        {"id": "btn_profile_referral", "label": "🔗 Реферальная система", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_referral"},
        {"id": "btn_profile_show_id", "label": "🆔 Мой ID", "color": "secondary", "row": 1, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_show_id"},
        {"id": "btn_profile_help", "label": "❓ Справка", "color": "secondary", "row": 2, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
        {"id": "btn_profile_back_main", "label": "🈴 На главную", "color": "secondary", "row": 3, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _my_keys_empty_page_text() -> str:
    """Default text of the empty “My Keys” page."""
    return (
        "🔑 <b>Мои ключи</b>\n\n"
        "У вас пока нет VPN-ключей.\n\n"
        "Нажмите «Купить ключ», чтобы приобрести доступ! 🚀"
    )


def _my_keys_empty_page_buttons() -> str:
    """Default buttons on the empty “My Keys” page."""
    return json.dumps([
        {"id": "btn_buy_key",   "label": "💳 Купить ключ", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_buy"},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _renew_payment_page_text() -> str:
    """Default text on the payment method selection page for renewal."""
    return (
        "💳 <b>Продление ключа</b>\n\n"
        "🔑 Ключ: <b>%ключ_имя%</b>\n\n"
        "Выберите способ оплаты:"
    )


def _renew_payment_page_buttons() -> str:
    """Default buttons on the page for selecting a payment method when renewing."""
    return json.dumps([
        {"id": "btn_renew_pay_crypto",  "label": "🪙 Оплатить USDT",              "color": "secondary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_stars",   "label": "⭐ Оплатить звёздами",          "color": "secondary",   "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cards",   "label": "💳 TG payments",                "color": "secondary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_qr",      "label": "📱 ЮКасса",                     "color": "secondary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_wata",    "label": "🌊 WATA",                       "color": "secondary",   "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_platega", "label": "💸 Platega",                    "color": "secondary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cardlink", "label": "🔗 Cardlink",                  "color": "secondary",   "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_demo",    "label": "🏦 Демо оплата (РФ карта)",     "color": "secondary",   "row": 7, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_balance", "label": "💎 Использовать баланс",        "color": "secondary",   "row": 8, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_back",        "label": "⬅️ Назад",                     "color": "secondary", "row": 9, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main",         "label": "🈴 На главную",                "color": "secondary", "row": 9, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _qr_payment_page_text() -> str:
    """Default text of the QR payment technical page."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "💳 <b>Тариф:</b> %платеж_тариф%\n"
        "💰 <b>Сумма:</b> %платеж_сумма%\n"
        "⏳ <b>%платеж_срок_тип%:</b> %платеж_срок%\n"
        "%платеж_скидка_строка%"
        "\n%платеж_инструкция%\n\n"
        "<i>%платеж_подсказка%</i>"
    )


def _crypto_payment_page_text() -> str:
    """Default text of the transition screen to crypto-payment."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "💳 <b>Тариф:</b> %платеж_тариф%\n"
        "💰 <b>Сумма к оплате:</b> %платеж_сумма%\n"
        "%платеж_скидка_строка%"
        "\n%платеж_инструкция%"
    )


def _balance_payment_page_text() -> str:
    """Default text of the balance payment screen."""
    return (
        "💳 <b>Оплата тарифа «%платеж_тариф%»</b>\n\n"
        "💰 Сумма: %платеж_сумма%\n"
        "%платеж_скидка_строка%"
        "💎 Ваш баланс: %платеж_баланс%\n\n"
        "✅ С баланса будет списано: %платеж_списание_баланса%\n"
        "💳 К оплате: %платеж_остаток_к_оплате%"
        "%платеж_доплата_подсказка%"
    )


def _demo_payment_page_text() -> str:
    """Default text of the payment demo screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_инструкция%\n\n"
        "%платеж_ключ_строка%"
        "📦 <b>Тариф:</b> %платеж_тариф%\n"
        "📅 <b>%платеж_срок_тип%:</b> %платеж_срок%\n"
        "💰 <b>Сумма:</b> %платеж_сумма%\n\n"
        "<i>%платеж_подсказка%</i>"
    )


def _payment_tariff_select_page_text() -> str:
    """Default text of the payment tariff selection screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


def _payment_status_page_text() -> str:
    """Default text of the payment status screen."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_инструкция%"
        "%платеж_подсказка%"
    )


def _support_start_page_text() -> str:
    """Default login text for built-in support."""
    return (
        "%поддержка_заголовок%\n\n"
        "%поддержка_инструкция%"
    )


def _support_status_page_text() -> str:
    """Default text of the result of a support request."""
    return (
        "%поддержка_статус_заголовок%\n\n"
        "%поддержка_статус_текст%"
    )


def _promo_enter_page_text() -> str:
    """Default text for entering a promotional code or coupon."""
    return (
        "🎟 <b>Промокод</b>\n\n"
        "Отправьте промокод или одноразовый купон одним сообщением.\n\n"
        "Ручной ввод заменит промокод, который мог быть сохранён по промо-ссылке."
    )


def _promo_status_page_text() -> str:
    """Default text of the result of processing a promotional code or coupon."""
    return (
        "%промо_статус_заголовок%\n\n"
        "%промо_статус_текст%"
    )


def _key_status_page_text() -> str:
    """Default text of the key operation status."""
    return (
        "%ключ_статус_заголовок%\n\n"
        "%ключ_статус_текст%"
    )


def _show_id_page_text() -> str:
    """Default text of the Telegram ID page."""
    return (
        "🆔 <b>Ваш Telegram ID</b>\n\n"
        "<code>%telegram_id%</code>"
    )


def _prepayment_unavailable_page_text() -> str:
    """Page defaults when purchase methods are not available."""
    return (
        "💳 <b>Купить ключ</b>\n\n"
        "😔 К сожалению, сейчас оплата недоступна.\n\n"
        "Попробуйте позже или обратитесь в поддержку."
    )


def _access_blocked_page_text() -> str:
    """Blocked access page default."""
    return (
        "⛔ <b>Доступ заблокирован</b>\n\n"
        "Ваш аккаунт заблокирован. Обратитесь в поддержку."
    )


def _empty_page_buttons() -> str:
    """Default without page buttons."""
    return '[]'


def _home_only_page_buttons() -> str:
    """Default button to return to home."""
    return json.dumps([
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _referral_new_ref_notification_text() -> str:
    """Hidden default notification to the referral provider about a new referral."""
    return (
        "👥 <b>Новый реферал</b>\n\n"
        "По вашей ссылке зарегистрировался пользователь.\n\n"
        "👤 Имя: <b>%реферал_имя%</b>\n"
        "🔗 Логин: %реферал_логин%\n"
        "📊 Уровень: <b>%реферальный_уровень%</b>"
    )


def _referral_purchase_notification_text() -> str:
    """Hidden default notification to the referral provider about the purchase of a referral."""
    return (
        "💳 <b>Покупка реферала</b>\n\n"
        "Пользователь <b>%покупатель_имя%</b> (%покупатель_логин%) оплатил тариф.\n\n"
        "🎫 Тариф: <b>%платеж_тариф%</b>\n"
        "💵 Сумма: <b>%платеж_сумма%</b>\n"
        "⏳ Срок: <b>%платеж_срок%</b>\n"
        "🎁 Ваш бонус: <b>%реферальное_вознаграждение%</b>\n"
        "📊 Уровень: <b>%реферальный_уровень%</b>"
    )


def _key_navigation_page_buttons() -> str:
    """Static navigation buttons after key operations."""
    return json.dumps([
        {"id": "btn_help",      "label": "📄 Инструкция", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
        {"id": "btn_my_keys",   "label": "🔑 Мои ключи", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _key_details_page_buttons() -> str:
    """Key card buttons: actions and bottom navigation."""
    return json.dumps([
        {"id": "btn_key_show_key",          "label": "📋 Показать ключ",      "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_show_subscription", "label": "📋 Показать подписку", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_configure",         "label": "⚙️ Настроить",         "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_renew",             "label": "📈 Продлить",          "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_replace",           "label": "🔄 Заменить",          "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_delete",            "label": "🗑 Удалить",           "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_key_rename",            "label": "✏️ Переименовать",    "color": "secondary", "row": 1, "col": 1, "is_hidden": False, "action_type": "system",   "action_value": None},
        {"id": "btn_my_keys",               "label": "🔑 Мои ключи",         "color": "secondary", "row": 2, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
        {"id": "btn_back_main",             "label": "🈴 На главную",        "color": "secondary", "row": 2, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _renew_payment_unavailable_buttons() -> str:
    """Page buttons when renewal options are not available."""
    return json.dumps([
        {"id": "btn_renew_back", "label": "⬅️ Назад", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main",  "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
    ], ensure_ascii=False)


def _key_details_page_text() -> str:
    """Default of a specific key card."""
    return "%ключ_информация%\n%ключ_история_операций%"


def _key_show_unconfigured_page_text() -> str:
    """Default of the page showing a key that has not yet been configured."""
    return (
        "📋 <b>Показать ключ</b>\n\n"
        "⚠️ Ключ ещё не создан на сервере.\n"
        "Обратитесь в поддержку."
    )


def _renew_payment_unavailable_page_text() -> str:
    """Unavailable renewal page default."""
    return (
        "💳 <b>Продление ключа</b>\n\n"
        "😔 Способы оплаты временно недоступны.\n"
        "Попробуйте позже."
    )


def _key_replace_server_select_page_text() -> str:
    """Server selection default for key replacement."""
    return (
        "🔄 <b>Замена ключа</b>\n\n"
        "%экран_данные%\n\n"
        "Выберите сервер:"
    )


def _key_replace_inbound_select_page_text() -> str:
    """Protocol selection default for key replacement."""
    return (
        "🖥️ <b>Выбор протокола</b>\n\n"
        "%экран_данные%\n\n"
        "Выберите протокол:"
    )


def _key_replace_confirm_page_text() -> str:
    """Key replacement confirmation default."""
    return (
        "⚠️ <b>Подтверждение замены</b>\n\n"
        "%замена_ключа_данные%\n\n"
        "Вы уверены?"
    )


def _key_rename_prompt_page_text() -> str:
    """New key name request defaulted."""
    return (
        "✏️ <b>Переименование ключа</b>\n\n"
        "%ключ_переименование_данные%\n\n"
        "Введите новое название для ключа (макс. 30 символов):\n"
        "<i>(Отправьте любой текст)</i>"
    )


def _new_key_server_select_page_text() -> str:
    """Server selection default after payment."""
    return (
        "🎉 <b>Оплата прошла успешно!</b>\n\n"
        "%экран_данные%"
    )


def _new_key_inbound_select_page_text() -> str:
    """Protocol selection default after payment."""
    return (
        "🖥️ <b>Выбор протокола</b>\n\n"
        "%экран_данные%\n\n"
        "Выберите протокол:"
    )


def _new_key_no_servers_page_text() -> str:
    """The page defaults to no servers after payment."""
    return (
        "🎉 <b>Оплата прошла успешно!</b>\n\n"
        "⚠️ К сожалению, сейчас нет доступных серверов.\n"
        "Пожалуйста, свяжитесь с поддержкой."
    )


def _key_runtime_page_defaults() -> dict:
    """Defaults on key pages edited only via /yaa."""
    return {
        'key_details': (_key_details_page_text(), _key_details_page_buttons()),
        'key_show_unconfigured': (_key_show_unconfigured_page_text(), _key_navigation_page_buttons()),
        'renew_payment_unavailable': (_renew_payment_unavailable_page_text(), _renew_payment_unavailable_buttons()),
        'key_replace_server_select': (_key_replace_server_select_page_text(), _empty_page_buttons()),
        'key_replace_inbound_select': (_key_replace_inbound_select_page_text(), _empty_page_buttons()),
        'key_replace_confirm': (_key_replace_confirm_page_text(), _empty_page_buttons()),
        'key_rename_prompt': (_key_rename_prompt_page_text(), _empty_page_buttons()),
        'new_key_server_select': (_new_key_server_select_page_text(), _empty_page_buttons()),
        'new_key_inbound_select': (_new_key_inbound_select_page_text(), _empty_page_buttons()),
        'new_key_no_servers': (_new_key_no_servers_page_text(), _home_only_page_buttons()),
    }


def get_current_version() -> int:
    """
    Gets the current version of the database schema.
    
    Returns:
        int: Version number (0 if version table does not exist)
    """
    with get_db() as conn:
        # Checking the existence of the schema_version table
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
    Sets the database schema version.
    
    Args:
        conn: Connection to the database
        version: Version number
    """
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


# ═══════════════════════════════════════════════════════════════════════════════
# Initial migration (v1–v21 compression)
# ═══════════════════════════════════════════════════════════════════════════════

def migration_initial(conn: sqlite3.Connection) -> None:
    """
    Initial migration: creates a complete up-to-date database schema (v21).
    
    Called only on new installations (version = 0).
    Condenses v1–v21 migrations into a single function.
    
    Tables:
    - schema_version: schema version
    - settings: global bot settings
    - users: Telegram users
    - tariffs: tariff plans
    - tariff_groups: tariff groups
    - servers: VPN servers (3X-UI)
    - server_groups: connection of servers with groups (many-to-many)
    - vpn_keys: user keys/subscriptions
    - payments: payment history
    - notification_log: notification log
    - referral_levels: referral system levels
    - referral_stats: referral statistics
    - pages: user interface pages
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
         '⚠️ <b>Ваш VPN-ключ %ключ_имя% скоро истекает!</b>\n\n'
         'Через %ключ_дней_до_окончания% дней закончится срок действия вашего ключа.\n\n'
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
         '⚠️ По ключу <b>%ключ_имя%</b> осталось %ключ_трафик_процент_остатка%% трафика (%ключ_трафик_использовано% из %ключ_трафик_лимит%)'),
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
        ('update_notifications_enabled', '1'),
        ('display_timezone', 'Europe/Moscow'),
        ('my_keys_item_template', _my_keys_item_template()),
        # The bot operating mode for new installations is Subscription
        # (the bot issues a subscription URL, keys in all inbound with a single subId).
        # On existing bots migration_28 sets 'key' - there are already workers there
        # single keys, and the mode cannot be changed without explicit action by the administrator.
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

    # Hidden tariff for admin keys
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
            media_type_default TEXT,
            buttons_default  TEXT NOT NULL DEFAULT '[]',
            text_custom      TEXT,
            image_custom     TEXT,
            media_type_custom TEXT,
            updated_at       TIMESTAMP,
            buttons_custom   TEXT,
            guard_names      TEXT NOT NULL DEFAULT '[]',
            hook_names       TEXT NOT NULL DEFAULT '[]'
        )
    """)

    # Default page data (texts in HTML, buttons in JSON)
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
                {"id": "btn_pay_cards",   "label": "💳 TG payments",           "color": "primary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_qr",      "label": "📱 ЮКасса",                "color": "primary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_wata",    "label": "🌊 WATA",                  "color": "primary",   "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_platega", "label": "💸 Platega",               "color": "primary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_cardlink", "label": "🔗 Cardlink",             "color": "primary",   "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_demo",    "label": "🏦 Демо оплата (РФ карта)", "color": "primary",   "row": 7, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_balance", "label": "💎 Использовать баланс",    "color": "primary",   "row": 8, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_back_main",   "label": "🈴 На главную",             "color": "secondary", "row": 9, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
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
                "<code>%реферальная_ссылка%</code>\n\n"
                "━━━━━━━━━━━━━━━\n"
                "📝 <b>Условия:</b>\n"
                "Приглашённые пользователи регистрируются по вашей ссылке. "
                "Когда они оплачивают подписку, вы получаете реферальное вознаграждение.\n\n"
                "━━━━━━━━━━━━━━━\n"
                "%реферальная_статистика%"
            ),
            'buttons': json.dumps([
                {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'key_delivery': {
            'text': (
                "✅ <b>Ваш VPN-ключ!</b>\n\n"
                "%ключ_для_копирования%\n"
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
# Incremental migrations (added below as the project develops)
# ═══════════════════════════════════════════════════════════════════════════════

# Example of adding a new migration:
#
def migration_22(conn):
    """
    Migration v22: removal of standard crypto payment mode.
    
    - Removes the crypto_integration_mode setting from settings
    - Removes the external_id column from the tariffs table
    """
    # 1. Remove the crypto_integration_mode setting
    conn.execute("DELETE FROM settings WHERE key = 'crypto_integration_mode'")
    
    # 2. Remove the external_id column from tariffs
    # ALTER TABLE DROP COLUMN supported since SQLite 3.35.0 (March 2021)
    # Fallback via table re-creation for old versions
    try:
        conn.execute("ALTER TABLE tariffs DROP COLUMN external_id")
        logger.info("Колонка external_id удалена через DROP COLUMN")
    except Exception as e:
        if "no such column" in str(e).lower():
            # The column is no longer there - everything is ok
            logger.info("Колонка external_id уже отсутствует — пропускаем")
        else:
            # Old SQLite - recreate the table without external_id
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
    Migration v23: adding WATA payment method.

    - Adds wata_enabled and wata_jwt_token settings
    - Adds the wata_link_id column to the payments table
    - Adds the btn_pay_wata button to the default layout of the prepayment page
    """
    # 1. WATA settings
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wata_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wata_jwt_token', '')")

    # 2. Column wata_link_id for tracking WATA payments
    _add_column(conn, "payments", "wata_link_id TEXT")

    # 3. Update the buttons_default of the prepayment page - insert btn_pay_wata after btn_pay_qr
    cursor = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'prepayment'")
    row = cursor.fetchone()
    if row:
        try:
            buttons = json.loads(row['buttons_default'])
        except (json.JSONDecodeError, TypeError):
            buttons = []

        existing_ids = {b.get('id') for b in buttons if isinstance(b, dict)}
        if 'btn_pay_wata' not in existing_ids:
            # Find the line btn_pay_qr and insert wata after it, shifting the remaining lines
            qr_row = None
            for b in buttons:
                if isinstance(b, dict) and b.get('id') == 'btn_pay_qr':
                    qr_row = b.get('row', 0)
                    break

            if qr_row is None:
                # No btn_pay_qr - insert before btn_back_main or at the end
                max_row = max((b.get('row', 0) for b in buttons if isinstance(b, dict)), default=-1)
                new_row = max_row + 1
                # If the last button is btn_back_main, insert it before it
                for b in buttons:
                    if isinstance(b, dict) and b.get('id') == 'btn_back_main':
                        new_row = b.get('row', new_row)
                        b['row'] = new_row + 1
                        break
                buttons.append({
                    "id": "btn_pay_wata",
                    "label": "🌊 WATA",
                    "color": "primary",
                    "row": new_row,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })
            else:
                # Shift all rows > qr_row down by 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('row', 0) > qr_row:
                        b['row'] = b['row'] + 1
                buttons.append({
                    "id": "btn_pay_wata",
                    "label": "🌊 WATA",
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
    Migration v24: adding the Platega payment method.

    - Adds settings platega_enabled, platega_merchant_id, platega_secret
    - Adds the platega_transaction_id column to the payments table
    - Adds the btn_pay_platega button to the default layout of the prepayment page
    """
    # 1. Platega settings
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_merchant_id', '')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('platega_secret', '')")

    # 2. Column platega_transaction_id for tracking Platega payments
    _add_column(conn, "payments", "platega_transaction_id TEXT")

    # 3. Update the buttons_default of the prepayment page - insert btn_pay_platega after btn_pay_wata
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
                # No btn_pay_wata - insert before btn_back_main or at the end
                max_row = max((b.get('row', 0) for b in buttons if isinstance(b, dict)), default=-1)
                new_row = max_row + 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('id') == 'btn_back_main':
                        new_row = b.get('row', new_row)
                        b['row'] = new_row + 1
                        break
                buttons.append({
                    "id": "btn_pay_platega",
                    "label": "💸 Platega",
                    "color": "primary",
                    "row": new_row,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "system",
                    "action_value": None,
                })
            else:
                # Shift all rows > wata_row down by 1
                for b in buttons:
                    if isinstance(b, dict) and b.get('row', 0) > wata_row:
                        b['row'] = b['row'] + 1
                buttons.append({
                    "id": "btn_pay_platega",
                    "label": "💸 Platega",
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

    logger.info("Миграция v24 применена: добавлен платёжный метод Platega")


def migration_25(conn):
    """
    Migration v25: adding the Cardlink payment method (cardlink.link).

    - Adds settings cardlink_enabled, cardlink_shop_id, cardlink_api_token
    - Adds the cardlink_bill_id column to the payments table
    - Adds the btn_pay_cardlink button to the default layout of the prepayment page
    """
    # 1. Cardlink settings
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_shop_id', '')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cardlink_api_token', '')")

    # 2. Cardlink_bill_id column for tracking Cardlink payments
    _add_column(conn, "payments", "cardlink_bill_id TEXT")

    # 3. Update the buttons_default of the prepayment page - insert btn_pay_cardlink after btn_pay_platega
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
                    "label": "🔗 Cardlink",
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
                    "label": "🔗 Cardlink",
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

    logger.info("Миграция v25 применена: добавлен платёжный метод Cardlink")


def migration_26(conn):
    """
    Migration v26: adding time settings for daily tasks and checking for updates.
    """
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_tasks_time', '03:00')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('update_check_time', '12:00')")
    logger.info("Миграция v26 применена: добавлены настройки daily_tasks_time и update_check_time")


def migration_27(conn):
    """
    Migration v27: adding api_token column to servers to support 3x-ui v3.0+.

    On v3.0+, the panel requires a CSRF token on all POST requests, but has an alternative -
    Bearer token via Authorization header, which completely bypasses CSRF.
    The bot automatically pulls the token via GET /panel/setting/getApiToken after
    the first successful login to the v3.0+ panel and saves it in this field.
    For v2.x panels, the field remains NULL - the old cookie flow is used.
    """
    _add_column(conn, "servers", "api_token TEXT")
    logger.info("Миграция v27 применена: добавлена колонка servers.api_token для 3x-ui v3.0+")


def migration_28(conn):
    """
    Migration v28: introduction of Subscription mode.

    - Adds vpn_keys.sub_id - subscription identifier (common for all clients
      with this email on the same server). NULL for legacy keys (Keys mode).
    - Creates an index (server_id, panel_email) for quickly searching for clients
      one subscription in synchronization.
    - Sets bot_mode='key' for existing bots: they are already running
      with single keys, and you cannot change the mode without an explicit decision from the admin.
      On new installations migration_initial puts 'subscription' before -
      INSERT OR IGNORE below will not overwrite it.
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
    Migration v29: normal style of default home page buttons.

    Only changes pages.buttons_default for the main page.
    pages.buttons_custom is not touched: custom settings remain
    custom and have priority when rendering.
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
    Migration v30: adding the max_ips field to the tariffs table.
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
    Migration v31: moving the choice of payment method during renewal to the pages table.

    Creates a renew_payment page with default text and system buttons.
    Custom fields text_custom/image_custom/buttons_custom are not changed.
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
    Migration v32: 3x-ui panel diagnostic cache.

    panel_version stores a specific version of the panel, panel_api_profile stores the selected one
    API profile ('legacy_inbounds' or 'clients_api'), panel_checked_at - time
    last successful check.
    """
    _add_column(conn, "servers", "panel_version TEXT")
    _add_column(conn, "servers", "panel_api_profile TEXT")
    _add_column(conn, "servers", "panel_checked_at TEXT")
    logger.info("Миграция v32 применена: добавлены поля диагностики 3x-ui в servers")


def migration_33(conn):
    """
    Migration v33: moving the “My Keys” page to the pages table.

    Creates the my_keys/my_keys_empty pages and a hidden format setting for one
    key Custom page fields do not change.
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
    v34 migration: Migration of additional custom key screens to pages.

    Updates only default fields. Custom text, image and buttons
    administrators remain unchanged.
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
    Migration v35: user unavailable flag for bot messages.

    The is_bot_blocked field marks users who have blocked the bot in Telegram.
    Such users are excluded from mass sendings until they contact the bot again.
    """
    _add_column(conn, "users", "is_bot_blocked INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_is_bot_blocked ON users(is_bot_blocked)")
    logger.info("Миграция v35 применена: добавлен флаг is_bot_blocked")


def migration_36(conn):
    """
    Migration v36: hidden time zone setting for date display.

    SQLite continues to store dates in UTC, the setting only affects the output in Telegram.
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
    Migration v37: hidden notifications to referrer and username.

    first_name/last_name are needed for the name placeholder in notifications.
    Notification settings remain hidden and can only be changed through the database.
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


def migration_38(conn):
    """Migration v38: hidden notification switch for new versions."""
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('update_notifications_enabled', '1')
        """
    )
    logger.info("Миграция v38 применена: добавлена настройка уведомлений об обновлениях")


def migration_39(conn):
    """Migration v39: media type for editable pages."""
    _add_column(conn, "pages", "media_type_default TEXT")
    _add_column(conn, "pages", "media_type_custom TEXT")
    conn.execute(
        """
        UPDATE pages
        SET media_type_default = 'photo'
        WHERE image_default IS NOT NULL
          AND image_default != ''
          AND media_type_default IS NULL
        """
    )
    conn.execute(
        """
        UPDATE pages
        SET media_type_custom = 'photo'
        WHERE image_custom IS NOT NULL
          AND image_custom != ''
          AND media_type_custom IS NULL
        """
    )
    logger.info("Миграция v39 применена: добавлены типы медиа для pages")


def migration_40(conn):
    """Migration v40: indexes for database growth and frequent queries."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username))")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_expires "
        "ON vpn_keys(user_id, expires_at DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_server_id ON vpn_keys(server_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vpn_keys_panel_email_lower "
        "ON vpn_keys(LOWER(panel_email))"
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status_paid_at ON payments(status, paid_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_key_status_paid_at "
        "ON payments(vpn_key_id, status, paid_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_yookassa_payment_id "
        "ON payments(yookassa_payment_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_wata_link_id ON payments(wata_link_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_platega_transaction_id "
        "ON payments(platega_transaction_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_cardlink_bill_id "
        "ON payments(cardlink_bill_id)"
    )
    conn.execute("PRAGMA optimize")
    logger.info("Миграция v40 применена: добавлены индексы для частых запросов")


def _update_standard_button_label(conn, page_key: str, button_id: str, replacements: dict) -> int:
    row = conn.execute(
        "SELECT buttons_default FROM pages WHERE page_key = ?",
        (page_key,),
    ).fetchone()
    if not row:
        return 0

    try:
        buttons = json.loads(row["buttons_default"] or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Миграция v41: buttons_default страницы {page_key} не является JSON, пропускаем")
        return 0

    if not isinstance(buttons, list):
        logger.warning(f"Миграция v41: buttons_default страницы {page_key} не является списком, пропускаем")
        return 0

    changed = 0
    for button in buttons:
        if not isinstance(button, dict) or button.get("id") != button_id:
            continue

        label = button.get("label")
        if label in replacements:
            button["label"] = replacements[label]
            changed += 1

    if changed:
        conn.execute(
            "UPDATE pages SET buttons_default = ? WHERE page_key = ?",
            (json.dumps(buttons, ensure_ascii=False), page_key),
        )

    return changed


def migration_41(conn):
    """Migration v41: neutral Platega labels without reference to SBP."""
    changed = 0
    changed += _update_standard_button_label(
        conn,
        "prepayment",
        "btn_pay_platega",
        {
            "💸 Оплата Platega (СБП)": "💸 Platega",
            "💸 Platega (СБП)": "💸 Platega",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "renew_payment",
        "btn_renew_pay_platega",
        {
            "💸 Platega (СБП)": "💸 Platega",
            "💸 Оплата Platega (СБП)": "💸 Platega",
        },
    )
    logger.info(f"Миграция v41 применена: обновлено стандартных labels Platega: {changed}")


def _move_key_details_custom_navigation_to_bottom(conn) -> int:
    """Moves the old custom key_details navigation buttons to the bottom row."""
    row = conn.execute(
        "SELECT buttons_custom FROM pages WHERE page_key = 'key_details'"
    ).fetchone()
    if not row or not row["buttons_custom"]:
        return 0

    try:
        buttons = json.loads(row["buttons_custom"] or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning("Миграция v42: buttons_custom страницы key_details не является JSON, пропускаем")
        return 0

    if not isinstance(buttons, list):
        logger.warning("Миграция v42: buttons_custom страницы key_details не является списком, пропускаем")
        return 0

    changed = 0
    for button in buttons:
        if not isinstance(button, dict):
            continue

        btn_id = button.get("id")
        try:
            current_row = int(button.get("row", 0))
        except (TypeError, ValueError):
            current_row = 0

        if btn_id == "btn_my_keys" and current_row == 0:
            button["row"] = 2
            button["col"] = 0
            changed += 1
        elif btn_id == "btn_back_main" and current_row == 0:
            button["row"] = 2
            button["col"] = 1
            changed += 1

    if changed:
        conn.execute(
            "UPDATE pages SET buttons_custom = ? WHERE page_key = 'key_details'",
            (json.dumps(buttons, ensure_ascii=False),),
        )

    return changed


def migration_42(conn):
    """Migration v42: key card action buttons are stored in pages."""
    buttons_default = _key_details_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('key_details', ?, ?)
        """,
        (_key_details_page_text(), buttons_default),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'key_details'",
        (buttons_default,),
    )
    moved = _move_key_details_custom_navigation_to_bottom(conn)
    logger.info(f"Миграция v42 применена: key_details.buttons_default обновлён, перенесено custom-кнопок: {moved}")


def migration_43(conn):
    """
    Migration v43: uniform public names of payment methods.

    Only changes pages.buttons_default. Custom labels for administrators
    in pages.buttons_custom are not touched and continue to have priority.
    """
    changed = 0

    changed += _update_standard_button_label(
        conn,
        "prepayment",
        "btn_pay_cards",
        {
            "💳 Оплатить картой": "💳 TG payments",
            "💳 Оплата картой": "💳 TG payments",
            "💳 Банковские карты (ЮКасса)": "💳 TG payments",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "prepayment",
        "btn_pay_qr",
        {
            "📱 QR-оплата": "📱 ЮКасса",
            "📱 QR-оплата (Карта/СБП)": "📱 ЮКасса",
            "📱 ЮКасса (QR/СБП)": "📱 ЮКасса",
            "💳 ЮКасса (QR/СБП)": "📱 ЮКасса",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "prepayment",
        "btn_pay_wata",
        {
            "🌊 WATA (Карта/СБП)": "🌊 WATA",
            "🌊 Оплата WATA (Карта/СБП)": "🌊 WATA",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "prepayment",
        "btn_pay_cardlink",
        {
            "🔗 Cardlink (Карта/СБП)": "🔗 Cardlink",
            "🔗 Оплата Cardlink (Карта/СБП)": "🔗 Cardlink",
        },
    )

    changed += _update_standard_button_label(
        conn,
        "renew_payment",
        "btn_renew_pay_cards",
        {
            "💳 Оплатить картой": "💳 TG payments",
            "💳 Оплата картой": "💳 TG payments",
            "💳 Банковские карты (ЮКасса)": "💳 TG payments",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "renew_payment",
        "btn_renew_pay_qr",
        {
            "📱 QR-оплата": "📱 ЮКасса",
            "📱 QR-оплата (Карта/СБП)": "📱 ЮКасса",
            "📱 ЮКасса (QR/СБП)": "📱 ЮКасса",
            "💳 ЮКасса (QR/СБП)": "📱 ЮКасса",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "renew_payment",
        "btn_renew_pay_wata",
        {
            "🌊 WATA (Карта/СБП)": "🌊 WATA",
            "🌊 Оплата WATA (Карта/СБП)": "🌊 WATA",
        },
    )
    changed += _update_standard_button_label(
        conn,
        "renew_payment",
        "btn_renew_pay_cardlink",
        {
            "🔗 Cardlink (Карта/СБП)": "🔗 Cardlink",
            "🔗 Оплата Cardlink (Карта/СБП)": "🔗 Cardlink",
        },
    )

    logger.info(f"Миграция v43 применена: обновлено стандартных labels оплат: {changed}")


def _add_main_support_button(conn) -> bool:
    """Adds a hidden support button to the default home page."""
    row = conn.execute("SELECT buttons_default FROM pages WHERE page_key = 'main'").fetchone()
    if not row:
        logger.info("Миграция v44: страница main не найдена, кнопку поддержки добавить некуда")
        return False

    try:
        buttons = json.loads(row["buttons_default"] or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning("Миграция v44: buttons_default страницы main не является JSON, пропускаем кнопку поддержки")
        return False

    if not isinstance(buttons, list):
        logger.warning("Миграция v44: buttons_default страницы main не является списком, пропускаем кнопку поддержки")
        return False

    for button in buttons:
        if isinstance(button, dict) and button.get("id") == "btn_support":
            return False

    buttons.append({
        "id": "btn_support",
        "label": "💬 Написать в поддержку",
        "color": "secondary",
        "row": 3,
        "col": 0,
        "is_hidden": True,
        "action_type": "internal",
        "action_value": "cmd_support",
    })
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'main'",
        (json.dumps(buttons, ensure_ascii=False),),
    )
    return True


def migration_44(conn):
    """Migration v44: built-in support system."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_telegram_id INTEGER NOT NULL,
            initiator_type TEXT NOT NULL CHECK (initiator_type IN ('user', 'admin')),
            initiator_admin_id INTEGER,
            assigned_admin_id INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_support_threads_user "
        "ON support_threads(user_telegram_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_support_threads_assigned "
        "ON support_threads(assigned_admin_id, updated_at)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'admin')),
            sender_telegram_id INTEGER NOT NULL,
            recipient_telegram_id INTEGER,
            text_html TEXT NOT NULL DEFAULT '',
            media_type TEXT,
            media_file_id TEXT,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES support_threads(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_support_messages_thread "
        "ON support_messages(thread_id, created_at)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS support_admin_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            admin_telegram_id INTEGER NOT NULL,
            card_message_id INTEGER,
            copy_message_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES support_threads(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_support_admin_notifications_thread "
        "ON support_admin_notifications(thread_id, is_active)"
    )

    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) "
        "VALUES ('support_claim_cleanup_mode', 'remove_button')"
    )
    added_button = _add_main_support_button(conn)
    logger.info(
        "Миграция v44 применена: таблицы поддержки созданы, кнопка main добавлена: %s",
        added_button,
    )


def _add_system_button_to_page_start(
    conn: sqlite3.Connection,
    page_key: str,
    button: dict,
) -> bool:
    """Adds a system button to the beginning of the default page layout."""
    cursor = conn.execute("SELECT buttons_default FROM pages WHERE page_key = ?", (page_key,))
    row = cursor.fetchone()
    if not row:
        return False

    try:
        buttons = json.loads(row["buttons_default"] or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning("Миграция v45: buttons_default страницы %s не является JSON", page_key)
        return False
    if not isinstance(buttons, list):
        logger.warning("Миграция v45: buttons_default страницы %s не является списком", page_key)
        return False

    button_id = button.get("id")
    if any(isinstance(item, dict) and item.get("id") == button_id for item in buttons):
        return False

    for item in buttons:
        if isinstance(item, dict):
            item["row"] = int(item.get("row") or 0) + 1

    buttons.append(button)
    buttons.sort(key=lambda item: (int(item.get("row") or 0), int(item.get("col") or 0)))
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = ?",
        (json.dumps(buttons, ensure_ascii=False), page_key),
    )
    return True


def migration_45(conn):
    """Migration v45: promotional codes, promotional links and one-time coupons."""
    _add_column(conn, "users", "active_promo_code_id INTEGER")

    _add_column(conn, "payments", "promo_code_id INTEGER")
    _add_column(conn, "payments", "promo_code TEXT")
    _add_column(conn, "payments", "discount_percent INTEGER DEFAULT 0")
    _add_column(conn, "payments", "original_amount_cents INTEGER")
    _add_column(conn, "payments", "discount_amount_cents INTEGER DEFAULT 0")
    _add_column(conn, "payments", "final_amount_cents INTEGER")
    _add_column(conn, "payments", "original_amount_stars INTEGER")
    _add_column(conn, "payments", "discount_amount_stars INTEGER DEFAULT 0")
    _add_column(conn, "payments", "final_amount_stars INTEGER")
    _add_column(conn, "payments", "is_promo_free INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('promo', 'coupon')),
            code TEXT NOT NULL UNIQUE,
            discount_percent INTEGER NOT NULL DEFAULT 0 CHECK (discount_percent >= 0 AND discount_percent <= 100),
            expires_at TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1,
            activation_limit INTEGER,
            usage_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',
            issued_to_user_id INTEGER,
            created_by_admin_id INTEGER,
            snapshot_discount_percent INTEGER,
            snapshot_lifetime_days INTEGER,
            snapshot_generated_at TIMESTAMP,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (issued_to_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_type ON promo_codes(type, is_active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_expires ON promo_codes(expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_source ON promo_codes(source)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            code TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'applied', 'canceled')),
            payment_type TEXT,
            action TEXT,
            original_amount INTEGER NOT NULL DEFAULT 0,
            discount_amount INTEGER NOT NULL DEFAULT 0,
            final_amount INTEGER NOT NULL DEFAULT 0,
            amount_unit TEXT NOT NULL DEFAULT 'cents',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied_at TIMESTAMP,
            FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redemptions_order "
        "ON promo_redemptions(order_id) WHERE status != 'canceled'"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code_status ON promo_redemptions(promo_code_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_redemptions_user ON promo_redemptions(user_id, created_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_link_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            user_id INTEGER,
            telegram_id INTEGER NOT NULL,
            start_param TEXT NOT NULL,
            converted_order_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            converted_at TIMESTAMP,
            FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_link_visits_code ON promo_link_visits(promo_code_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_link_visits_user ON promo_link_visits(user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_promo_code_id ON payments(promo_code_id)")

    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('coupon_auto_enabled', '0')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('coupon_auto_discount_percent', '10')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('coupon_auto_lifetime_days', '90')")

    prepayment_button_added = _add_system_button_to_page_start(
        conn,
        "prepayment",
        {
            "id": "btn_enter_promo",
            "label": "🎟 Ввести промокод",
            "color": "primary",
            "row": 0,
            "col": 0,
            "is_hidden": False,
            "action_type": "system",
            "action_value": None,
        },
    )
    renew_button_added = _add_system_button_to_page_start(
        conn,
        "renew_payment",
        {
            "id": "btn_renew_enter_promo",
            "label": "🎟 Ввести промокод",
            "color": "secondary",
            "row": 0,
            "col": 0,
            "is_hidden": False,
            "action_type": "system",
            "action_value": None,
        },
    )

    logger.info(
        "Миграция v45 применена: промокоды и купоны добавлены, кнопки оплаты: prepayment=%s, renew=%s",
        prepayment_button_added,
        renew_button_added,
    )


_LEGACY_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')


PAGE_CONSTRUCTOR_PLACEHOLDER_RENAMES = {
    '%статистика%': '%реферальная_статистика%',
    '%ключ%': '%ключ_для_копирования%',
    '%списокключей%': '%список_ключей%',
    '%информацияключа%': '%ключ_информация%',
    '%историяопераций%': '%ключ_история_операций%',
    '%имяключа%': '%ключ_имя%',
    '%данныеэкрана%': '%экран_данные%',
    '%данныезамены%': '%замена_ключа_данные%',
    '%данныеключа%': '%ключ_переименование_данные%',
}


PAGE_SPECIFIC_PLACEHOLDER_RENAMES = {
    'referral': {
        '%ссылка%': '%реферальная_ссылка%',
    },
    'key_delivery': {
        '%ссылка%': '%ключ_ссылка%',
    },
}


PAGE_URL_ACTION_PLACEHOLDER_RENAMES = {
    'referral': {
        '%ссылка%': '%реферальная_ссылка_url%',
    },
    'key_delivery': {
        '%ключ%': '%ключ_ссылка_url%',
        '%ссылка%': '%ключ_ссылка_url%',
    },
}


MY_KEYS_ITEM_PLACEHOLDER_RENAMES = {
    '%статус%': '%ключ_статус%',
    '%имяключа%': '%ключ_имя%',
    '%трафик%': '%ключ_трафик%',
    '%датаокончания%': '%ключ_дата_окончания%',
    '%сервер%': '%ключ_сервер%',
    '%инбаунд%': '%ключ_инбаунд%',
    '%протокол%': '%ключ_протокол%',
    '%id%': '%ключ_id%',
}


def _replace_legacy_page_placeholders(
    text: str | None,
    page_key: str,
    *,
    url_action_value: bool = False,
) -> str | None:
    """Replaces old constructor placeholders with canonical names."""
    if text is None:
        return None

    replacements = dict(PAGE_CONSTRUCTOR_PLACEHOLDER_RENAMES)
    replacements.update(PAGE_SPECIFIC_PLACEHOLDER_RENAMES.get(page_key, {}))
    if url_action_value:
        replacements.update(PAGE_URL_ACTION_PLACEHOLDER_RENAMES.get(page_key, {}))
    normalized = {key.casefold(): value for key, value in replacements.items()}

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return normalized.get(placeholder.casefold(), placeholder)

    return _LEGACY_PLACEHOLDER_RE.sub(replace_match, text)


def _replace_legacy_template_placeholders(text: str | None) -> str | None:
    """Replaces the placeholders of a hidden template of one key line."""
    if text is None:
        return None
    normalized = {key.casefold(): value for key, value in MY_KEYS_ITEM_PLACEHOLDER_RENAMES.items()}

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return normalized.get(placeholder.casefold(), placeholder)

    return _LEGACY_PLACEHOLDER_RE.sub(replace_match, text)


def _replace_button_placeholders(buttons_json: str | None, page_key: str) -> tuple[str | None, bool]:
    """Rewrites placeholders in the label/action_value of JSON page buttons."""
    if not buttons_json:
        return buttons_json, False

    try:
        buttons = json.loads(buttons_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Миграция v46: кнопки страницы %s не являются JSON, пропускаем", page_key)
        return buttons_json, False

    if not isinstance(buttons, list):
        logger.warning("Миграция v46: кнопки страницы %s не являются списком, пропускаем", page_key)
        return buttons_json, False

    changed = False
    for button in buttons:
        if not isinstance(button, dict):
            continue
        for field in ('label', 'action_value'):
            value = button.get(field)
            if not isinstance(value, str):
                continue
            updated = _replace_legacy_page_placeholders(
                value,
                page_key,
                url_action_value=field == 'action_value' and button.get('action_type') == 'url',
            )
            if updated != value:
                button[field] = updated
                changed = True

    if not changed:
        return buttons_json, False
    return json.dumps(buttons, ensure_ascii=False), True


def migration_46(conn):
    """Migration v46: canonical placeholders for page and button builder."""
    page_rows = conn.execute(
        """
        SELECT page_key, text_default, text_custom, buttons_default, buttons_custom
        FROM pages
        """
    ).fetchall()

    changed_pages = 0
    changed_button_sets = 0
    for row in page_rows:
        page_key = row["page_key"]
        updates = {}

        for field in ('text_default', 'text_custom'):
            value = row[field]
            updated = _replace_legacy_page_placeholders(value, page_key)
            if updated != value:
                updates[field] = updated

        for field in ('buttons_default', 'buttons_custom'):
            updated, changed = _replace_button_placeholders(row[field], page_key)
            if changed:
                updates[field] = updated
                changed_button_sets += 1

        if updates:
            assignments = ', '.join(f"{field} = ?" for field in updates)
            conn.execute(
                f"UPDATE pages SET {assignments} WHERE page_key = ?",
                [*updates.values(), page_key],
            )
            changed_pages += 1

    setting_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'my_keys_item_template'"
    ).fetchone()
    template_changed = False
    if setting_row:
        current_template = setting_row["value"]
        updated_template = _replace_legacy_template_placeholders(current_template)
        if updated_template != current_template:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'my_keys_item_template'",
                (updated_template,),
            )
            template_changed = True

    logger.info(
        "Миграция v46 применена: страниц обновлено=%s, наборов кнопок=%s, шаблон ключей=%s",
        changed_pages,
        changed_button_sets,
        template_changed,
    )


def migration_47(conn):
    """Migration v47: table of data-driven page builder routes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS page_routes (
            route_key TEXT PRIMARY KEY,
            page_key TEXT NOT NULL,
            guard_names TEXT NOT NULL DEFAULT '[]',
            hook_names TEXT NOT NULL DEFAULT '[]',
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (page_key) REFERENCES pages(page_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_routes_page_key ON page_routes(page_key)"
    )
    logger.info("Миграция v47 применена: таблица page_routes готова")


def migration_48(conn):
    """Migration v48: page-level guards/hooks for direct custom page transitions."""
    _add_column(conn, "pages", "guard_names TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "pages", "hook_names TEXT NOT NULL DEFAULT '[]'")
    logger.info("Миграция v48 применена: pages.guard_names/hook_names готовы")


def migration_49(conn):
    """Migration v49: ready-made custom page for your personal account and route profile."""
    text_default = _custom_profile_page_text()
    buttons_default = _custom_profile_page_buttons()

    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('custom_profile', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'custom_profile'
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        INSERT INTO page_routes (
            route_key, page_key, guard_names, hook_names, is_enabled, updated_at
        )
        VALUES ('profile', 'custom_profile', '["not_banned"]', '[]', 1, CURRENT_TIMESTAMP)
        ON CONFLICT(route_key) DO UPDATE SET
            page_key = excluded.page_key,
            guard_names = excluded.guard_names,
            hook_names = excluded.hook_names,
            is_enabled = 1,
            updated_at = CURRENT_TIMESTAMP
        """
    )
    logger.info("Миграция v49 применена: custom_profile и route profile готовы")


def migration_50(conn):
    """Migration v50: flag for enabling user custom extensions."""
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('custom_extensions_enabled', '0')
        """
    )
    logger.info("Миграция v50 применена: флаг custom_extensions_enabled готов")


def migration_51(conn):
    """Migration v51: custom extension storage/schema system tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_schema_versions (
            extension_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_storage (
            extension_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, key)
        )
        """
    )
    logger.info("Миграция v51 применена: extension storage/schema registry готов")


def migration_52(conn):
    """Migration v52: idempotent key lifecycle events log."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS key_lifecycle_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL REFERENCES vpn_keys(id) ON DELETE CASCADE,
            event_name TEXT NOT NULL,
            event_token TEXT NOT NULL,
            metadata_json TEXT,
            emitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (vpn_key_id, event_name, event_token)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_lifecycle_event_lookup
        ON key_lifecycle_event_log(event_name, vpn_key_id, event_token)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_lifecycle_event_emitted
        ON key_lifecycle_event_log(emitted_at)
        """
    )
    logger.info("Миграция v52 применена: key lifecycle event log готов")


def migration_53(conn):
    """Migration v53: connection of core orders with custom payment providers."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_provider_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL UNIQUE,
            provider_id TEXT NOT NULL,
            payment_type TEXT NOT NULL,
            provider_payment_id TEXT,
            payment_url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_provider
        ON payment_provider_orders(provider_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_external
        ON payment_provider_orders(provider_id, provider_payment_id)
        """
    )
    logger.info("Миграция v53 применена: payment provider orders готовы")


def migration_54(conn):
    """Migration v54: webhook endpoint settings for custom payment providers."""
    defaults = [
        ('custom_payment_webhooks_enabled', '0'),
        ('custom_payment_webhooks_host', '127.0.0.1'),
        ('custom_payment_webhooks_port', '8088'),
        ('custom_payment_webhooks_path_prefix', '/custom-payment-webhook'),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    logger.info("Миграция v54 применена: настройки custom payment webhooks добавлены")


def migration_55(conn):
    """Migration v55: page-backed QR payment text."""
    text_default = _qr_payment_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('qr_payment', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'qr_payment'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v55 применена: страница qr_payment готова")


def migration_56(conn):
    """Migration v56: page-backed text transition to crypto-payment."""
    text_default = _crypto_payment_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('crypto_payment', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'crypto_payment'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v56 применена: страница crypto_payment готова")


def migration_57(conn):
    """Migration v57: page-backed text of payment from balance."""
    text_default = _balance_payment_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('balance_payment', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'balance_payment'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v57 применена: страница balance_payment готова")


def migration_58(conn):
    """Migration v58: page-backed text of demo payment."""
    text_default = _demo_payment_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('demo_payment', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'demo_payment'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v58 применена: страница demo_payment готова")


def migration_59(conn):
    """Migration v59: page-backed text for choosing a payment plan."""
    text_default = _payment_tariff_select_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('payment_tariff_select', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'payment_tariff_select'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v59 применена: страница payment_tariff_select готова")


def migration_60(conn):
    """Migration v60: page-backed payment status text."""
    text_default = _payment_status_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('payment_status', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'payment_status'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v60 применена: страница payment_status готова")


def migration_61(conn):
    """Migration v61: page-backed support login screen."""
    text_default = _support_start_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('support_start', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'support_start'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v61 применена: страница support_start готова")


def migration_62(conn):
    """Migration v62: page-backed promo code entry screen."""
    text_default = _promo_enter_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('promo_enter', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'promo_enter'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v62 применена: страница promo_enter готова")


def migration_63(conn):
    """Migration v63: page-backed Telegram ID screen."""
    text_default = _show_id_page_text()
    buttons_default = _home_only_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('show_id', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'show_id'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v63 применена: страница show_id готова")


def migration_64(conn):
    """Migration v64: page-backed purchase unavailable screen."""
    text_default = _prepayment_unavailable_page_text()
    buttons_default = _home_only_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('prepayment_unavailable', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'prepayment_unavailable'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v64 применена: страница prepayment_unavailable готова")


def migration_65(conn):
    """Migration v65: page-backed blocked access screen."""
    text_default = _access_blocked_page_text()
    buttons_default = _home_only_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('access_blocked', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'access_blocked'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v65 применена: страница access_blocked готова")


def migration_66(conn):
    """Migration v66: page-backed support result screen."""
    text_default = _support_status_page_text()
    buttons_default = _home_only_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('support_status', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'support_status'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v66 применена: страница support_status готова")


def migration_67(conn):
    """Migration v67: page-backed promo code result screen."""
    text_default = _promo_status_page_text()
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('promo_status', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'promo_status'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v67 применена: страница promo_status готова")


def migration_68(conn):
    """Migration v68: page-backed status of key operations."""
    text_default = _key_status_page_text()
    buttons_default = _home_only_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('key_status', ?, ?)
        """,
        (text_default, buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?,
            buttons_default = ?
        WHERE page_key = 'key_status'
        """,
        (text_default, buttons_default),
    )
    logger.info("Миграция v68 применена: страница key_status готова")


def migration_69(conn):
    """Migration v69: removes access to the main page from the server selection after payment."""
    buttons_default = _empty_page_buttons()
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default)
        VALUES ('new_key_server_select', ?, ?)
        """,
        (_new_key_server_select_page_text(), buttons_default),
    )
    conn.execute(
        """
        UPDATE pages
        SET buttons_default = ?
        WHERE page_key = 'new_key_server_select'
        """,
        (buttons_default,),
    )
    logger.info("Миграция v69 применена: new_key_server_select.buttons_default очищен")


EVENT_PLACEHOLDER_RENAMES = {
    'notification_text': {
        '%дней%': '%ключ_дней_до_окончания%',
        '%имяключа%': '%ключ_имя%',
    },
    'traffic_notification_text': {
        '{keyname}': '%ключ_имя%',
        '{percent}': '%ключ_трафик_процент_остатка%',
        '{used}': '%ключ_трафик_использовано%',
        '{limit}': '%ключ_трафик_лимит%',
    },
    'referral_new_ref_notification_text': {
        '%имя%': '%реферал_имя%',
        '%логин%': '%реферал_логин%',
        '%telegram_id%': '%реферал_telegram_id%',
        '%уровень%': '%реферальный_уровень%',
    },
    'referral_purchase_notification_text': {
        '%имя%': '%покупатель_имя%',
        '%логин%': '%покупатель_логин%',
        '%telegram_id%': '%покупатель_telegram_id%',
        '%уровень%': '%реферальный_уровень%',
        '%тариф%': '%платеж_тариф%',
        '%сумма%': '%платеж_сумма%',
        '%дней%': '%платеж_срок%',
        '%вознаграждение%': '%реферальное_вознаграждение%',
    },
}


def _replace_event_placeholder_names(text: str | None, mapping: dict[str, str]) -> str | None:
    """Replaces old event placeholders with canonical names."""
    if text is None:
        return None
    result = text
    for old, new in mapping.items():
        result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
    return result


def _rewrite_setting_text(conn: sqlite3.Connection, key: str, mapping: dict[str, str]) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return False

    raw = row['value']
    new_value = _replace_event_placeholder_names(raw, mapping)
    if new_value != raw:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (new_value, key))
        return True
    return False


def _rewrite_setting_json_text(conn: sqlite3.Connection, key: str, mapping: dict[str, str]) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return False

    raw = row['value']
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return _rewrite_setting_text(conn, key, mapping)

    if not isinstance(data, dict) or not isinstance(data.get('text'), str):
        return False

    new_text = _replace_event_placeholder_names(data['text'], mapping)
    if new_text == data['text']:
        return False
    data['text'] = new_text
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = ?",
        (json.dumps(data, ensure_ascii=False), key),
    )
    return True


def migration_70(conn):
    """Migration v70: event placeholders and log operations extension core facade."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_core_operations (
            extension_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            target_user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            status TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, idempotency_key)
        )
        """
    )

    changed = 0
    changed += int(_rewrite_setting_json_text(conn, 'notification_text', EVENT_PLACEHOLDER_RENAMES['notification_text']))
    changed += int(_rewrite_setting_text(conn, 'traffic_notification_text', EVENT_PLACEHOLDER_RENAMES['traffic_notification_text']))
    changed += int(_rewrite_setting_text(conn, 'referral_new_ref_notification_text', EVENT_PLACEHOLDER_RENAMES['referral_new_ref_notification_text']))
    changed += int(_rewrite_setting_text(conn, 'referral_purchase_notification_text', EVENT_PLACEHOLDER_RENAMES['referral_purchase_notification_text']))

    broadcast_mapping = {
        '%имяключа%': '%ключ_имя%',
        '%дней%': '%ключ_дней_до_окончания%',
        '{keyname}': '%ключ_имя%',
        '{percent}': '%ключ_трафик_процент_остатка%',
        '{used}': '%ключ_трафик_использовано%',
        '{limit}': '%ключ_трафик_лимит%',
    }
    changed += int(_rewrite_setting_json_text(conn, 'broadcast_message', broadcast_mapping))

    logger.info(
        "Миграция v70 применена: event-плейсхолдеры обновлены, extension_core_operations готова (changed=%s)",
        changed,
    )


def migration_71(conn):
    """Migration v71: business history of key and balance transactions."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS key_operation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_days INTEGER DEFAULT 0,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            expires_before TEXT,
            expires_after TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_operation_log_key_created
        ON key_operation_log(vpn_key_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_operation_log_user_created
        ON key_operation_log(user_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_cents INTEGER NOT NULL,
            balance_before INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            performed_by INTEGER,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_operations_user_created
        ON balance_operations(user_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_operations_reference
        ON balance_operations(reference_type, reference_id)
        """
    )
    logger.info("Миграция v71 применена: key_operation_log и balance_operations готовы")


def migration_72(conn):
    """Migration v72: hidden Yadreno Admin customization/core policy settings."""
    defaults = [
        ('yadreno_admin_customization_enabled', '0'),
        ('yadreno_admin_core_changes_enabled', '0'),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    logger.info("Migration v72 applied: Yadreno Admin customization hidden settings ready")


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
    38: migration_38,
    39: migration_39,
    40: migration_40,
    41: migration_41,
    42: migration_42,
    43: migration_43,
    44: migration_44,
    45: migration_45,
    46: migration_46,
    47: migration_47,
    48: migration_48,
    49: migration_49,
    50: migration_50,
    51: migration_51,
    52: migration_52,
    53: migration_53,
    54: migration_54,
    55: migration_55,
    56: migration_56,
    57: migration_57,
    58: migration_58,
    59: migration_59,
    60: migration_60,
    61: migration_61,
    62: migration_62,
    63: migration_63,
    64: migration_64,
    65: migration_65,
    66: migration_66,
    67: migration_67,
    68: migration_68,
    69: migration_69,
    70: migration_70,
    71: migration_71,
    72: migration_72,
}



def run_migrations() -> None:
    """
    Runs all necessary migrations.
    
    Logic:
    - version = 0 (new install): calls migration_initial → sets INITIAL_VERSION → applies incremental migrations up to LATEST_VERSION
    - version = LATEST_VERSION: does nothing
    - version < INITIAL_VERSION: error (need to update via intermediate version)
    - version >= INITIAL_VERSION: applies incremental migrations from MIGRATIONS
    """
    try:
        current = get_current_version()
        
        if current >= LATEST_VERSION:
            logger.info(f"✅ БД соответствует версии {LATEST_VERSION}. Миграция не требуется.")
            return
        
        # Protection: Database on an intermediate version that cannot be updated with compressed migrations
        if 0 < current < INITIAL_VERSION:
            raise RuntimeError(
                f"Версия БД ({current}) ниже минимально поддерживаемой ({INITIAL_VERSION}). "
                f"Сначала обновите бот до промежуточной версии, чтобы БД мигрировала до v{INITIAL_VERSION}."
            )
        
        logger.info(f"🔄 Требуется миграция БД с версии {current} до {LATEST_VERSION}")
        
        with get_db() as conn:
            # New installation - creating a database from scratch
            if current == 0:
                migration_initial(conn)
                set_version(conn, INITIAL_VERSION)
                current = INITIAL_VERSION
            
            # Incremental migrations (22, 23, ...)
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
