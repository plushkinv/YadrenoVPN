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
INITIAL_VERSION = 73

# Current version of the database schema (incremented when new migrations are added)
LATEST_VERSION = 75

DEFAULT_BROADCAST_STYLE_PROFILE = {
    "schema_version": 1,
    "tone": "friendly_professional",
    "address": "polite_you",
    "emoji_level": "medium",
    "length": "compact",
    "headline": "emoji_bold",
    "paragraphs": "short",
    "cta": "direct_calm",
    "use_lists": True,
    "custom_instructions": "",
}


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
        {"id": "btn_renew_enter_promo", "label": "🎟 Ввести промокод",            "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_crypto",  "label": "🪙 Оплатить USDT",              "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_stars",   "label": "⭐ Оплатить звёздами",          "color": "secondary", "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cards",   "label": "💳 TG payments",                "color": "secondary", "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_qr",      "label": "📱 ЮКасса",                     "color": "secondary", "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_wata",    "label": "🌊 WATA",                       "color": "secondary", "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_platega", "label": "💸 Platega",                    "color": "secondary", "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_cardlink", "label": "🔗 Cardlink",                  "color": "secondary", "row": 7, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_demo",    "label": "🏦 Демо оплата (РФ карта)",     "color": "secondary", "row": 8, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_pay_balance", "label": "💎 Использовать баланс",        "color": "secondary", "row": 9, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_renew_back",        "label": "⬅️ Назад",                     "color": "secondary", "row": 10, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main",         "label": "🈴 На главную",                "color": "secondary", "row": 10, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
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
    Initial migration: creates the complete database schema at version 73.
    
    Called only on new installations (version = 0).
    Condenses v1–v73 migrations into a single function.
    
    Includes all core and feature tables, indexes, settings, editable pages and
    page routes that existed at the v73 compatibility boundary.
    """
    logger.info("Создание БД (базовая схема v73)...")

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
        (
            'broadcast_style_profile',
            json.dumps(DEFAULT_BROADCAST_STYLE_PROFILE, ensure_ascii=False, separators=(',', ':')),
        ),
        ('broadcast_config_revision', '0'),
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
        ('platega_enabled', '0'),
        ('platega_merchant_id', ''),
        ('platega_secret', ''),
        ('cardlink_enabled', '0'),
        ('cardlink_shop_id', ''),
        ('cardlink_api_token', ''),
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
        ('telegram_link_domain', 'telegram.me'),
        ('my_keys_item_template', _my_keys_item_template()),
        ('custom_extensions_enabled', '0'),
        ('custom_payment_webhooks_enabled', '0'),
        ('custom_payment_webhooks_host', '127.0.0.1'),
        ('custom_payment_webhooks_port', '8088'),
        ('custom_payment_webhooks_path_prefix', '/custom-payment-webhook'),
        ('coupon_auto_enabled', '0'),
        ('coupon_auto_discount_percent', '10'),
        ('coupon_auto_lifetime_days', '90'),
        ('support_claim_cleanup_mode', 'remove_button'),
        ('yadreno_admin_customization_enabled', '0'),
        ('yadreno_admin_core_changes_enabled', '0'),
        # The bot operating mode for new installations is Subscription
        # (the bot issues a subscription URL, keys in all inbound with a single subId).
        # Existing installations reached v73 before this baseline was compressed.
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
            referral_coefficient REAL DEFAULT 1.0,
            active_promo_code_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_is_bot_blocked ON users(is_bot_blocked)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username))")

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
            sub_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_id ON vpn_keys(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_expires_at ON vpn_keys(expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_expires ON vpn_keys(user_id, expires_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_server_email ON vpn_keys(server_id, panel_email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_panel_email_lower ON vpn_keys(LOWER(panel_email))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_server_id ON vpn_keys(server_id)")

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
            platega_transaction_id TEXT,
            cardlink_bill_id TEXT,
            promo_code_id INTEGER,
            promo_code TEXT,
            discount_percent INTEGER DEFAULT 0,
            original_amount_cents INTEGER,
            discount_amount_cents INTEGER DEFAULT 0,
            final_amount_cents INTEGER,
            original_amount_stars INTEGER,
            discount_amount_stars INTEGER DEFAULT 0,
            final_amount_stars INTEGER,
            is_promo_free INTEGER DEFAULT 0,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_yookassa_payment_id ON payments(yookassa_payment_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_wata_link_id ON payments(wata_link_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_platega_transaction_id ON payments(platega_transaction_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_cardlink_bill_id ON payments(cardlink_bill_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status_paid_at ON payments(status, paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_key_status_paid_at ON payments(vpn_key_id, status, paid_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_promo_code_id ON payments(promo_code_id)")

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

    # ── support ───────────────────────────────────────────────────────────────

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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_support_threads_user ON support_threads(user_telegram_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_support_threads_assigned ON support_threads(assigned_admin_id, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_support_messages_thread ON support_messages(thread_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_support_admin_notifications_thread ON support_admin_notifications(thread_id, is_active)")

    # ── promotions ────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('promo', 'coupon')),
            code TEXT NOT NULL UNIQUE,
            discount_percent INTEGER NOT NULL DEFAULT 0
                CHECK (discount_percent >= 0 AND discount_percent <= 100),
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            code TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved', 'applied', 'canceled')),
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_type ON promo_codes(type, is_active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_source ON promo_codes(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_expires ON promo_codes(expires_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redemptions_order ON promo_redemptions(order_id) WHERE status != 'canceled'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_redemptions_user ON promo_redemptions(user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code_status ON promo_redemptions(promo_code_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_link_visits_code ON promo_link_visits(promo_code_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_link_visits_user ON promo_link_visits(user_id, created_at)")

    # ── custom extensions ─────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS extension_schema_versions (
            extension_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extension_storage (
            extension_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, key)
        )
    """)
    conn.execute("""
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
    """)

    # ── payment provider bridge ────────────────────────────────────────────────

    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_provider ON payment_provider_orders(provider_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_external ON payment_provider_orders(provider_id, provider_payment_id)")

    # ── lifecycle and business operation logs ─────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS key_lifecycle_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL REFERENCES vpn_keys(id) ON DELETE CASCADE,
            event_name TEXT NOT NULL,
            event_token TEXT NOT NULL,
            metadata_json TEXT,
            emitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (vpn_key_id, event_name, event_token)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key_lifecycle_event_lookup ON key_lifecycle_event_log(event_name, vpn_key_id, event_token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key_lifecycle_event_emitted ON key_lifecycle_event_log(emitted_at)")
    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key_operation_log_key_created ON key_operation_log(vpn_key_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key_operation_log_user_created ON key_operation_log(user_id, created_at)")
    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_balance_operations_user_created ON balance_operations(user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_balance_operations_reference ON balance_operations(reference_type, reference_id)")

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
                {"id": "btn_support",  "label": "💬 Написать в поддержку", "color": "secondary", "row": 3, "col": 0, "is_hidden": True, "action_type": "internal", "action_value": "cmd_support"},
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
                {"id": "btn_news",      "label": "📢 Новости",    "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "url", "action_value": "https://%telegram_link_domain%/plushkin_blog"},
                {"id": "btn_support",   "label": "💬 Поддержка",  "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "url", "action_value": "https://%telegram_link_domain%/plushkin_chat"},
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
                {"id": "btn_enter_promo", "label": "🎟 Ввести промокод",        "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_crypto",  "label": "🪙 Оплатить USDT",          "color": "primary",   "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_stars",   "label": "⭐ Оплатить звёздами",      "color": "primary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_cards",   "label": "💳 TG payments",           "color": "primary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_qr",      "label": "📱 ЮКасса",                "color": "primary",   "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_wata",    "label": "🌊 WATA",                  "color": "primary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_platega", "label": "💸 Platega",               "color": "primary",   "row": 6, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_cardlink", "label": "🔗 Cardlink",             "color": "primary",   "row": 7, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_demo",    "label": "🏦 Демо оплата (РФ карта)", "color": "primary",   "row": 8, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_balance", "label": "💎 Использовать баланс",    "color": "primary",   "row": 9, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_back_main",   "label": "🈴 На главную",             "color": "secondary", "row": 10, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
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

    page_defaults.update({
        'custom_profile': {
            'text': _custom_profile_page_text(),
            'buttons': _custom_profile_page_buttons(),
        },
        'qr_payment': {
            'text': _qr_payment_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'crypto_payment': {
            'text': _crypto_payment_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'balance_payment': {
            'text': _balance_payment_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'demo_payment': {
            'text': _demo_payment_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'payment_tariff_select': {
            'text': _payment_tariff_select_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'payment_status': {
            'text': _payment_status_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'support_start': {
            'text': _support_start_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'support_status': {
            'text': _support_status_page_text(),
            'buttons': _home_only_page_buttons(),
        },
        'promo_enter': {
            'text': _promo_enter_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'promo_status': {
            'text': _promo_status_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'key_status': {
            'text': _key_status_page_text(),
            'buttons': _home_only_page_buttons(),
        },
        'show_id': {
            'text': _show_id_page_text(),
            'buttons': _home_only_page_buttons(),
        },
        'prepayment_unavailable': {
            'text': _prepayment_unavailable_page_text(),
            'buttons': _home_only_page_buttons(),
        },
        'access_blocked': {
            'text': _access_blocked_page_text(),
            'buttons': _home_only_page_buttons(),
        },
    })

    for page_key, data in page_defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default) VALUES (?, ?, ?)",
            (page_key, data['text'], data['buttons'])
        )

    # ── page routes ───────────────────────────────────────────────────────────

    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page_routes_page_key ON page_routes(page_key)")
    conn.execute(
        """
        INSERT OR IGNORE INTO page_routes
            (route_key, page_key, guard_names, hook_names, is_enabled)
        VALUES ('profile', 'custom_profile', '["not_banned"]', '[]', 1)
        """
    )

    logger.info("БД создана (базовая схема v73)")


# ═══════════════════════════════════════════════════════════════════════════════
# Incremental migrations (added below as the project develops)
# ═══════════════════════════════════════════════════════════════════════════════

def migration_74(conn):
    """Migration v74: payment auto-check state and the restored t.me default."""
    _add_column(conn, "payments", "balance_deduct_cents INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_auto_checks (
            order_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active'
                CHECK (state IN (
                    'active', 'provider_succeeded', 'completed',
                    'canceled', 'exhausted', 'completion_failed'
                )),
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            next_check_at TIMESTAMP,
            last_check_at TIMESTAMP,
            check_attempts INTEGER NOT NULL DEFAULT 0,
            completion_attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_auto_checks_due
        ON payment_auto_checks(state, next_check_at)
        """
    )
    conn.execute(
        """
        UPDATE settings
        SET value = 't.me'
        WHERE key = 'telegram_link_domain' AND value = 'telegram.me'
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('telegram_link_domain', 't.me')
        """
    )
    logger.info(
        "Migration v74 applied: payment auto-check state ready, "
        "default Telegram link domain restored to t.me"
    )


def migration_75(conn: sqlite3.Connection) -> None:
    """Migration v75: broadcast style profile and working-config revision."""
    defaults = (
        (
            "broadcast_style_profile",
            json.dumps(
                DEFAULT_BROADCAST_STYLE_PROFILE,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
        ("broadcast_config_revision", "0"),
    )
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    logger.info("Migration v75 applied: broadcast editor settings ready")


MIGRATIONS = {
    74: migration_74,
    75: migration_75,
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
            
            # Incremental migrations after the compressed baseline.
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
