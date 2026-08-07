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
from decimal import Decimal, InvalidOperation
from .connection import get_db
from .db_stats import (
    BroadcastFilterError,
    encode_broadcast_filters,
    normalize_broadcast_filters,
)
from .db_user_ui_texts import update_user_ui_text_defaults
from .user_ui_text_catalog import USER_UI_TEXT_DEFINITIONS

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
LATEST_VERSION = 97

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


PAYMENT_COUPON_PLACEHOLDER = '%payment_coupon%'
PAYMENT_COUPON_V89_PAGE_KEYS = (
    'new_key_server_select',
    'new_key_inbound_select',
    'new_key_no_servers',
    'key_progress',
    'key_operation_unavailable',
    'key_operation_failed',
    'key_delivery',
    'key_delivery_partial',
    'key_delivery_failed',
    'key_renewed',
    'balance_topup_result',
    'payment_auto_completed',
)

PAYMENT_COUPON_V89_MISPLACED_PAGE_KEYS = (
    'new_key_server_select',
    'new_key_inbound_select',
    'new_key_no_servers',
    'key_progress',
    'key_operation_unavailable',
    'key_operation_failed',
    'key_delivery',
    'key_delivery_partial',
    'key_delivery_failed',
)

PAYMENT_COUPON_RESULT_PAGE_KEYS = (
    'payment_completed',
    'key_renewed',
    'balance_topup_result',
    'payment_auto_completed',
)


def _with_payment_coupon_placeholder(text: str) -> str:
    """Appends the canonical payment-coupon slot once."""
    normalized = str(text or '')
    folded = normalized.casefold()
    if PAYMENT_COUPON_PLACEHOLDER.casefold() in folded:
        return normalized
    return f"{normalized.rstrip()}\n\n{PAYMENT_COUPON_PLACEHOLDER}"


def _without_migrated_payment_coupon_suffix(text: str) -> str:
    """Removes only the exact terminal slot automatically appended by v89."""
    normalized = str(text or '')
    suffix = f"\n\n{PAYMENT_COUPON_PLACEHOLDER}"
    if normalized.casefold().endswith(suffix.casefold()):
        return normalized[:-len(suffix)].rstrip()
    return normalized


def _payment_completed_page_text() -> str:
    """Default confirmation shown once before configuring a purchased key."""
    return _with_payment_coupon_placeholder(
        "✅ <b>Оплата прошла успешно!</b>"
    )


def _my_keys_item_template() -> str:
    """Hidden default of one key format on the “My Keys” page."""
    return (
        "🔑 <b>%key(field=name)%</b>\n"
        "%key(field=status)% · %key(field=traffic)%\n"
        "📅 До %key(field=expires_at)%\n"
        "📍 %key(field=server)%"
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


def _expired_keys_deleted_page_text() -> str:
    """Default grouped notification after expired keys are removed from the bot."""
    return (
        "🗑️ <b>Неактивные ключи удалены</b>\n\n"
        "Срок действия этих VPN-ключей закончился не менее "
        "%retention_days% дней назад, поэтому мы удалили их из бота:\n\n"
        "%deleted_keys%\n\n"
        "Если VPN снова понадобится, нажмите «Купить ключ» — "
        "новый доступ можно оформить в любое время."
    )


def _lapsed_key_coupon_page_text() -> str:
    """Default win-back coupon page after all user keys have expired."""
    return (
        "🎁 <b>Купон для вас</b>\n\n"
        "Мы заметили, что вы не продлили VPN-ключ, и хотим помочь вам "
        "вернуться.\n\n"
        "Ваш купон на скидку <b>%promo_discount%%</b>:\n"
        "<pre>%promo_code%</pre>\n"
        "Купон действует до <b>%promo_expires_at%</b>.\n\n"
        "Введите его в поле промокода при следующей покупке или продлении."
    )


def _renew_payment_page_text() -> str:
    """Default text on the payment method selection page for renewal."""
    return (
        "💳 <b>Продление ключа</b>\n\n"
        "🔑 Ключ: <b>%key(field=name)%</b>\n\n"
        "Выберите способ оплаты:"
    )


def _legacy_prepayment_page_buttons() -> str:
    """Return the provider-first purchase buttons stored by the v73 baseline."""
    return json.dumps([
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
    ], ensure_ascii=False)


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


def _support_reply_page_buttons() -> str:
    """Default keyboard attached to an administrator support reply."""
    return json.dumps([
        {"id": "btn_support_reply", "label": "💬 Ответить", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
        {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
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


def _key_details_page_text_v96() -> str:
    """Current stock key card with tariff and effective device limit."""
    return (
        "🔑 <b>%key(field=name)%</b>\n\n"
        "<b>Статус:</b> %key(field=status)%\n"
        "<b>Сервер:</b> %key(field=server)%\n"
        "<b>Тариф:</b> %key(field=tariff)%\n"
        "<b>Устройств:</b> %key(field=device_limit)%\n"
        "<b>Трафик:</b> %key(field=traffic)%\n"
        "<b>Действует до:</b> %key(field=expires_at)%\n\n"
        "📜 <b>История операций:</b>\n%key_history%"
    )


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
    """Server selection default for a paid key draft."""
    return (
        "🌐 <b>Выбор сервера</b>\n\n"
        "%экран_данные%"
    )


def _new_key_inbound_select_page_text() -> str:
    """Protocol selection default for a paid key draft."""
    return (
        "🖥️ <b>Выбор протокола</b>\n\n"
        "%экран_данные%\n\n"
        "Выберите протокол:"
    )


def _new_key_no_servers_page_text() -> str:
    """Unavailable-server result after a paid key draft was created."""
    return (
        "⚠️ <b>Нет доступных серверов</b>\n\n"
        "К сожалению, сейчас нет доступных серверов.\n"
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
        ('broadcast_filter', '[]'),
        ('broadcast_filter_contract_version', '2'),
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
        ('trial_usage_scope', 'once_per_user'),
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
        ('expired_key_retention_days', '30'),
        ('expired_key_deletion_notifications_enabled', '1'),
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
            max_ips INTEGER DEFAULT 1,
            system_type TEXT
        )
    """)

    # ── tariff_groups ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 1,
            monthly_traffic_reset_enabled INTEGER NOT NULL DEFAULT 0
                CHECK (monthly_traffic_reset_enabled IN (0, 1)),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO tariff_groups (id, name, sort_order)
        VALUES (1, 'Основная', 1)
    """)
    conn.execute("""
        INSERT INTO tariffs (
            name, duration_days, price_cents, price_stars, display_order,
            is_active, traffic_limit_gb, group_id, max_ips, system_type
        )
        SELECT 'Admin Tariff', 0, 0, 0, 999, 0, 0, 1, 1, 'admin_custom'
        WHERE NOT EXISTS (
            SELECT 1 FROM tariffs
            WHERE group_id = 1 AND system_type = 'admin_custom'
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tariffs_admin_custom_group "
        "ON tariffs(group_id) WHERE system_type = 'admin_custom'"
    )

    # ── trial_offers ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trial_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tariff_id INTEGER REFERENCES tariffs(id) ON DELETE RESTRICT,
            is_primary INTEGER NOT NULL DEFAULT 0
                CHECK (is_primary IN (0, 1)),
            is_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (is_enabled IN (0, 1)),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (is_primary = 1 OR tariff_id IS NOT NULL)
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_offers_primary "
        "ON trial_offers(is_primary) WHERE is_primary = 1"
    )

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
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            traffic_updated_at DATETIME,
            traffic_notified_pct INTEGER DEFAULT 100,
            sub_id TEXT,
            traffic_limit_override INTEGER
                CHECK (traffic_limit_override IS NULL OR traffic_limit_override >= 0),
            max_ips_override INTEGER
                CHECK (max_ips_override IS NULL OR max_ips_override BETWEEN 1 AND 999),
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

    # ── trial_activations ─────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trial_activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            offer_id INTEGER,
            tariff_id INTEGER,
            group_id INTEGER,
            vpn_key_id INTEGER REFERENCES vpn_keys(id) ON DELETE SET NULL,
            payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL,
            legacy_global_block INTEGER NOT NULL DEFAULT 0
                CHECK (legacy_global_block IN (0, 1)),
            activated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_activations_user_group "
        "ON trial_activations(user_id, group_id) "
        "WHERE legacy_global_block = 0 AND group_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_activations_legacy_user "
        "ON trial_activations(user_id) WHERE legacy_global_block = 1"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trial_activations_offer "
        "ON trial_activations(offer_id)"
    )

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
            'buttons': _legacy_prepayment_page_buttons(),
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
        'payment_completed': {
            'text': _payment_completed_page_text(),
            'buttons': _empty_page_buttons(),
        },
        'payment_coupon_message': {
            'text': '',
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
        'support_reply': {
            'text': '',
            'buttons': _support_reply_page_buttons(),
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


_ATOMIC_KEY_PAGE_PLACEHOLDER_MAP = {
    '%key_id%': '%key(field=id)%',
    '%ключ_id%': '%key(field=id)%',
    '%key_name%': '%key(field=name)%',
    '%ключ_имя%': '%key(field=name)%',
    '%key_status%': '%key(field=status)%',
    '%ключ_статус%': '%key(field=status)%',
    '%key_traffic%': '%key(field=traffic)%',
    '%ключ_трафик%': '%key(field=traffic)%',
    '%key_expires_at%': '%key(field=expires_at)%',
    '%ключ_дата_окончания%': '%key(field=expires_at)%',
    '%key_server%': '%key(field=server)%',
    '%ключ_сервер%': '%key(field=server)%',
    '%key_inbound%': '%key(field=inbound)%',
    '%ключ_инбаунд%': '%key(field=inbound)%',
    '%key_protocol%': '%key(field=protocol)%',
    '%ключ_протокол%': '%key(field=protocol)%',
}
_ATOMIC_KEY_PAGE_PLACEHOLDER_RE = re.compile(r'%[^%\s]+%')


def _upgrade_atomic_key_page_placeholders(value: str | None) -> str | None:
    """Converts removed atomic key page placeholders to the parameterized form."""
    if value is None:
        return None

    return _ATOMIC_KEY_PAGE_PLACEHOLDER_RE.sub(
        lambda match: _ATOMIC_KEY_PAGE_PLACEHOLDER_MAP.get(
            match.group(0).casefold(),
            match.group(0),
        ),
        value,
    )


def _upgrade_atomic_key_button_placeholders(value: str | None) -> str | None:
    """Converts placeholders inside button JSON, including Unicode escapes."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return _upgrade_atomic_key_page_placeholders(value)

    def upgrade(item):
        if isinstance(item, str):
            return _upgrade_atomic_key_page_placeholders(item)
        if isinstance(item, list):
            return [upgrade(child) for child in item]
        if isinstance(item, dict):
            return {key: upgrade(child) for key, child in item.items()}
        return item

    upgraded = upgrade(parsed)
    if upgraded == parsed:
        return value
    return json.dumps(upgraded, ensure_ascii=False)


def migration_76(conn: sqlite3.Connection) -> None:
    """Migration v76: parameterized key fields for editable pages."""
    for column in ('text_default', 'text_custom', 'buttons_default', 'buttons_custom'):
        rows = conn.execute(
            f"SELECT page_key, {column} FROM pages WHERE {column} IS NOT NULL"
        ).fetchall()
        for page_key, value in rows:
            if column.startswith('buttons_'):
                upgraded = _upgrade_atomic_key_button_placeholders(value)
            else:
                upgraded = _upgrade_atomic_key_page_placeholders(value)
            if upgraded != value:
                conn.execute(
                    f"UPDATE pages SET {column} = ? WHERE page_key = ?",
                    (upgraded, page_key),
                )

    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'my_keys_item_template'"
    ).fetchone()
    if row:
        upgraded = _upgrade_atomic_key_page_placeholders(row[0])
        if upgraded != row[0]:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'my_keys_item_template'",
                (upgraded,),
            )

    logger.info("Migration v76 applied: key page placeholders are parameterized")


def _drop_column_if_exists(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> None:
    """Drops an obsolete column when the local SQLite version supports it."""
    columns = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _decimal_setting(value: object, default: Decimal) -> Decimal:
    """Returns a positive decimal setting value or its safe migration default."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _decimal_text(value: Decimal) -> str:
    """Serializes a Decimal without exponent notation or insignificant zeroes."""
    normalized = format(value, 'f')
    if '.' in normalized:
        normalized = normalized.rstrip('0').rstrip('.')
    return normalized or '0'


def migration_77(conn: sqlite3.Connection) -> None:
    """Migration v77: persistent payment intents and RUB-only tariff prices."""
    payment_columns = (
        "intent_version INTEGER NOT NULL DEFAULT 0",
        "purpose TEXT NOT NULL DEFAULT 'legacy_key_payment'",
        "purpose_data_json TEXT NOT NULL DEFAULT '{}'",
        "nominal_amount_cents INTEGER NOT NULL DEFAULT 0",
        "payable_amount_cents INTEGER NOT NULL DEFAULT 0",
        "charge_amount TEXT",
        "charge_currency TEXT",
        "rate_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        "description TEXT",
        "success_target_json TEXT NOT NULL DEFAULT '{}'",
        "cancel_target_json TEXT NOT NULL DEFAULT '{}'",
        "fulfillment_status TEXT NOT NULL DEFAULT 'pending'",
        "fulfillment_attempts INTEGER NOT NULL DEFAULT 0",
        "fulfillment_started_at TIMESTAMP",
        "fulfillment_last_error TEXT",
        "provider_confirmed_at TIMESTAMP",
        "fulfilled_at TIMESTAMP",
        "created_at TIMESTAMP",
    )
    for column_def in payment_columns:
        _add_column(conn, "payments", column_def)
    conn.execute(
        "UPDATE payments SET created_at = COALESCE(created_at, paid_at, CURRENT_TIMESTAMP)"
    )

    provider_order_columns = (
        "purpose TEXT",
        "charge_amount TEXT",
        "charge_currency TEXT",
    )
    for column_def in provider_order_columns:
        _add_column(conn, "payment_provider_orders", column_def)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_effects (
            order_id TEXT NOT NULL,
            effect_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'started'
                CHECK (status IN ('started', 'completed', 'failed')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            attempts INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            PRIMARY KEY (order_id, effect_name),
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_operations_payment_topup
        ON balance_operations(reference_type, reference_id)
        WHERE reference_type = 'payment_topup' AND reference_id IS NOT NULL
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_fulfillment "
        "ON payments(fulfillment_status, provider_confirmed_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_codes_payment_coupon "
        "ON promo_codes(source) WHERE source LIKE 'auto_payment:%'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_operations_payment_referral "
        "ON balance_operations(user_id, operation_type, source, reference_type, reference_id) "
        "WHERE reference_type = 'payment_referral' AND reference_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_key_operations_payment_reward "
        "ON key_operation_log(user_id, source, reference_type, reference_id) "
        "WHERE reference_type IN ('payment_referral', 'payment_promo_reward') "
        "AND reference_id IS NOT NULL"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_referral_effects (
            order_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            referrer_id INTEGER NOT NULL,
            payer_id INTEGER NOT NULL,
            reward_cents INTEGER NOT NULL DEFAULT 0,
            reward_days INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_id, level),
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        """
        UPDATE payments
        SET purpose = CASE
                WHEN status = 'pending' AND vpn_key_id IS NOT NULL THEN 'key_renewal'
                WHEN status = 'pending' THEN 'key_purchase'
                ELSE 'legacy_key_payment'
            END,
            purpose_data_json = CASE
                WHEN status = 'pending' AND vpn_key_id IS NOT NULL
                    THEN json_object('key_id', vpn_key_id, 'tariff_id', tariff_id)
                WHEN status = 'pending'
                    THEN json_object('tariff_id', tariff_id)
                ELSE '{}'
            END,
            nominal_amount_cents = COALESCE(
                (SELECT price_rub * 100 FROM tariffs WHERE tariffs.id = payments.tariff_id),
                0
            ),
            payable_amount_cents = COALESCE(final_amount_cents, amount_cents, 0),
            fulfillment_status = CASE
                WHEN status = 'paid' THEN 'completed'
                ELSE 'pending'
            END,
            fulfilled_at = CASE WHEN status = 'paid' THEN paid_at ELSE NULL END
        WHERE intent_version = 0
        """
    )

    usd_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'usd_rub_rate'"
    ).fetchone()
    usd_cents = _decimal_setting(usd_row[0] if usd_row else None, Decimal('9500'))
    stablecoin_rate = usd_cents / Decimal('100')
    star_rate = stablecoin_rate * Decimal('0.013')
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ('stablecoin_rub_rate', _decimal_text(stablecoin_rate)),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ('star_rub_rate', _decimal_text(star_rate)),
    )

    page_defaults = {
        'payment_method_select': (
            "💳 <b>Выбор способа оплаты</b>\n\n"
            "%платеж_назначение%\n"
            "💰 <b>Сумма:</b> %платеж_сумма%\n"
            "%платеж_скидка_строка%\n"
            "Выберите удобный способ оплаты:",
            '[]',
        ),
        'balance_topup_amount': (
            "💰 <b>Пополнение баланса</b>\n\n"
            "Введите сумму в рублях, которую хотите зачислить на баланс.\n\n"
            "Например: <code>500</code>"
            "%платеж_ошибка%",
            '[]',
        ),
        'balance_topup_result': (
            _with_payment_coupon_placeholder(
                "✅ <b>Баланс пополнен</b>\n\n"
                "На баланс зачислено: <b>%платеж_номинал%</b>\n"
                "Оплачено: <b>%платеж_сумма%</b>"
            ),
            json.dumps([
                {
                    "id": "btn_back_main",
                    "label": "🈴 На главную",
                    "color": "secondary",
                    "row": 0,
                    "col": 0,
                    "is_hidden": False,
                    "action_type": "internal",
                    "action_value": "cmd_back_main",
                },
            ], ensure_ascii=False),
        ),
    }
    for page_key, (text_default, buttons_default) in page_defaults.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO pages
                (page_key, text_default, buttons_default)
            VALUES (?, ?, ?)
            """,
            (page_key, text_default, buttons_default),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO page_routes
            (route_key, page_key, guard_names, hook_names, is_enabled)
        VALUES ('balance_topup_result', 'balance_topup_result', '["not_banned"]', '[]', 1)
        """
    )

    _drop_column_if_exists(conn, 'tariffs', 'price_cents')
    _drop_column_if_exists(conn, 'tariffs', 'price_stars')
    logger.info(
        "Migration v77 applied: persistent payment intents and RUB-only tariffs ready"
    )


def migration_78(conn: sqlite3.Connection) -> None:
    """Migration v78: editable keyboard for administrator support replies."""
    conn.execute(
        """
        INSERT OR IGNORE INTO pages
            (page_key, text_default, buttons_default)
        VALUES (?, '', ?)
        """,
        ('support_reply', _support_reply_page_buttons()),
    )
    logger.info("Migration v78 applied: support reply keyboard is editable")


def migration_79(conn: sqlite3.Connection) -> None:
    """Migration v79: configurable RUB/USD base currency and generic money fields."""
    _add_column(conn, "tariffs", "price_minor INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "UPDATE tariffs SET price_minor = COALESCE(price_rub, 0) * 100 "
        "WHERE price_minor = 0"
    )

    for column_def in (
        "base_currency TEXT NOT NULL DEFAULT 'RUB'",
        "nominal_amount_minor INTEGER NOT NULL DEFAULT 0",
        "payable_amount_minor INTEGER NOT NULL DEFAULT 0",
        "balance_deduct_minor INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "payments", column_def)
    conn.execute(
        """
        UPDATE payments
        SET base_currency = COALESCE(NULLIF(UPPER(base_currency), ''), 'RUB'),
            nominal_amount_minor = CASE
                WHEN nominal_amount_minor = 0 THEN COALESCE(nominal_amount_cents, 0)
                ELSE nominal_amount_minor
            END,
            payable_amount_minor = CASE
                WHEN payable_amount_minor = 0 THEN COALESCE(payable_amount_cents, 0)
                ELSE payable_amount_minor
            END,
            balance_deduct_minor = CASE
                WHEN balance_deduct_minor = 0 THEN COALESCE(balance_deduct_cents, 0)
                ELSE balance_deduct_minor
            END
        """
    )

    for column_def in (
        "currency TEXT NOT NULL DEFAULT 'RUB'",
        "delta_minor INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "balance_operations", column_def)
    conn.execute(
        "UPDATE balance_operations SET delta_minor = delta_cents "
        "WHERE delta_minor = 0 AND delta_cents != 0"
    )

    for column_def in (
        "reward_currency TEXT NOT NULL DEFAULT 'RUB'",
        "total_reward_minor INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "referral_stats", column_def)
    conn.execute(
        "UPDATE referral_stats SET total_reward_minor = total_reward_cents "
        "WHERE total_reward_minor = 0 AND total_reward_cents != 0"
    )

    for column_def in (
        "reward_currency TEXT NOT NULL DEFAULT 'RUB'",
        "reward_minor INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "payment_referral_effects", column_def)
    conn.execute(
        "UPDATE payment_referral_effects SET reward_minor = reward_cents "
        "WHERE reward_minor = 0 AND reward_cents != 0"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS currency_rates (
            base_currency TEXT NOT NULL,
            target_currency TEXT NOT NULL,
            units_per_base TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (base_currency, target_currency)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS base_currency_switches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            to_units_per_from TEXT NOT NULL,
            admin_telegram_id INTEGER NOT NULL,
            backup_path TEXT NOT NULL,
            converted_tariffs INTEGER NOT NULL DEFAULT 0,
            converted_balances INTEGER NOT NULL DEFAULT 0,
            converted_referral_rows INTEGER NOT NULL DEFAULT 0,
            canceled_intents INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('base_currency', 'RUB')"
    )

    stablecoin_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'stablecoin_rub_rate'"
    ).fetchone()
    star_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'star_rub_rate'"
    ).fetchone()
    stablecoin_rub = _decimal_setting(
        stablecoin_row[0] if stablecoin_row else None,
        Decimal('100'),
    )
    star_rub = _decimal_setting(
        star_row[0] if star_row else None,
        Decimal('1.3'),
    )
    for target, rate in (
        ('USDT', Decimal('1') / stablecoin_rub),
        ('XTR', Decimal('1') / star_rub),
    ):
        conn.execute(
            """
            INSERT OR IGNORE INTO currency_rates (
                base_currency, target_currency, units_per_base
            ) VALUES ('RUB', ?, ?)
            """,
            (target, _decimal_text(rate)),
        )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?
        WHERE page_key = 'balance_topup_amount'
        """,
        (
            "💰 <b>Пополнение баланса</b>\n\n"
            "Введите сумму в базовой валюте (%платеж_базовая_валюта%), "
            "которую хотите зачислить на баланс.\n\n"
            "Например: <code>500</code>%платеж_ошибка%",
        ),
    )
    logger.info(
        "Migration v79 applied: generic base money and RUB/USD switching are ready"
    )


def migration_80(conn: sqlite3.Connection) -> None:
    """Migration v80: expose customization permanently and remove its obsolete flag."""
    conn.execute(
        "DELETE FROM settings WHERE key = 'yadreno_admin_customization_enabled'"
    )
    logger.info(
        "Migration v80 applied: obsolete customization visibility setting removed"
    )


def _ui_page_buttons(*buttons: dict) -> str:
    """Serializes stock page buttons used by migration v81."""
    return json.dumps(list(buttons), ensure_ascii=False)


def _ui_internal_button(
    button_id: str,
    label: str,
    action_value: str,
    row: int,
    col: int = 0,
) -> dict:
    return {
        "id": button_id,
        "label": label,
        "color": "secondary",
        "row": row,
        "col": col,
        "is_hidden": False,
        "action_type": "internal",
        "action_value": action_value,
    }


def _ui_system_button(
    button_id: str,
    label: str,
    row: int,
    col: int = 0,
) -> dict:
    return {
        "id": button_id,
        "label": label,
        "color": "secondary",
        "row": row,
        "col": col,
        "is_hidden": False,
        "action_type": "system",
        "action_value": None,
    }


def _ui_collection_button(button_id: str, label: str, row: int = 0) -> dict:
    return {
        "id": button_id,
        "label": label,
        "color": "secondary",
        "row": row,
        "col": 0,
        "is_hidden": False,
        "action_type": "system_collection",
        "action_value": None,
    }


def _ui_home_buttons() -> str:
    return _ui_page_buttons(
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 0),
    )


def _ui_cancel_buttons() -> str:
    return _ui_page_buttons(
        _ui_internal_button("btn_back_main", "❌ Отмена", "cmd_back_main", 0),
    )


def _ui_key_buttons() -> str:
    return _ui_page_buttons(
        _ui_internal_button("btn_my_keys", "🔑 Мои ключи", "cmd_my_keys", 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1),
    )


def _ui_payment_status_buttons() -> str:
    return _ui_page_buttons(
        _ui_system_button("btn_intent_methods", "🔄 Сменить способ", 0),
        _ui_system_button("btn_intent_cancel", "⬅️ Назад", 1, 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1, 1),
    )


def _ui_promo_buttons(primary_label: str) -> str:
    return _ui_page_buttons(
        _ui_system_button("btn_promo_return", primary_label, 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1),
    )


def _ui_intent_method_buttons(*, include_promo: bool = True) -> str:
    buttons = [
        _ui_system_button("btn_intent_provider_crypto", "🪙 Оплатить USDT", 0),
        _ui_system_button("btn_intent_provider_stars", "⭐ Оплатить звёздами", 1),
        _ui_system_button("btn_intent_provider_cards", "💳 TG payments", 2),
        _ui_system_button("btn_intent_provider_yookassa_qr", "📱 ЮКасса", 3),
        _ui_system_button("btn_intent_provider_wata", "🌊 WATA", 4),
        _ui_system_button("btn_intent_provider_platega", "💸 Platega", 5),
        _ui_system_button("btn_intent_provider_cardlink", "🔗 Cardlink", 6),
        _ui_system_button("btn_intent_provider_demo", "🏦 Демо оплата", 7),
        _ui_system_button("btn_intent_balance", "💎 Использовать баланс", 8),
    ]
    navigation_row = 9
    if include_promo:
        buttons.append(
            _ui_system_button("btn_intent_promo", "🎟 Ввести промокод", 9)
        )
        navigation_row = 10
    buttons.extend((
        _ui_system_button("btn_intent_cancel", "⬅️ Назад", navigation_row, 0),
        _ui_internal_button(
            "btn_back_main",
            "🈴 На главную",
            "cmd_back_main",
            navigation_row,
            1,
        ),
    ))
    return _ui_page_buttons(*buttons)


def _ui_intent_link_buttons() -> str:
    return _ui_page_buttons(
        _ui_system_button("btn_intent_open", "💳 Перейти к оплате", 0),
        _ui_system_button("btn_intent_check", "✅ Я оплатил", 1),
        _ui_system_button("btn_intent_methods", "🔄 Сменить способ", 2),
        _ui_system_button("btn_intent_cancel", "⬅️ Назад", 3, 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 3, 1),
    )


def _ui_balance_confirmation_buttons() -> str:
    return _ui_page_buttons(
        _ui_system_button("btn_intent_balance", "💎 Использовать баланс", 0),
        _ui_system_button("btn_intent_cancel", "⬅️ Назад", 1, 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1, 1),
    )


def _ui_tariff_collection_buttons() -> str:
    return _ui_page_buttons(
        _ui_collection_button("btn_tariff_items", "💳 %item_name% — %item_price%"),
        _ui_system_button("btn_tariff_back", "⬅️ Назад", 1000, 0),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1000, 1),
    )


def _ui_purchase_tariff_collection_buttons() -> str:
    """Return purchase tariff controls without a duplicate route to Home."""
    return _ui_page_buttons(
        _ui_collection_button("btn_tariff_items", "💳 %item_name% — %item_price%"),
        _ui_system_button("btn_enter_promo", "🎟 Ввести промокод", 999),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1000),
    )


def _ui_renewal_tariff_collection_buttons() -> str:
    """Return renewal tariffs, promotion entry, and final navigation row."""
    return _ui_page_buttons(
        _ui_collection_button("btn_tariff_items", "💳 %item_name% — %item_price%"),
        _ui_system_button(
            "btn_renew_enter_promo",
            "🎟 Ввести промокод",
            999,
        ),
        _ui_system_button("btn_tariff_back", "⬅️ Назад", 1000, 0),
        _ui_internal_button(
            "btn_back_main",
            "🈴 На главную",
            "cmd_back_main",
            1000,
            1,
        ),
    )


def _ui_key_collection_buttons() -> str:
    return _ui_page_buttons(
        _ui_collection_button("btn_key_items", "%item_status_indicator% %item_name%"),
        _ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1000),
    )


def _ui_server_collection_buttons(*, with_back: bool) -> str:
    buttons = [_ui_collection_button("btn_server_items", "🌐 %item_name%")]
    if with_back:
        buttons.append(_ui_system_button("btn_key_flow_back", "❌ Отмена", 1000))
    else:
        buttons.append(_ui_internal_button("btn_back_main", "🈴 На главную", "cmd_back_main", 1000))
    return _ui_page_buttons(*buttons)


def _ui_protocol_collection_buttons() -> str:
    return _ui_page_buttons(
        _ui_collection_button("btn_protocol_items", "🔌 %item_name% (%item_protocol%)"),
        _ui_system_button("btn_key_flow_back", "⬅️ Назад", 1000),
    )


def _ui_key_flow_confirm_buttons() -> str:
    return _ui_page_buttons(
        _ui_system_button("btn_key_flow_confirm", "✅ Да, заменить", 0),
        _ui_system_button("btn_key_flow_back", "❌ Отмена", 1),
    )


def _user_ui_page_defaults_v81() -> dict[str, tuple[str, str]]:
    """Concrete stock user screens introduced by the one-language customization layer."""
    pages: dict[str, tuple[str, str]] = {
        "action_unavailable": (
            "⚠️ <b>Действие недоступно</b>\n\nОткройте нужный раздел заново и повторите попытку.",
            _ui_home_buttons(),
        ),
        "screen_unavailable": (
            "⚠️ <b>Экран недоступен</b>\n\nВернитесь на главную и попробуйте ещё раз.",
            _ui_home_buttons(),
        ),
        "trial_already_used": (
            "🎁 <b>Пробный период уже использован</b>\n\nПробный доступ предоставляется один раз.",
            _ui_home_buttons(),
        ),
        "balance_insufficient": (
            "💎 <b>Недостаточно средств</b>\n\nВаш баланс: <b>%payment_balance%</b>\nК оплате: <b>%payment_amount%</b>",
            _ui_payment_status_buttons(),
        ),
        "balance_topup_amount_invalid": (
            "⚠️ <b>Некорректная сумма</b>\n\nВведите положительное число без дополнительных символов.",
            _ui_home_buttons(),
        ),
        "payment_method_select_renewal": (
            "💳 <b>Продление ключа</b>\n\n🔑 <b>%key(field=name)%</b>\n💰 К оплате: <b>%payment_amount%</b>\n%payment_discount_line%\nВыберите способ оплаты:",
            _ui_intent_method_buttons(include_promo=False),
        ),
        "payment_method_select_topup": (
            "💰 <b>Пополнение баланса</b>\n\nНа баланс: <b>%payment_nominal%</b>\nК оплате: <b>%payment_amount%</b>\nВыберите способ оплаты:",
            _ui_intent_method_buttons(),
        ),
        "payment_method_select_surcharge": (
            "💎 <b>Доплата после списания баланса</b>\n\nС баланса: <b>%payment_balance_deduct%</b>\nОсталось оплатить: <b>%payment_remaining%</b>\nВыберите способ доплаты:",
            _ui_intent_method_buttons(include_promo=False),
        ),
        "payment_link_renewal": (
            "💳 <b>Оплата продления</b>\n\n🔑 <b>%key(field=name)%</b>\n💰 Сумма: <b>%payment_amount%</b>\n%payment_discount_line%\nПерейдите к оплате по кнопке ниже.\n\n<i>Статус обновится автоматически; если доступна ручная проверка, используйте кнопку ниже.</i>",
            _ui_intent_link_buttons(),
        ),
        "payment_link_topup": (
            "💰 <b>Пополнение баланса</b>\n\nНа баланс: <b>%payment_nominal%</b>\nК оплате: <b>%payment_amount%</b>\nПерейдите к оплате по кнопке ниже.\n\n<i>Статус обновится автоматически; если доступна ручная проверка, используйте кнопку ниже.</i>",
            _ui_intent_link_buttons(),
        ),
        "payment_creating": (
            "⏳ <b>Создаём платёж</b>\n\nПодождите немного.",
            _ui_home_buttons(),
        ),
        "payment_pending": (
            "⏳ <b>Платёж ещё не поступил</b>\n\nЗавершите оплату и повторите проверку немного позже.",
            _ui_payment_status_buttons(),
        ),
        "payment_check_wait": (
            "⏳ <b>Проверка пока недоступна</b>\n\nПовторите через %payment_wait_seconds% сек.",
            _ui_payment_status_buttons(),
        ),
        "payment_canceled": (
            "⚪ <b>Платёж отменён</b>\n\nВыберите другой способ оплаты или вернитесь позже.",
            _ui_payment_status_buttons(),
        ),
        "payment_unavailable": (
            "⚠️ <b>Оплата недоступна</b>\n\nВыберите другой способ оплаты или попробуйте позже.",
            _ui_payment_status_buttons(),
        ),
        "payment_minimum_unavailable": (
            "⚠️ <b>Сумма слишком мала</b>\n\nМинимальная сумма для выбранного способа: <b>%payment_minimum%</b>.",
            _ui_payment_status_buttons(),
        ),
        "payment_order_unavailable": (
            "⚠️ <b>Платёж не найден</b>\n\nОткройте оплату заново — прежний счёт мог устареть.",
            _ui_home_buttons(),
        ),
        "payment_failed": (
            "❌ <b>Не удалось обработать платёж</b>\n\nПопробуйте позже или выберите другой способ оплаты.",
            _ui_payment_status_buttons(),
        ),
        "payment_auto_completed": (
            _with_payment_coupon_placeholder(
                "✅ <b>Платёж подтверждён</b>\n\n"
                "Операция завершена автоматически."
            ),
            _ui_key_buttons(),
        ),
        "promo_invalid": (
            "⚠️ <b>Некорректный промокод</b>\n\nПроверьте введённое значение и попробуйте снова.",
            _ui_promo_buttons("⬅️ Назад"),
        ),
        "promo_not_found": (
            "❌ <b>Промокод не найден</b>\n\nПроверьте код или вернитесь к оплате.",
            _ui_promo_buttons("⬅️ Назад"),
        ),
        "promo_inactive": (
            "⚪ <b>Промокод неактивен</b>\n\nВернитесь к оплате и выберите другой вариант.",
            _ui_promo_buttons("💳 К оплате"),
        ),
        "promo_expired": (
            "⌛ <b>Срок промокода истёк</b>\n\nВернитесь к оплате и выберите другой вариант.",
            _ui_promo_buttons("💳 К оплате"),
        ),
        "promo_exhausted": (
            "⚪ <b>Промокод уже использован</b>\n\nВернитесь к оплате и выберите другой вариант.",
            _ui_promo_buttons("💳 К оплате"),
        ),
        "promo_unavailable": (
            "⚠️ <b>Промокоды недоступны</b>\n\nВернитесь к оплате и выберите другой вариант.",
            _ui_promo_buttons("💳 К оплате"),
        ),
        "promo_applied": (
            "✅ <b>Промокод применён</b>\n\nКод: <code>%promo_code%</code>\nСкидка: <b>%promo_discount%%</b>",
            _ui_promo_buttons("💳 К оплате"),
        ),
        "promo_link_saved": (
            "🎟 <b>Промокод сохранён</b>\n\nКод <code>%promo_code%</code> будет применён при оплате.",
            _ui_promo_buttons("💳 Перейти к оплате"),
        ),
        "support_reply_start": (
            "💬 <b>Ответ в поддержку</b>\n\nОтправьте текст, фото, видео или GIF одним сообщением.",
            _ui_cancel_buttons(),
        ),
        "support_format_unsupported": (
            "❌ <b>Формат не поддерживается</b>\n\nОтправьте текст, фото, видео или GIF.",
            _ui_home_buttons(),
        ),
        "support_thread_unavailable": (
            "❌ <b>Диалог не найден</b>\n\nНачните новое обращение в поддержку.",
            _ui_home_buttons(),
        ),
        "support_failed": (
            "⚠️ <b>Сообщение не отправлено</b>\n\nПопробуйте позже.",
            _ui_home_buttons(),
        ),
        "support_sent": (
            "✅ <b>Сообщение отправлено</b>\n\nОтвет придёт сюда, в бот.",
            _ui_home_buttons(),
        ),
        "my_keys_key_deleted": (
            "✅ <b>Ключ удалён</b>\n\nКлюч <b>%key(field=name)%</b> успешно удалён.",
            _ui_key_buttons(),
        ),
        "key_not_found": (
            "❌ <b>Ключ не найден</b>\n\nКлюч удалён, устарел или принадлежит другому пользователю.",
            _ui_key_buttons(),
        ),
        "key_progress": (
            "⏳ <b>Выполняем операцию с ключом</b>\n\n"
            "Подождите немного.",
            _ui_key_buttons(),
        ),
        "key_operation_unavailable": (
            "⚠️ <b>Действие с ключом недоступно</b>\n\n"
            "Откройте карточку ключа заново и повторите попытку.",
            _ui_key_buttons(),
        ),
        "key_operation_failed": (
            "❌ <b>Не удалось выполнить операцию</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            _ui_key_buttons(),
        ),
        "key_rename_invalid": (
            "⚠️ <b>Некорректное имя</b>\n\nВведите непустое имя длиной не более 30 символов.",
            _ui_key_buttons(),
        ),
        "key_delivery_partial": (
            "📋 <b>Ваш VPN-ключ</b>\n\n"
            "%ключ_для_копирования%\n\n"
            "⚠️ Полную конфигурацию получить не удалось. Попробуйте позже.",
            _ui_key_buttons(),
        ),
        "key_delivery_failed": (
            "❌ <b>Ошибка выдачи ключа</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            _ui_key_buttons(),
        ),
        "key_renewed": (
            _with_payment_coupon_placeholder(
                "✅ <b>Ключ продлён</b>\n\n"
                "🔑 <b>%key(field=name)%</b>\n"
                "Новый срок: <b>%payment_term%</b>."
            ),
            _ui_key_buttons(),
        ),
        "expiry_notification_actions": (
            "",
            _ui_key_buttons(),
        ),
        "expired_keys_deleted": (
            _expired_keys_deleted_page_text(),
            _my_keys_empty_page_buttons(),
        ),
    }
    return pages


_UI_TEMPLATE_PLACEHOLDER_RE = re.compile(r"%[^%\s]+%")


def _copy_compatible_page_customs_v81(
    conn: sqlite3.Connection,
    *,
    source_page_key: str,
    target_page_keys: tuple[str, ...],
    text_placeholders: frozenset[str],
) -> None:
    """Copies only source custom values that the split target can still render."""
    source = conn.execute(
        """
        SELECT text_custom, image_custom, media_type_custom, buttons_custom
        FROM pages
        WHERE page_key = ?
        """,
        (source_page_key,),
    ).fetchone()
    if not source:
        return

    text_custom = source[0]
    copy_text = False
    if text_custom is not None:
        placeholders = {
            match.group(0).casefold()
            for match in _UI_TEMPLATE_PLACEHOLDER_RE.finditer(str(text_custom))
        }
        copy_text = placeholders <= {item.casefold() for item in text_placeholders}

    for target_page_key in target_page_keys:
        conn.execute(
            """
            UPDATE pages
            SET text_custom = CASE
                    WHEN text_custom IS NULL AND ? THEN ?
                    ELSE text_custom
                END,
                image_custom = CASE
                    WHEN image_custom IS NULL THEN ?
                    ELSE image_custom
                END,
                media_type_custom = CASE
                    WHEN media_type_custom IS NULL THEN ?
                    ELSE media_type_custom
                END,
                buttons_custom = CASE
                    WHEN buttons_custom IS NULL THEN ?
                    ELSE buttons_custom
                END
            WHERE page_key = ?
            """,
            (
                1 if copy_text else 0,
                text_custom,
                source[1],
                source[2],
                source[3],
                target_page_key,
            ),
        )


def migration_81(conn: sqlite3.Connection) -> None:
    """Migration v81: database-backed stock user UI outside the admin interface."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_ui_texts (
            text_key TEXT PRIMARY KEY,
            text_default TEXT NOT NULL,
            text_custom TEXT,
            text_format TEXT NOT NULL CHECK (text_format IN ('html', 'plain', 'button')),
            description TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    update_user_ui_text_defaults(USER_UI_TEXT_DEFINITIONS, conn=conn)

    page_defaults = _user_ui_page_defaults_v81()
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
            SET text_default = ?, buttons_default = ?
            WHERE page_key = ?
            """,
            (text_default, buttons_default, page_key),
        )

    common_payment_placeholders = frozenset({
        '%telegram_id%',
        '%bot_username%',
        '%payment_tariff%',
        '%платеж_тариф%',
        '%payment_amount%',
        '%платеж_сумма%',
        '%payment_discount_line%',
        '%платеж_скидка_строка%',
        '%key(field=name)%',
    })
    _copy_compatible_page_customs_v81(
        conn,
        source_page_key='payment_method_select',
        target_page_keys=('payment_method_select_renewal',),
        text_placeholders=common_payment_placeholders,
    )
    _copy_compatible_page_customs_v81(
        conn,
        source_page_key='payment_method_select',
        target_page_keys=('payment_method_select_topup',),
        text_placeholders=frozenset({
            '%telegram_id%',
            '%bot_username%',
            '%payment_amount%',
            '%платеж_сумма%',
            '%payment_nominal%',
            '%платеж_номинал%',
            '%payment_discount_line%',
            '%платеж_скидка_строка%',
        }),
    )
    _copy_compatible_page_customs_v81(
        conn,
        source_page_key='payment_method_select',
        target_page_keys=('payment_method_select_surcharge',),
        text_placeholders=common_payment_placeholders | frozenset({
            '%payment_nominal%',
            '%платеж_номинал%',
            '%payment_balance%',
            '%платеж_баланс%',
            '%payment_balance_deduct%',
            '%платеж_списание_баланса%',
            '%payment_remaining%',
            '%платеж_остаток_к_оплате%',
        }),
    )
    common_link_placeholders = common_payment_placeholders | frozenset({
        '%payment_provider%',
        '%платеж_провайдер%',
        '%payment_term%',
        '%платеж_срок%',
        '%payment_link%',
        '%платеж_ссылка%',
        '%payment_link_url%',
        '%платеж_ссылка_url%',
    })
    _copy_compatible_page_customs_v81(
        conn,
        source_page_key='qr_payment',
        target_page_keys=('payment_link_renewal',),
        text_placeholders=common_link_placeholders,
    )
    _copy_compatible_page_customs_v81(
        conn,
        source_page_key='qr_payment',
        target_page_keys=('payment_link_topup',),
        text_placeholders=frozenset({
            '%telegram_id%',
            '%bot_username%',
            '%payment_provider%',
            '%платеж_провайдер%',
            '%payment_amount%',
            '%платеж_сумма%',
            '%payment_nominal%',
            '%платеж_номинал%',
            '%payment_link%',
            '%платеж_ссылка%',
            '%payment_link_url%',
            '%платеж_ссылка_url%',
            '%payment_discount_line%',
            '%платеж_скидка_строка%',
        }),
    )

    purchase_method_text = (
        "💳 <b>Выбор способа оплаты</b>\n\n"
        "%payment_tariff%\n"
        "💰 К оплате: <b>%payment_amount%</b>\n"
        "%payment_discount_line%\n"
        "Выберите способ оплаты:"
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?, buttons_default = ?
        WHERE page_key = 'payment_method_select'
        """,
        (purchase_method_text, _ui_intent_method_buttons(include_promo=False)),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?, buttons_default = ?
        WHERE page_key = 'qr_payment'
        """,
        (
            "💳 <b>Оплата</b>\n\n"
            "%payment_tariff%\n"
            "💰 Сумма: <b>%payment_amount%</b>\n"
            "%payment_discount_line%\n"
            "Перейдите по ссылке или отсканируйте QR-код.\n\n"
            "<i>Статус обновится автоматически; если доступна ручная проверка, используйте кнопку ниже.</i>",
            _ui_intent_link_buttons(),
        ),
    )
    existing_page_defaults = {
        "balance_payment": (
            "💎 <b>Оплата с баланса</b>\n\n"
            "Тариф: <b>%payment_tariff%</b>\n"
            "Стоимость: <b>%payment_amount%</b>\n"
            "%payment_discount_line%\n"
            "Ваш баланс: <b>%payment_balance%</b>\n\n"
            "С баланса будет списано: <b>%payment_balance_deduct%</b>\n"
            "Останется оплатить: <b>%payment_remaining%</b>"
        ),
        "balance_topup_amount": (
            "💰 <b>Пополнение баланса</b>\n\n"
            "Введите сумму в базовой валюте (%payment_base_currency%), "
            "которую хотите зачислить на баланс.\n\n"
            "Например: <code>500</code>"
        ),
        "crypto_payment": (
            "🪙 <b>Оплата криптовалютой</b>\n\n"
            "🎫 Тариф: <b>%payment_tariff%</b>\n"
            "💰 Сумма: <b>%payment_amount%</b>\n"
            "%payment_discount_line%\n"
            "Перейдите к оплате по кнопке ниже."
        ),
        "demo_payment": (
            "🏦 <b>Демонстрационная оплата</b>\n\n"
            "Это демо-режим. Реального списания не происходит.\n\n"
            "🎫 Тариф: <b>%payment_tariff%</b>\n"
            "📅 Срок: <b>%payment_term%</b>\n"
            "💰 Сумма: <b>%payment_amount%</b>"
        ),
        "main": (
            "🔐 <b>Добро пожаловать в VPN-бот!</b>\n\n"
            "Быстрый, безопасный и анонимный доступ к интернету.\n"
            "Без логов, без ограничений, без проблем! 🚀\n\n"
            "📋 <b>Тарифы:</b>\n%tariffs%"
        ),
        "custom_profile": (
            "👤 <b>Личный кабинет</b>\n\n"
            "Имя: <b>%user_name%</b>\n"
            "Telegram ID: <code>%telegram_id%</code>\n"
            "Username: %user_username%\n"
            "Дата регистрации: %user_registered_at%\n"
            "Баланс: <b>%user_balance%</b>\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🔑 <b>Ключи</b>\n"
            "Всего: <b>%keys_total%</b>\n"
            "Активных: <b>%keys_active%</b>\n"
            "Истёкших: <b>%keys_expired%</b>"
        ),
        "key_details": (
            "🔑 <b>%key(field=name)%</b>\n\n"
            "<b>Статус:</b> %key(field=status)%\n"
            "<b>Сервер:</b> %key(field=server)%\n"
            "<b>Трафик:</b> %key(field=traffic)%\n"
            "<b>Действует до:</b> %key(field=expires_at)%\n\n"
            "📜 <b>История операций:</b>\n%key_history%"
        ),
        "key_replace_server_select": (
            "🔄 <b>Замена ключа</b>\n\n"
            "Вы можете пересоздать ключ на другом или том же сервере.\n"
            "Старый ключ будет удалён, но срок действия сохранится.\n\n"
            "Выберите сервер:"
        ),
        "key_replace_inbound_select": (
            "🖥️ <b>Выбор протокола</b>\n\n"
            "Сервер: <b>%selected_server%</b>\n\n"
            "Выберите протокол:"
        ),
        "key_replace_confirm": (
            "⚠️ <b>Подтверждение замены</b>\n\n"
            "Ключ: <b>%key(field=name)%</b>\n"
            "Новый сервер: <b>%selected_server%</b>\n\n"
            "Старый ключ или ссылка перестанет работать. "
            "Обновите настройки в приложении.\n\n"
            "Вы уверены?"
        ),
        "key_rename_prompt": (
            "✏️ <b>Переименование ключа</b>\n\n"
            "Текущее имя: <b>%key(field=name)%</b>\n\n"
            "Введите новое название для ключа (макс. 30 символов):\n"
            "<i>(Отправьте любой текст)</i>"
        ),
        "new_key_server_select": (
            "🌐 <b>Выбор сервера</b>\n\n"
            "🔑 Выберите сервер для вашего нового ключа."
        ),
        "new_key_inbound_select": (
            "🖥️ <b>Выбор протокола</b>\n\n"
            "Сервер: <b>%selected_server%</b>\n\n"
            "Выберите протокол:"
        ),
        "referral": (
            "👥 <b>Реферальная система</b>\n\n"
            "📎 Ваша реферальная ссылка:\n<code>%referral_link%</code>\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📝 <b>Условия:</b>\n"
            "Приглашённые пользователи регистрируются по вашей ссылке. "
            "Когда они оплачивают подписку, вы получаете реферальное вознаграждение.\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📊 <b>Ваша статистика:</b>\n\n%referral_stats%"
        ),
        "support_start": (
            "💬 <b>Поддержка</b>\n\n"
            "Отправьте текст, фото, видео или GIF одним сообщением."
        ),
        "payment_tariff_select": (
            "💳 <b>Выбор тарифа</b>\n\n"
            "Выберите подходящий тариф:"
        ),
    }
    for page_key, text_default in existing_page_defaults.items():
        conn.execute(
            "UPDATE pages SET text_default = ? WHERE page_key = ?",
            (text_default, page_key),
        )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'support_start'",
        (_ui_cancel_buttons(),),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'crypto_payment'",
        (_ui_intent_link_buttons(),),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'demo_payment'",
        (_ui_payment_status_buttons(),),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'balance_payment'",
        (_ui_balance_confirmation_buttons(),),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'promo_enter'",
        (_ui_promo_buttons("⬅️ Назад"),),
    )
    conn.execute(
        "UPDATE pages SET buttons_default = ? WHERE page_key = 'balance_topup_amount'",
        (_ui_cancel_buttons(),),
    )
    dynamic_page_buttons = {
        "my_keys": _ui_key_collection_buttons(),
        "prepayment": _ui_purchase_tariff_collection_buttons(),
        "payment_tariff_select": _ui_tariff_collection_buttons(),
        "renew_payment": _ui_renewal_tariff_collection_buttons(),
        "key_replace_server_select": _ui_server_collection_buttons(with_back=True),
        "key_replace_inbound_select": _ui_protocol_collection_buttons(),
        "new_key_server_select": _ui_server_collection_buttons(with_back=False),
        "new_key_inbound_select": _ui_protocol_collection_buttons(),
        "key_replace_confirm": _ui_key_flow_confirm_buttons(),
        "key_rename_prompt": _ui_page_buttons(
            _ui_system_button("btn_key_flow_back", "❌ Отмена", 0),
        ),
    }
    for page_key, buttons_default in dynamic_page_buttons.items():
        conn.execute(
            "UPDATE pages SET buttons_default = ? WHERE page_key = ?",
            (buttons_default, page_key),
        )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?
        WHERE page_key = 'renew_payment'
        """,
        (
            "💳 <b>Продление ключа</b>\n\n"
            "🔑 <b>%key(field=name)%</b>\n"
            "Выберите тариф:",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('my_keys_item_template', ?)",
        (_my_keys_item_template(),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('notification_text', ?)",
        (
            "⚠️ <b>Ваш VPN-ключ %ключ_имя% скоро истекает!</b>\n\n"
            "Через %ключ_дней_до_окончания% дней закончится срок действия вашего ключа.\n\n"
            "Продлите подписку, чтобы сохранить доступ к VPN без перерывов!",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('traffic_notification_text', ?)",
        (
            "⚠️ По ключу <b>%ключ_имя%</b> осталось "
            "%ключ_трафик_процент_остатка%% трафика "
            "(%ключ_трафик_использовано% из %ключ_трафик_лимит%)",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_new_ref_notification_text', ?)",
        (_referral_new_ref_notification_text(),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_purchase_notification_text', ?)",
        (_referral_purchase_notification_text(),),
    )
    logger.info(
        "Migration v81 applied: cached user UI fragments and concrete stock pages are ready"
    )


_BUTTON_COSMETIC_FIELDS_V82 = (
    'label',
    'color',
    'row',
    'col',
    'is_hidden',
    'icon_custom_emoji_id',
)

_PURCHASE_METHOD_BUTTON_MAP_V82 = {
    'btn_pay_crypto': 'btn_intent_provider_crypto',
    'btn_pay_stars': 'btn_intent_provider_stars',
    'btn_pay_cards': 'btn_intent_provider_cards',
    'btn_pay_qr': 'btn_intent_provider_yookassa_qr',
    'btn_pay_wata': 'btn_intent_provider_wata',
    'btn_pay_platega': 'btn_intent_provider_platega',
    'btn_pay_cardlink': 'btn_intent_provider_cardlink',
    'btn_pay_demo': 'btn_intent_provider_demo',
    'btn_pay_balance': 'btn_intent_balance',
}

_RENEWAL_METHOD_BUTTON_MAP_V82 = {
    'btn_renew_pay_crypto': 'btn_intent_provider_crypto',
    'btn_renew_pay_stars': 'btn_intent_provider_stars',
    'btn_renew_pay_cards': 'btn_intent_provider_cards',
    'btn_renew_pay_qr': 'btn_intent_provider_yookassa_qr',
    'btn_renew_pay_wata': 'btn_intent_provider_wata',
    'btn_renew_pay_platega': 'btn_intent_provider_platega',
    'btn_renew_pay_cardlink': 'btn_intent_provider_cardlink',
    'btn_renew_pay_demo': 'btn_intent_provider_demo',
    'btn_renew_pay_balance': 'btn_intent_balance',
}


def _legacy_purchase_page_buttons_v82() -> str:
    """Return every purchase control that may remain in v81 custom data."""
    buttons = json.loads(_legacy_prepayment_page_buttons())
    buttons.append(
        _ui_system_button('btn_tariff_back', '⬅️ Назад', 1000, 0)
    )
    return json.dumps(buttons, ensure_ascii=False)


def _migration_button_array_v82(raw: str | None) -> list | None:
    """Parse a stored button array without treating malformed custom data as empty."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


def _migration_button_index_v82(buttons: list | None) -> dict[str, dict]:
    """Index valid migration button records by stable id."""
    if buttons is None:
        return {}
    return {
        str(button['id']): button
        for button in buttons
        if isinstance(button, dict)
        and isinstance(button.get('id'), str)
        and button['id']
    }


def _button_cosmetic_overrides_v82(
    custom_button: dict,
    legacy_default: dict,
) -> dict:
    """Keep only administrator-visible changes while replacing obsolete semantics."""
    return {
        field: custom_button[field]
        for field in _BUTTON_COSMETIC_FIELDS_V82
        if field in custom_button
        and custom_button[field] != legacy_default.get(field)
    }


def _build_button_override_v82(
    target_defaults: dict[str, dict],
    target_id: str,
    overrides: dict,
) -> dict | None:
    """Build a complete override on top of the target page's current button."""
    target_default = target_defaults.get(target_id)
    if target_default is None or not overrides:
        return None
    result = dict(target_default)
    result.update(overrides)
    return result


def _append_button_override_v82(buttons: list, button: dict | None) -> bool:
    """Append an override only when the target id has no newer customization."""
    if button is None:
        return False
    button_id = button.get('id')
    if not isinstance(button_id, str) or not button_id:
        return False
    if any(
        isinstance(item, dict) and item.get('id') == button_id
        for item in buttons
    ):
        return False
    buttons.append(button)
    return True


def _migrate_tariff_first_page_buttons_v82(
    conn: sqlite3.Connection,
    *,
    tariff_page_key: str,
    method_page_key: str,
    legacy_defaults_json: str,
    method_button_map: dict[str, str],
    source_navigation_map: dict[str, str],
    method_navigation_map: dict[str, str],
    extension_button_prefix: str,
) -> tuple[int, int]:
    """Separate legacy provider overrides from one tariff-first page."""
    source = conn.execute(
        """
        SELECT buttons_default, buttons_custom
        FROM pages
        WHERE page_key = ?
        """,
        (tariff_page_key,),
    ).fetchone()
    if source is None or not source[1]:
        return 0, 0

    source_custom = _migration_button_array_v82(source[1])
    if source_custom is None:
        logger.warning(
            "Migration v82 skipped malformed buttons_custom on page %s",
            tariff_page_key,
        )
        return 0, 0

    legacy_defaults = _migration_button_index_v82(
        _migration_button_array_v82(legacy_defaults_json)
    )
    source_defaults = _migration_button_index_v82(
        _migration_button_array_v82(source[0])
    )
    method = conn.execute(
        """
        SELECT buttons_default, buttons_custom
        FROM pages
        WHERE page_key = ?
        """,
        (method_page_key,),
    ).fetchone()
    method_defaults = _migration_button_index_v82(
        _migration_button_array_v82(method[0] if method else None)
    )
    method_custom = _migration_button_array_v82(method[1] if method else None)
    can_transfer = method is not None and method_custom is not None
    if method is not None and method_custom is None:
        logger.warning(
            "Migration v82 cannot transfer button labels into malformed "
            "buttons_custom on page %s",
            method_page_key,
        )
    target_custom = list(method_custom or [])
    source_result: list = []
    source_pending: list[dict] = []
    method_pending: list[dict] = []
    removed_count = 0

    for item in source_custom:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            source_result.append(item)
            continue
        button_id = item['id']
        is_legacy_extension = button_id.startswith(extension_button_prefix)
        is_legacy_button = (
            button_id in method_button_map
            or button_id in source_navigation_map
            or button_id in method_navigation_map
            or is_legacy_extension
        )
        if not is_legacy_button:
            source_result.append(item)
            continue

        removed_count += 1
        legacy_default = legacy_defaults.get(button_id, {})
        overrides = _button_cosmetic_overrides_v82(item, legacy_default)

        source_target_id = source_navigation_map.get(button_id)
        if source_target_id:
            source_override = _build_button_override_v82(
                source_defaults,
                source_target_id,
                overrides,
            )
            if source_override is not None:
                source_pending.append(source_override)

        method_target_id = (
            method_button_map.get(button_id)
            or method_navigation_map.get(button_id)
        )
        if method_target_id and can_transfer:
            method_override = _build_button_override_v82(
                method_defaults,
                method_target_id,
                overrides,
            )
            if method_override is not None:
                method_pending.append(method_override)

    if not removed_count:
        return 0, 0

    for button in source_pending:
        _append_button_override_v82(source_result, button)

    transferred_count = 0
    if can_transfer:
        for button in method_pending:
            if _append_button_override_v82(target_custom, button):
                transferred_count += 1

    conn.execute(
        """
        UPDATE pages
        SET buttons_custom = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE page_key = ?
        """,
        (
            json.dumps(source_result, ensure_ascii=False) if source_result else None,
            tariff_page_key,
        ),
    )
    if can_transfer and transferred_count:
        conn.execute(
            """
            UPDATE pages
            SET buttons_custom = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE page_key = ?
            """,
            (json.dumps(target_custom, ensure_ascii=False), method_page_key),
        )
    return removed_count, transferred_count


def migration_82(conn: sqlite3.Connection) -> None:
    """Migration v82: separate stale customized provider buttons from tariff pages."""
    page_defaults = {
        'prepayment': _ui_purchase_tariff_collection_buttons(),
        'renew_payment': _ui_renewal_tariff_collection_buttons(),
        'payment_method_select': _ui_intent_method_buttons(include_promo=False),
        'payment_method_select_renewal': _ui_intent_method_buttons(
            include_promo=False
        ),
    }
    for page_key, buttons_default in page_defaults.items():
        conn.execute(
            """
            UPDATE pages
            SET buttons_default = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE page_key = ?
            """,
            (buttons_default, page_key),
        )

    purchase_counts = _migrate_tariff_first_page_buttons_v82(
        conn,
        tariff_page_key='prepayment',
        method_page_key='payment_method_select',
        legacy_defaults_json=_legacy_purchase_page_buttons_v82(),
        method_button_map=_PURCHASE_METHOD_BUTTON_MAP_V82,
        source_navigation_map={
            'btn_enter_promo': 'btn_enter_promo',
            'btn_back_main': 'btn_back_main',
        },
        method_navigation_map={
            'btn_tariff_back': 'btn_intent_cancel',
            'btn_back_main': 'btn_back_main',
        },
        extension_button_prefix='btn_pay_ext_',
    )
    renewal_counts = _migrate_tariff_first_page_buttons_v82(
        conn,
        tariff_page_key='renew_payment',
        method_page_key='payment_method_select_renewal',
        legacy_defaults_json=_renew_payment_page_buttons(),
        method_button_map=_RENEWAL_METHOD_BUTTON_MAP_V82,
        source_navigation_map={
            'btn_renew_enter_promo': 'btn_renew_enter_promo',
            'btn_renew_back': 'btn_tariff_back',
            'btn_back_main': 'btn_back_main',
        },
        method_navigation_map={
            'btn_renew_back': 'btn_intent_cancel',
            'btn_back_main': 'btn_back_main',
        },
        extension_button_prefix='btn_renew_pay_ext_',
    )
    logger.info(
        "Migration v82 applied: removed %s stale tariff-page payment buttons "
        "and transferred %s compatible custom overrides",
        purchase_counts[0] + renewal_counts[0],
        purchase_counts[1] + renewal_counts[1],
    )


_PROMO_COSMETIC_FIELDS_V83 = (
    'label',
    'color',
    'is_hidden',
    'icon_custom_emoji_id',
)


def _move_intent_promo_customization_v83(
    conn: sqlite3.Connection,
    *,
    method_page_key: str,
    tariff_page_key: str,
    tariff_button_id: str,
    tariff_defaults_json: str,
) -> bool:
    """Move promo cosmetics back without carrying obsolete row coordinates."""
    method = conn.execute(
        """
        SELECT buttons_default, buttons_custom
        FROM pages
        WHERE page_key = ?
        """,
        (method_page_key,),
    ).fetchone()
    tariff = conn.execute(
        "SELECT buttons_custom FROM pages WHERE page_key = ?",
        (tariff_page_key,),
    ).fetchone()
    if method is None or tariff is None:
        return False

    method_custom = _migration_button_array_v82(method[1])
    tariff_custom = _migration_button_array_v82(tariff[0])
    if method_custom is None or tariff_custom is None:
        logger.warning(
            "Migration v83 skipped malformed promo customization on %s or %s",
            method_page_key,
            tariff_page_key,
        )
        return False

    method_defaults = _migration_button_index_v82(
        _migration_button_array_v82(method[0])
    )
    tariff_defaults = _migration_button_index_v82(
        _migration_button_array_v82(tariff_defaults_json)
    )
    old_default = method_defaults.get('btn_intent_promo', {})
    method_result: list = []
    promo_overrides: dict | None = None
    found = False

    for item in method_custom:
        if (
            isinstance(item, dict)
            and item.get('id') == 'btn_intent_promo'
        ):
            found = True
            promo_overrides = {
                field: item[field]
                for field in _PROMO_COSMETIC_FIELDS_V83
                if field in item and item[field] != old_default.get(field)
            }
            continue
        method_result.append(item)

    if not found:
        return False

    target_custom = list(tariff_custom)
    target_override = _build_button_override_v82(
        tariff_defaults,
        tariff_button_id,
        promo_overrides or {},
    )
    _append_button_override_v82(target_custom, target_override)
    conn.execute(
        """
        UPDATE pages
        SET buttons_custom = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE page_key = ?
        """,
        (
            json.dumps(method_result, ensure_ascii=False)
            if method_result else None,
            method_page_key,
        ),
    )
    conn.execute(
        """
        UPDATE pages
        SET buttons_custom = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE page_key = ?
        """,
        (
            json.dumps(target_custom, ensure_ascii=False)
            if target_custom else None,
            tariff_page_key,
        ),
    )
    return True


def migration_83(conn: sqlite3.Connection) -> None:
    """Migration v83: show promotions before tariffs and reset payment methods."""
    purchase_defaults = _ui_purchase_tariff_collection_buttons()
    renewal_defaults = _ui_renewal_tariff_collection_buttons()
    moved_purchase = _move_intent_promo_customization_v83(
        conn,
        method_page_key='payment_method_select',
        tariff_page_key='prepayment',
        tariff_button_id='btn_enter_promo',
        tariff_defaults_json=purchase_defaults,
    )
    moved_renewal = _move_intent_promo_customization_v83(
        conn,
        method_page_key='payment_method_select_renewal',
        tariff_page_key='renew_payment',
        tariff_button_id='btn_renew_enter_promo',
        tariff_defaults_json=renewal_defaults,
    )
    page_defaults = {
        'prepayment': purchase_defaults,
        'renew_payment': renewal_defaults,
        'payment_method_select': _ui_intent_method_buttons(
            include_promo=False
        ),
        'payment_method_select_renewal': _ui_intent_method_buttons(
            include_promo=False
        ),
        'payment_method_select_topup': _ui_intent_method_buttons(),
        'payment_method_select_surcharge': _ui_intent_method_buttons(
            include_promo=False
        ),
    }
    for page_key, buttons_default in page_defaults.items():
        conn.execute(
            """
            UPDATE pages
            SET buttons_default = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE page_key = ?
            """,
            (buttons_default, page_key),
        )
    logger.info(
        "Migration v83 applied: promo entry restored to tariff pages "
        "(purchase_custom=%s, renewal_custom=%s)",
        moved_purchase,
        moved_renewal,
    )


def migration_84(conn: sqlite3.Connection) -> None:
    """Migration v84: normalize the legacy My Keys Home position once."""
    row = conn.execute(
        """
        SELECT buttons_custom
        FROM pages
        WHERE page_key = 'my_keys'
        """
    ).fetchone()
    if row is None or not row[0]:
        logger.info(
            "Migration v84 applied: no legacy My Keys Home position to normalize"
        )
        return

    buttons = _migration_button_array_v82(row[0])
    if buttons is None:
        logger.warning(
            "Migration v84 skipped malformed buttons_custom on page my_keys"
        )
        return

    changed = False
    normalized: list = []
    for item in buttons:
        if (
            isinstance(item, dict)
            and item.get('id') == 'btn_back_main'
            and item.get('action_type') == 'internal'
            and item.get('action_value') == 'cmd_back_main'
            and item.get('row') == 0
            and item.get('col') == 0
        ):
            item = dict(item)
            item['row'] = 1000
            changed = True
        normalized.append(item)

    if changed:
        conn.execute(
            """
            UPDATE pages
            SET buttons_custom = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE page_key = 'my_keys'
            """,
            (json.dumps(normalized, ensure_ascii=False),),
        )

    logger.info(
        "Migration v84 applied: legacy My Keys Home position normalized=%s",
        changed,
    )


def migration_85(conn: sqlite3.Connection) -> None:
    """Migration v85: use indicator-only key statuses and space legacy rows."""
    status_keys = {
        'key.status.active',
        'key.status.expired',
        'key.status.traffic_exhausted',
    }
    update_user_ui_text_defaults(
        (
            definition
            for definition in USER_UI_TEXT_DEFINITIONS
            if definition.text_key in status_keys
        ),
        conn=conn,
    )

    legacy_item_template = (
        "%key(field=status)%<b>%key(field=name)%</b> - "
        "%key(field=traffic)% - до %key(field=expires_at)%\n"
        "     📍%key(field=server)% - %key(field=inbound)% "
        "(%key(field=protocol)%)"
    )
    spaced_item_template = legacy_item_template.replace(
        "%key(field=status)%<b>",
        "%key(field=status)% <b>",
        1,
    )
    cursor = conn.execute(
        """
        UPDATE settings
        SET value = ?
        WHERE key = 'my_keys_item_template'
          AND value = ?
        """,
        (spaced_item_template, legacy_item_template),
    )
    logger.info(
        "Migration v85 applied: key statuses use indicators only "
        "(legacy_template_spaced=%s)",
        cursor.rowcount > 0,
    )


def migration_86(conn: sqlite3.Connection) -> None:
    """Migration v86: expired-key retention settings and deletion page."""
    for key, value in (
        ('expired_key_retention_days', '30'),
        ('expired_key_deletion_notifications_enabled', '1'),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    page_key = 'expired_keys_deleted'
    text_default = _expired_keys_deleted_page_text()
    buttons_default = _my_keys_empty_page_buttons()
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
        SET text_default = ?, buttons_default = ?
        WHERE page_key = ?
        """,
        (text_default, buttons_default, page_key),
    )

    update_user_ui_text_defaults(
        (
            definition
            for definition in USER_UI_TEXT_DEFINITIONS
            if definition.text_key in {
                'key.deleted_list.item',
                'key.deleted_list.more',
            }
        ),
        conn=conn,
    )
    logger.info(
        "Migration v86 applied: expired-key retention and deletion page ready"
    )


def migration_87(conn: sqlite3.Connection) -> None:
    """Migration v87: canonical multi-filter broadcast audience contract."""
    marker = conn.execute(
        """
        SELECT value FROM settings
        WHERE key = 'broadcast_filter_contract_version'
        """
    ).fetchone()
    if marker and str(marker[0]) == '2':
        logger.info("Migration v87 already applied: broadcast filter contract is v2")
        return

    filter_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'broadcast_filter'"
    ).fetchone()
    raw_filter = filter_row[0] if filter_row else None
    filter_valid = True
    try:
        canonical_filter = encode_broadcast_filters(raw_filter)
    except BroadcastFilterError:
        filter_valid = False
        canonical_filter = str(raw_filter or '')
        logger.error(
            "Migration v87 preserved an invalid broadcast_filter value; "
            "broadcast launch will fail closed until it is replaced"
        )

    revision_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'broadcast_config_revision'"
    ).fetchone()
    try:
        current_revision = max(0, int(revision_row[0] if revision_row else 0))
    except (TypeError, ValueError):
        current_revision = 0
    next_revision = current_revision + 1

    migrated_stages = 0
    removed_stages = 0
    stage_rows = conn.execute(
        """
        SELECT key, value FROM settings
        WHERE key LIKE 'broadcast_editor_stage:%'
        """
    ).fetchall()
    for stage_key, raw_stage in stage_rows:
        try:
            stage = json.loads(raw_stage)
            if not isinstance(stage, dict):
                raise ValueError("stage must be an object")
            schema_version = int(stage.get('schema_version') or 0)
            if schema_version == 1:
                if 'filter' not in stage:
                    raise ValueError("legacy stage filter is missing")
                stage_filters = normalize_broadcast_filters(stage['filter'])
            elif schema_version == 2:
                if not isinstance(stage.get('filters'), list):
                    raise ValueError("stage filters must be an array")
                stage_filters = normalize_broadcast_filters(stage['filters'])
            else:
                raise ValueError("unsupported stage schema")

            stage['schema_version'] = 2
            stage['filters'] = list(stage_filters)
            stage.pop('filter', None)
            try:
                base_revision = max(0, int(stage.get('base_config_revision') or 0))
            except (TypeError, ValueError):
                base_revision = 0
            if base_revision == current_revision:
                stage['base_config_revision'] = next_revision
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (
                    json.dumps(
                        stage,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(',', ':'),
                    ),
                    stage_key,
                ),
            )
            migrated_stages += 1
        except (
            BroadcastFilterError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            conn.execute("DELETE FROM settings WHERE key = ?", (stage_key,))
            removed_stages += 1

    if filter_valid:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('broadcast_filter', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (canonical_filter,),
        )
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('broadcast_config_revision', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(next_revision),),
    )
    conn.execute("DELETE FROM settings WHERE key LIKE 'broadcast_confirm:%'")
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('broadcast_filter_contract_version', '2')
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """
    )
    logger.info(
        "Migration v87 applied: multi-filter broadcast audience ready "
        "(filter_valid=%s, stages_migrated=%s, stages_removed=%s)",
        filter_valid,
        migrated_stages,
        removed_stages,
    )


def migration_88(conn: sqlite3.Connection) -> None:
    """Migration v88: lapsed-user automatic coupons and editable page."""
    for key, value in (
        ('coupon_lapsed_enabled', '0'),
        ('coupon_lapsed_discount_percent', '10'),
        ('coupon_lapsed_lifetime_days', '90'),
        ('coupon_lapsed_delay_days', '7'),
        ('coupon_lapsed_enabled_since', ''),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lapsed_coupon_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            lapse_token TEXT NOT NULL,
            lapsed_at TIMESTAMP NOT NULL,
            coupon_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'failed', 'canceled')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            UNIQUE (user_id, lapse_token),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (coupon_id) REFERENCES promo_codes(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lapsed_coupon_deliveries_due
        ON lapsed_coupon_deliveries(status, lapsed_at, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lapsed_coupon_deliveries_coupon
        ON lapsed_coupon_deliveries(coupon_id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_codes_lapsed_coupon
        ON promo_codes(source) WHERE source LIKE 'auto_lapsed:%'
        """
    )

    page_key = 'lapsed_key_coupon'
    text_default = _lapsed_key_coupon_page_text()
    buttons_default = _ui_key_buttons()
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
        SET text_default = ?, buttons_default = ?
        WHERE page_key = ?
        """,
        (text_default, buttons_default, page_key),
    )
    logger.info(
        "Migration v88 applied: lapsed-user automatic coupons are ready"
    )


def migration_89(conn: sqlite3.Connection) -> None:
    """Migration v89: expose payment coupons through editable page placeholders."""
    migrated_custom_pages = 0
    for page_key in PAYMENT_COUPON_V89_PAGE_KEYS:
        row = conn.execute(
            """
            SELECT text_default, text_custom
            FROM pages
            WHERE page_key = ?
            """,
            (page_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Required payment-coupon page is missing: {page_key}"
            )

        text_default = _with_payment_coupon_placeholder(row[0] or '')
        text_custom = row[1]
        migrated_text_custom = text_custom
        if isinstance(text_custom, str) and text_custom:
            migrated_text_custom = _with_payment_coupon_placeholder(text_custom)
            if migrated_text_custom != text_custom:
                migrated_custom_pages += 1

        conn.execute(
            """
            UPDATE pages
            SET text_default = ?,
                text_custom = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE page_key = ?
            """,
            (text_default, migrated_text_custom, page_key),
        )

    logger.info(
        "Migration v89 applied: payment coupon placeholder added "
        "(custom_pages_migrated=%s)",
        migrated_custom_pages,
    )


def migration_90(conn: sqlite3.Connection) -> None:
    """Migration v90: keep the stock coupon on payment-result messages only."""
    conn.execute(
        """
        INSERT OR IGNORE INTO pages
            (page_key, text_default, buttons_default)
        VALUES (?, ?, ?)
        """,
        (
            'payment_completed',
            _payment_completed_page_text(),
            _empty_page_buttons(),
        ),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?, buttons_default = ?
        WHERE page_key = 'payment_completed'
        """,
        (
            _payment_completed_page_text(),
            _empty_page_buttons(),
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO pages
            (page_key, text_default, buttons_default)
        VALUES ('payment_coupon_message', '', ?)
        """,
        (_empty_page_buttons(),),
    )
    conn.execute(
        """
        UPDATE pages
        SET text_default = '', buttons_default = ?
        WHERE page_key = 'payment_coupon_message'
        """,
        (_empty_page_buttons(),),
    )

    page_key_params = ",".join("?" for _ in PAYMENT_COUPON_V89_PAGE_KEYS)
    v89_batch_row = conn.execute(
        f"""
        SELECT updated_at, COUNT(*)
        FROM pages
        WHERE page_key IN ({page_key_params})
          AND updated_at IS NOT NULL
        GROUP BY updated_at
        HAVING COUNT(*) >= 2
        ORDER BY updated_at ASC
        LIMIT 1
        """,
        PAYMENT_COUPON_V89_PAGE_KEYS,
    ).fetchone()
    v89_batch_timestamp = str(v89_batch_row[0]) if v89_batch_row else None

    cleaned_custom_pages = 0
    for page_key in PAYMENT_COUPON_V89_MISPLACED_PAGE_KEYS:
        row = conn.execute(
            """
            SELECT text_default, text_custom, updated_at
            FROM pages
            WHERE page_key = ?
            """,
            (page_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Required post-payment page is missing: {page_key}"
            )

        text_default = _without_migrated_payment_coupon_suffix(row[0] or '')
        text_custom = row[1]
        migrated_text_custom = text_custom
        if (
            isinstance(text_custom, str)
            and text_custom
            and v89_batch_timestamp is not None
            and str(row[2]) == v89_batch_timestamp
        ):
            migrated_text_custom = _without_migrated_payment_coupon_suffix(
                text_custom
            )
            if migrated_text_custom != text_custom:
                cleaned_custom_pages += 1

        conn.execute(
            """
            UPDATE pages
            SET text_default = ?,
                text_custom = ?
            WHERE page_key = ?
            """,
            (text_default, migrated_text_custom, page_key),
        )

    for page_key, text_default in (
        ('new_key_server_select', _new_key_server_select_page_text()),
        ('new_key_no_servers', _new_key_no_servers_page_text()),
    ):
        conn.execute(
            """
            UPDATE pages
            SET text_default = ?
            WHERE page_key = ?
            """,
            (text_default, page_key),
        )

    for page_key in PAYMENT_COUPON_RESULT_PAGE_KEYS:
        row = conn.execute(
            "SELECT text_default FROM pages WHERE page_key = ?",
            (page_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Required payment-result page is missing: {page_key}"
            )
        conn.execute(
            """
            UPDATE pages
            SET text_default = ?
            WHERE page_key = ?
            """,
            (_with_payment_coupon_placeholder(row[0] or ''), page_key),
        )

    logger.info(
        "Migration v90 applied: payment coupon restored to result pages "
        "(misplaced_custom_slots_removed=%s)",
        cleaned_custom_pages,
    )


def migration_91(conn: sqlite3.Connection) -> None:
    """Migration v91: show discount in the stock automatic-coupon fragment."""
    update_user_ui_text_defaults(
        (
            definition
            for definition in USER_UI_TEXT_DEFINITIONS
            if definition.text_key == 'promo.auto_coupon'
        ),
        conn=conn,
    )
    logger.info(
        "Migration v91 applied: automatic coupon default includes discount"
    )


def _vpn_keys_expiry_requires_v92_rebuild(conn: sqlite3.Connection) -> bool:
    """Returns whether the legacy key table still rejects NULL expiry values."""
    for row in conn.execute("PRAGMA table_info(vpn_keys)").fetchall():
        if row[1] == 'expires_at':
            return bool(row[3])
    raise RuntimeError("vpn_keys.expires_at is missing")


def _rebuild_vpn_keys_for_v92(conn: sqlite3.Connection) -> None:
    """Rebuilds vpn_keys with nullable expiry and per-key admin overrides."""
    source_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(vpn_keys)").fetchall()
    }
    traffic_override_expr = (
        "traffic_limit_override"
        if "traffic_limit_override" in source_columns
        else "NULL"
    )
    max_ips_override_expr = (
        "max_ips_override" if "max_ips_override" in source_columns else "NULL"
    )

    conn.execute("DROP TABLE IF EXISTS vpn_keys_v92")
    conn.execute(
        """
        CREATE TABLE vpn_keys_v92 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            server_id INTEGER,
            tariff_id INTEGER NOT NULL,
            panel_inbound_id INTEGER,
            client_uuid TEXT,
            panel_email TEXT,
            custom_name TEXT,
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            traffic_updated_at DATETIME,
            traffic_notified_pct INTEGER DEFAULT 100,
            sub_id TEXT,
            traffic_limit_override INTEGER
                CHECK (traffic_limit_override IS NULL OR traffic_limit_override >= 0),
            max_ips_override INTEGER
                CHECK (max_ips_override IS NULL OR max_ips_override BETWEEN 1 AND 999),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO vpn_keys_v92 (
            id, user_id, server_id, tariff_id, panel_inbound_id, client_uuid,
            panel_email, custom_name, expires_at, created_at, traffic_used,
            traffic_limit, traffic_updated_at, traffic_notified_pct, sub_id,
            traffic_limit_override, max_ips_override
        )
        SELECT
            id, user_id, server_id, tariff_id, panel_inbound_id, client_uuid,
            panel_email, custom_name, expires_at, created_at, traffic_used,
            traffic_limit, traffic_updated_at, traffic_notified_pct, sub_id,
            {traffic_override_expr}, {max_ips_override_expr}
        FROM vpn_keys
        """
    )
    conn.execute("DROP TABLE vpn_keys")
    conn.execute("ALTER TABLE vpn_keys_v92 RENAME TO vpn_keys")
    conn.execute("CREATE INDEX idx_vpn_keys_user_id ON vpn_keys(user_id)")
    conn.execute("CREATE INDEX idx_vpn_keys_expires_at ON vpn_keys(expires_at)")
    conn.execute(
        "CREATE INDEX idx_vpn_keys_user_expires "
        "ON vpn_keys(user_id, expires_at DESC)"
    )
    conn.execute(
        "CREATE INDEX idx_vpn_keys_server_email "
        "ON vpn_keys(server_id, panel_email)"
    )
    conn.execute(
        "CREATE INDEX idx_vpn_keys_panel_email_lower "
        "ON vpn_keys(LOWER(panel_email))"
    )
    conn.execute("CREATE INDEX idx_vpn_keys_server_id ON vpn_keys(server_id)")


def migration_92(conn: sqlite3.Connection) -> None:
    """Migration v92: group reset policy, unlimited keys and admin plans."""
    requires_rebuild = _vpn_keys_expiry_requires_v92_rebuild(conn)
    foreign_keys_disabled = False
    if requires_rebuild:
        # PRAGMA foreign_keys cannot be changed while a transaction is active.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        foreign_keys_disabled = True

    try:
        _add_column(
            conn,
            "tariff_groups",
            "monthly_traffic_reset_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK (monthly_traffic_reset_enabled IN (0, 1))",
        )
        _add_column(conn, "tariffs", "system_type TEXT")

        if requires_rebuild:
            _rebuild_vpn_keys_for_v92(conn)
        else:
            _add_column(
                conn,
                "vpn_keys",
                "traffic_limit_override INTEGER "
                "CHECK (traffic_limit_override IS NULL OR "
                "traffic_limit_override >= 0)",
            )
            _add_column(
                conn,
                "vpn_keys",
                "max_ips_override INTEGER "
                "CHECK (max_ips_override IS NULL OR "
                "max_ips_override BETWEEN 1 AND 999)",
            )

        reset_row = conn.execute(
            "SELECT value FROM settings "
            "WHERE key = 'monthly_traffic_reset_enabled'"
        ).fetchone()
        if reset_row is not None:
            reset_enabled = int(
                str(reset_row[0]).strip().lower()
                in {'1', 'true', 'yes', 'on'}
            )
            conn.execute(
                "UPDATE tariff_groups "
                "SET monthly_traffic_reset_enabled = ?",
                (reset_enabled,),
            )
            conn.execute(
                "DELETE FROM settings "
                "WHERE key = 'monthly_traffic_reset_enabled'"
            )

        legacy_admin = conn.execute(
            """
            SELECT id, group_id, max_ips
            FROM tariffs
            WHERE system_type = 'admin_custom' OR name = 'Admin Tariff'
            ORDER BY CASE WHEN system_type = 'admin_custom' THEN 0 ELSE 1 END, id
            LIMIT 1
            """
        ).fetchone()
        if legacy_admin:
            legacy_admin_id = int(legacy_admin[0])
            legacy_group_id = int(legacy_admin[1] or 1)
            legacy_max_ips = max(1, min(999, int(legacy_admin[2] or 1)))
            conn.execute(
                """
                UPDATE vpn_keys
                SET traffic_limit_override = COALESCE(
                        traffic_limit_override,
                        MAX(0, COALESCE(traffic_limit, 0))
                    ),
                    max_ips_override = COALESCE(max_ips_override, ?)
                WHERE tariff_id = ?
                """,
                (legacy_max_ips, legacy_admin_id),
            )
            conn.execute(
                """
                UPDATE tariffs
                SET group_id = ?, system_type = 'admin_custom', is_active = 0,
                    duration_days = 0, traffic_limit_gb = 0, max_ips = 1,
                    price_rub = 0, price_minor = 0
                WHERE id = ?
                """,
                (legacy_group_id, legacy_admin_id),
            )

        groups = conn.execute(
            "SELECT id FROM tariff_groups ORDER BY id"
        ).fetchall()
        for group in groups:
            group_id = int(group[0])
            existing = conn.execute(
                """
                SELECT id FROM tariffs
                WHERE group_id = ? AND system_type = 'admin_custom'
                ORDER BY id LIMIT 1
                """,
                (group_id,),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO tariffs (
                    name, duration_days, price_rub, price_minor, display_order,
                    is_active, traffic_limit_gb, group_id, max_ips, system_type
                )
                VALUES (?, 0, 0, 0, 999, 0, 0, ?, 1, 'admin_custom')
                """,
                (f'Admin Custom {group_id}', group_id),
            )

        conn.execute(
            """
            UPDATE settings
            SET value = ''
            WHERE key = 'trial_tariff_id'
              AND CAST(value AS INTEGER) IN (
                    SELECT id FROM tariffs
                    WHERE system_type = 'admin_custom'
              )
            """
        )

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tariffs_admin_custom_group "
            "ON tariffs(group_id) WHERE system_type = 'admin_custom'"
        )
        update_user_ui_text_defaults(
            (
                definition
                for definition in USER_UI_TEXT_DEFINITIONS
                if definition.text_key
                in {'format.duration_unlimited', 'key.tariff.custom'}
            ),
            conn=conn,
        )

        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                "foreign_key_check failed after vpn_keys rebuild: "
                f"{foreign_key_errors[:5]}"
            )

        if foreign_keys_disabled:
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            foreign_keys_disabled = False
    except Exception:
        if foreign_keys_disabled:
            conn.rollback()
            conn.execute("PRAGMA foreign_keys = ON")
        raise

    logger.info(
        "Migration v92 applied: tariff-group reset policies, nullable key "
        "expiry and protected admin plans are ready"
    )


def _create_trial_tables_v93(conn: sqlite3.Connection) -> None:
    """Creates the persisted trial-offer model and its integrity guards."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trial_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tariff_id INTEGER REFERENCES tariffs(id) ON DELETE RESTRICT,
            is_primary INTEGER NOT NULL DEFAULT 0
                CHECK (is_primary IN (0, 1)),
            is_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (is_enabled IN (0, 1)),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (is_primary = 1 OR tariff_id IS NOT NULL)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_offers_primary "
        "ON trial_offers(is_primary) WHERE is_primary = 1"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trial_activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            offer_id INTEGER,
            tariff_id INTEGER,
            group_id INTEGER,
            vpn_key_id INTEGER REFERENCES vpn_keys(id) ON DELETE SET NULL,
            payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL,
            legacy_global_block INTEGER NOT NULL DEFAULT 0
                CHECK (legacy_global_block IN (0, 1)),
            activated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_activations_user_group "
        "ON trial_activations(user_id, group_id) "
        "WHERE legacy_global_block = 0 AND group_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trial_activations_legacy_user "
        "ON trial_activations(user_id) WHERE legacy_global_block = 1"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trial_activations_offer "
        "ON trial_activations(offer_id)"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_protect_primary_delete
        BEFORE DELETE ON trial_offers
        WHEN OLD.is_primary = 1
        BEGIN
            SELECT RAISE(ABORT, 'primary trial offer cannot be deleted');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_protect_primary_marker
        BEFORE UPDATE OF is_primary ON trial_offers
        WHEN NEW.is_primary <> OLD.is_primary
        BEGIN
            SELECT RAISE(ABORT, 'trial offer primary marker is immutable');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_reject_missing_insert
        BEFORE INSERT ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND NOT EXISTS (
                SELECT 1 FROM tariffs WHERE id = NEW.tariff_id
             )
        BEGIN
            SELECT RAISE(ABORT, 'trial tariff does not exist');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_reject_missing_update
        BEFORE UPDATE OF tariff_id ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND NOT EXISTS (
                SELECT 1 FROM tariffs WHERE id = NEW.tariff_id
             )
        BEGIN
            SELECT RAISE(ABORT, 'trial tariff does not exist');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_reject_system_insert
        BEFORE INSERT ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND EXISTS (
                SELECT 1 FROM tariffs
                WHERE id = NEW.tariff_id AND system_type IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'system tariff cannot be used by a trial offer');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_reject_system_update
        BEFORE UPDATE OF tariff_id ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND EXISTS (
                SELECT 1 FROM tariffs
                WHERE id = NEW.tariff_id AND system_type IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'system tariff cannot be used by a trial offer');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_offers_touch_updated_at
        AFTER UPDATE OF tariff_id, is_enabled ON trial_offers
        BEGIN
            UPDATE trial_offers
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = NEW.id;
        END
        """
    )


def _migrate_trial_button_v93(raw_json: str | None) -> str | None:
    """Converts only the released legacy activate action to a context action."""
    if raw_json is None:
        return None
    try:
        buttons = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return raw_json
    if not isinstance(buttons, list):
        return raw_json

    changed = False
    for button in buttons:
        if not isinstance(button, dict):
            continue
        if (
            button.get('id') == 'btn_activate_trial'
            and button.get('action_type') == 'internal'
            and button.get('action_value') == 'cmd_activate_trial'
        ):
            button['action_type'] = 'system'
            button['action_value'] = None
            changed = True
    if not changed:
        return raw_json
    return json.dumps(buttons, ensure_ascii=False)


def migration_93(conn: sqlite3.Connection) -> None:
    """Migration v93: group-aware persisted trial offers and activations."""
    activation_table_existed = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'trial_activations'
        """
    ).fetchone() is not None
    legacy_settings = {
        str(row['key']): str(row['value'] or '')
        for row in conn.execute(
            """
            SELECT key, value FROM settings
            WHERE key IN ('trial_enabled', 'trial_tariff_id')
            """
        ).fetchall()
    }

    _create_trial_tables_v93(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('trial_usage_scope', 'once_per_user')
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_usage_scope_insert
        BEFORE INSERT ON settings
        WHEN NEW.key = 'trial_usage_scope'
         AND NEW.value NOT IN ('once_per_user', 'once_per_group')
        BEGIN
            SELECT RAISE(ABORT, 'invalid trial usage scope');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_trial_usage_scope_update
        BEFORE UPDATE OF value ON settings
        WHEN NEW.key = 'trial_usage_scope'
         AND NEW.value NOT IN ('once_per_user', 'once_per_group')
        BEGIN
            SELECT RAISE(ABORT, 'invalid trial usage scope');
        END
        """
    )

    primary = conn.execute(
        "SELECT id FROM trial_offers WHERE is_primary = 1 LIMIT 1"
    ).fetchone()
    migrated_tariff_id = None
    raw_tariff_id = legacy_settings.get('trial_tariff_id', '')
    if raw_tariff_id.isdecimal():
        candidate = conn.execute(
            """
            SELECT id FROM tariffs
            WHERE id = ? AND system_type IS NULL
            """,
            (int(raw_tariff_id),),
        ).fetchone()
        if candidate is not None:
            migrated_tariff_id = int(candidate['id'])
    migrated_enabled = int(
        legacy_settings.get('trial_enabled', '0').strip().casefold()
        in {'1', 'true', 'yes', 'on'}
    )

    if primary is None:
        conn.execute(
            """
            INSERT INTO trial_offers (
                tariff_id, is_primary, is_enabled
            ) VALUES (?, 1, ?)
            """,
            (migrated_tariff_id, migrated_enabled),
        )
    elif legacy_settings:
        conn.execute(
            """
            UPDATE trial_offers
            SET tariff_id = ?, is_enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (migrated_tariff_id, migrated_enabled, int(primary['id'])),
        )

    should_backfill_legacy = (not activation_table_existed) or bool(legacy_settings)
    if should_backfill_legacy:
        conn.execute(
            """
            UPDATE users
            SET used_trial = 1
            WHERE EXISTS (
                SELECT 1 FROM payments p
                WHERE p.user_id = users.id
                  AND p.payment_type = 'trial'
                  AND p.status = 'paid'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trial_activations (
                user_id, legacy_global_block
            )
            SELECT u.id, 1
            FROM users u
            WHERE COALESCE(u.used_trial, 0) = 1
              AND NOT EXISTS (
                    SELECT 1 FROM trial_activations ta
                    WHERE ta.user_id = u.id
                      AND ta.legacy_global_block = 1
              )
            """
        )

    conn.execute(
        "DELETE FROM settings WHERE key IN ('trial_enabled', 'trial_tariff_id')"
    )

    trial_text = (
        "🎁 <b>Пробная подписка</b>\n\n"
        "Попробуйте VPN бесплатно и оцените качество соединения.\n\n"
        "%trial_offer%\n\n"
        "Нажмите кнопку ниже, чтобы активировать пробный доступ.\n\n"
        "%trial_eligibility%"
    )
    trial_row = conn.execute(
        """
        SELECT buttons_default, buttons_custom
        FROM pages WHERE page_key = 'trial'
        """
    ).fetchone()
    if trial_row is not None:
        conn.execute(
            """
            UPDATE pages
            SET text_default = ?, buttons_default = ?, buttons_custom = ?
            WHERE page_key = 'trial'
            """,
            (
                trial_text,
                _migrate_trial_button_v93(trial_row['buttons_default']),
                _migrate_trial_button_v93(trial_row['buttons_custom']),
            ),
        )
    conn.execute(
        """
        UPDATE pages
        SET text_default = ?
        WHERE page_key = 'trial_already_used'
        """,
        (
            "🎁 <b>Пробный период недоступен</b>\n\n"
            "Это пробное предложение уже недоступно для вашего аккаунта.",
        ),
    )

    update_user_ui_text_defaults(
        (
            definition
            for definition in USER_UI_TEXT_DEFINITIONS
            if definition.text_key in {
                'format.traffic_gb',
                'trial.offer.summary',
                'trial.eligibility.once_per_user',
                'trial.eligibility.once_per_group',
            }
        ),
        conn=conn,
    )

    foreign_key_errors = conn.execute('PRAGMA foreign_key_check').fetchall()
    if foreign_key_errors:
        raise RuntimeError(
            "foreign_key_check failed after trial migration: "
            f"{foreign_key_errors[:5]}"
        )
    logger.info(
        "Migration v93 applied: persisted primary/additional trial offers and "
        "group-aware eligibility are ready"
    )


_RETIRED_KEY_PAGE_PLACEHOLDERS = (
    '%key(field=inbound)%',
    '%key(field=protocol)%',
    '%key_inbound%',
    '%key_protocol%',
    '%ключ_инбаунд%',
    '%ключ_протокол%',
)
_RETIRED_KEY_PAGE_PLACEHOLDER_PATTERN = (
    r'(?:'
    + '|'.join(
        re.escape(placeholder)
        for placeholder in sorted(
            _RETIRED_KEY_PAGE_PLACEHOLDERS,
            key=len,
            reverse=True,
        )
    )
    + r')'
)
_RETIRED_KEY_PAGE_PLACEHOLDER_RE = re.compile(
    _RETIRED_KEY_PAGE_PLACEHOLDER_PATTERN,
    re.IGNORECASE,
)
_RETIRED_KEY_WRAPPED_PLACEHOLDER_RE = re.compile(
    r'\(\s*'
    + _RETIRED_KEY_PAGE_PLACEHOLDER_PATTERN
    + r'(?:\s*[,/|·-]\s*'
    + _RETIRED_KEY_PAGE_PLACEHOLDER_PATTERN
    + r')*\s*\)',
    re.IGNORECASE,
)
_RETIRED_KEY_SEGMENT_SEPARATOR_RE = re.compile(
    r'(\s+(?:[·|/]|[-–—])\s+|[,;]\s*)'
)
_RETIRED_KEY_BUTTON_DISPLAY_FIELDS = frozenset({
    'label',
    'text',
    'title',
    'item_label',
    'item_label_template',
})
_RETIRED_KEY_BUTTON_TARGET_FIELDS = frozenset({
    'id',
    'url',
    'action_value',
    'callback_data',
    'web_app',
})
_DROP_RETIRED_KEY_BUTTON = object()


def _contains_retired_key_page_placeholder(value: str) -> bool:
    """Returns whether a stored customization uses a retired key field."""
    return _RETIRED_KEY_PAGE_PLACEHOLDER_RE.search(value) is not None


def _remove_retired_key_page_tokens(value: str) -> str:
    """Removes only retired tokens without interpreting the surrounding data."""
    return _RETIRED_KEY_PAGE_PLACEHOLDER_RE.sub('', value)


def _clean_retired_key_text_segment(segment: str) -> str | None:
    """Removes one retired-only display clause while preserving mixed clauses."""
    placeholders = _ATOMIC_KEY_PAGE_PLACEHOLDER_RE.findall(segment)
    if not any(
        _RETIRED_KEY_PAGE_PLACEHOLDER_RE.fullmatch(placeholder)
        for placeholder in placeholders
    ):
        return segment

    has_supported_placeholder = any(
        _RETIRED_KEY_PAGE_PLACEHOLDER_RE.fullmatch(placeholder) is None
        for placeholder in placeholders
    )
    if not has_supported_placeholder:
        return None

    leading = segment[:len(segment) - len(segment.lstrip(' \t'))]
    trailing = segment[len(segment.rstrip(' \t')):]
    cleaned = _RETIRED_KEY_WRAPPED_PLACEHOLDER_RE.sub('', segment.strip(' \t'))
    cleaned = _remove_retired_key_page_tokens(cleaned)
    cleaned = re.sub(r'\(\s*\)|\[\s*\]', '', cleaned)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    cleaned = re.sub(r'^\s*(?:[·|/,;]|[-–—])+\s*', '', cleaned)
    cleaned = re.sub(r'\s*(?:[·|/,;]|[-–—])+\s*$', '', cleaned)
    cleaned = cleaned.strip(' \t')
    if not cleaned:
        return None
    return f'{leading}{cleaned}{trailing}'


def _clean_retired_key_text_line(line: str) -> str | None:
    """Cleans retired key fields from one line of a stored text template."""
    if not _contains_retired_key_page_placeholder(line):
        return line

    parts = _RETIRED_KEY_SEGMENT_SEPARATOR_RE.split(line)
    kept_parts: list[str] = []
    for index in range(0, len(parts), 2):
        cleaned = _clean_retired_key_text_segment(parts[index])
        if cleaned is None:
            continue
        if kept_parts and index > 0:
            kept_parts.append(parts[index - 1])
        kept_parts.append(cleaned)

    if not kept_parts:
        return None

    cleaned_line = ''.join(kept_parts)
    cleaned_line = re.sub(r'[ \t]{2,}', ' ', cleaned_line)
    cleaned_line = re.sub(r'\s*(?:[·|/,;]|[-–—])+\s*$', '', cleaned_line)
    return cleaned_line.rstrip(' \t') or None


def _clean_retired_key_text(value: str | None) -> str | None:
    """Cleans retired key fields from a page or item text without resetting it."""
    if value is None or not _contains_retired_key_page_placeholder(value):
        return value

    result: list[str] = []
    for raw_line in value.splitlines(keepends=True):
        if raw_line.endswith('\r\n'):
            line, ending = raw_line[:-2], '\r\n'
        elif raw_line.endswith(('\r', '\n')):
            line, ending = raw_line[:-1], raw_line[-1]
        else:
            line, ending = raw_line, ''
        cleaned_line = _clean_retired_key_text_line(line)
        if cleaned_line is not None:
            result.append(f'{cleaned_line}{ending}')
    return ''.join(result)


def _clean_retired_key_button_value(value, *, field_name: str | None = None):
    """Recursively cleans one decoded button JSON value."""
    if isinstance(value, str):
        if not _contains_retired_key_page_placeholder(value):
            return value, 0
        if field_name in _RETIRED_KEY_BUTTON_TARGET_FIELDS:
            return _DROP_RETIRED_KEY_BUTTON, 1
        if field_name in _RETIRED_KEY_BUTTON_DISPLAY_FIELDS:
            cleaned = _clean_retired_key_text(value)
            if cleaned is None or not cleaned.strip():
                return _DROP_RETIRED_KEY_BUTTON, 1
            return cleaned, 0
        return _remove_retired_key_page_tokens(value), 0

    if isinstance(value, list):
        cleaned_items = []
        dropped = 0
        for child in value:
            cleaned_child, child_dropped = _clean_retired_key_button_value(child)
            dropped += child_dropped
            if cleaned_child is not _DROP_RETIRED_KEY_BUTTON:
                cleaned_items.append(cleaned_child)
        return cleaned_items, dropped

    if isinstance(value, dict):
        for key in _RETIRED_KEY_BUTTON_TARGET_FIELDS:
            target = value.get(key)
            if isinstance(target, str) and _contains_retired_key_page_placeholder(target):
                return _DROP_RETIRED_KEY_BUTTON, 1

        cleaned_mapping = {}
        dropped = 0
        for key, child in value.items():
            cleaned_child, child_dropped = _clean_retired_key_button_value(
                child,
                field_name=str(key).casefold(),
            )
            dropped += child_dropped
            if cleaned_child is _DROP_RETIRED_KEY_BUTTON:
                return _DROP_RETIRED_KEY_BUTTON, dropped or 1
            cleaned_mapping[key] = cleaned_child
        return cleaned_mapping, dropped

    return value, 0


def _clean_retired_key_button_json(
    value: str | None,
) -> tuple[str | None, int, bool]:
    """Cleans button JSON and reports dropped buttons and malformed input."""
    if value is None:
        return value, 0, False
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        if not _contains_retired_key_page_placeholder(value):
            return value, 0, True
        return _remove_retired_key_page_tokens(value), 0, True

    cleaned, dropped = _clean_retired_key_button_value(parsed)
    if cleaned is _DROP_RETIRED_KEY_BUTTON:
        cleaned = []
        dropped = max(1, dropped)
    if cleaned == parsed and dropped == 0:
        return value, 0, False
    return json.dumps(cleaned, ensure_ascii=False), dropped, False


def migration_94(conn: sqlite3.Connection) -> None:
    """Migration v94: retire inbound/protocol key presentation fields."""
    deleted_texts = conn.execute(
        "DELETE FROM user_ui_texts WHERE text_key = 'key.inbound.all_protocols'"
    ).rowcount

    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('my_keys_item_template', ?)
        """,
        (_my_keys_item_template(),),
    )
    setting_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'my_keys_item_template'"
    ).fetchone()
    settings_changed = 0
    if setting_row is not None:
        current_value = setting_row[0]
        cleaned_value = _clean_retired_key_text(current_value)
        if cleaned_value != current_value:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'my_keys_item_template'",
                (cleaned_value,),
            )
            settings_changed = 1

    page_values_changed = 0
    dropped_buttons = 0
    malformed_button_values = 0
    for column in ('text_default', 'text_custom', 'buttons_default', 'buttons_custom'):
        rows = conn.execute(
            f"SELECT page_key, {column} FROM pages WHERE {column} IS NOT NULL"
        ).fetchall()
        for page_key, current_value in rows:
            if not isinstance(current_value, str):
                continue
            if column.startswith('buttons_'):
                cleaned_value, dropped, malformed = _clean_retired_key_button_json(
                    current_value
                )
                dropped_buttons += dropped
                malformed_button_values += int(malformed)
            else:
                cleaned_value = _clean_retired_key_text(current_value)
            if cleaned_value != current_value:
                conn.execute(
                    f"UPDATE pages SET {column} = ? WHERE page_key = ?",
                    (cleaned_value, page_key),
                )
                page_values_changed += 1

    logger.info(
        "Migration v94 applied: retired key presentation fields removed "
        "(ui_texts_deleted=%s, settings_changed=%s, page_values_changed=%s, "
        "buttons_dropped=%s, malformed_button_values=%s)",
        deleted_texts,
        settings_changed,
        page_values_changed,
        dropped_buttons,
        malformed_button_values,
    )


def migration_95(conn: sqlite3.Connection) -> None:
    """Migration v95: requeue background-completed purchases left as drafts."""
    repaired = conn.execute(
        """
        UPDATE payment_auto_checks
        SET state = 'provider_succeeded',
            next_check_at = CURRENT_TIMESTAMP,
            completion_attempts = 1,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE state = 'completed'
          AND EXISTS (
                SELECT 1
                FROM payments p
                JOIN vpn_keys vk ON vk.id = p.vpn_key_id
                WHERE p.order_id = payment_auto_checks.order_id
                  AND p.intent_version = 1
                  AND p.purpose = 'key_purchase'
                  AND p.status = 'paid'
                  AND p.fulfillment_status = 'completed'
                  AND vk.server_id IS NULL
          )
        """
    ).rowcount
    logger.info(
        "Migration v95 applied: requeued %s background paid key drafts",
        repaired,
    )


def migration_96(conn: sqlite3.Connection) -> None:
    """Migration v96: add tariff and device limit to the stock key card."""
    updated = conn.execute(
        """
        UPDATE pages
        SET text_default = ?
        WHERE page_key = 'key_details'
        """,
        (_key_details_page_text_v96(),),
    ).rowcount
    logger.info(
        "Migration v96 applied: refreshed %s stock key-details default",
        updated,
    )


def migration_97(conn: sqlite3.Connection) -> None:
    """Migration v97: add per-user numbering for future key display names."""
    _add_column(
        conn,
        'users',
        'last_key_number INTEGER NOT NULL DEFAULT 0 CHECK (last_key_number >= 0)',
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('key_name_prefix', 'Ключ')
        """
    )
    logger.info(
        "Migration v97 applied: per-user key counters and key_name_prefix are ready"
    )


MIGRATIONS = {
    74: migration_74,
    75: migration_75,
    76: migration_76,
    77: migration_77,
    78: migration_78,
    79: migration_79,
    80: migration_80,
    81: migration_81,
    82: migration_82,
    83: migration_83,
    84: migration_84,
    85: migration_85,
    86: migration_86,
    87: migration_87,
    88: migration_88,
    89: migration_89,
    90: migration_90,
    91: migration_91,
    92: migration_92,
    93: migration_93,
    94: migration_94,
    95: migration_95,
    96: migration_96,
    97: migration_97,
}


def _assert_migration_database_integrity(
    conn: sqlite3.Connection,
    *,
    stage: str,
) -> None:
    """Fail a migration boundary on structural or foreign-key corruption."""
    quick_rows = conn.execute('PRAGMA quick_check').fetchall()
    if len(quick_rows) != 1 or quick_rows[0][0] != 'ok':
        raise RuntimeError(
            f"quick_check failed {stage}: {quick_rows[:5]}"
        )
    foreign_key_rows = conn.execute('PRAGMA foreign_key_check').fetchall()
    if foreign_key_rows:
        raise RuntimeError(
            f"foreign_key_check failed {stage}: {foreign_key_rows[:5]}"
        )



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

        with get_db() as validation_conn:
            _assert_migration_database_integrity(
                validation_conn,
                stage="before migrations",
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

        with get_db() as validation_conn:
            _assert_migration_database_integrity(
                validation_conn,
                stage="after migrations",
            )
            version_row = validation_conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            final_version = int(version_row[0]) if version_row else 0
            if final_version != LATEST_VERSION:
                raise RuntimeError(
                    f"schema version mismatch after migrations: "
                    f"expected {LATEST_VERSION}, got {final_version}"
                )
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
