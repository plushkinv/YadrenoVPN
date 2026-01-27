"""
Клавиатуры для пользовательской части бота.

Inline-клавиатуры для обычных пользователей.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """
    Главное меню пользователя.
    
    Args:
        is_admin: Показывать ли кнопку админ-панели
    """
    builder = InlineKeyboardBuilder()
    
    # Основные кнопки
    builder.row(
        InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"),
        InlineKeyboardButton(text="💳 Купить ключ", callback_data="buy_key")
    )
    
    builder.row(
        InlineKeyboardButton(text="❓ Справка", callback_data="help")
    )
    
    # Кнопка админ-панели (только для админов)
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")
        )
    
    return builder.as_markup()


def help_kb(news_link: str, support_link: str) -> InlineKeyboardMarkup:
    """
    Клавиатура справки с внешними ссылками.
    
    Args:
        news_link: Ссылка на канал новостей
        support_link: Ссылка на чат поддержки
    """
    builder = InlineKeyboardBuilder()
    
    # Новости и Поддержка в одном ряду
    builder.row(
        InlineKeyboardButton(text="📢 Новости", url=news_link),
        InlineKeyboardButton(text="💬 Поддержка", url=support_link)
    )
    
    # На главную
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def buy_key_kb(crypto_url: str = None, stars_enabled: bool = False, order_id: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура для страницы «Купить ключ».
    
    Args:
        crypto_url: URL для оплаты криптой (если настроен)
        stars_enabled: Показывать ли кнопку оплаты Stars
        order_id: ID созданного ордера (для оптимизации Stars)
    """
    builder = InlineKeyboardBuilder()
    
    # Кнопки оплаты (показываем только включённые методы)
    # USDT — внешняя ссылка
    if crypto_url:
        builder.row(
            InlineKeyboardButton(text="💰 Оплатить USDT", url=crypto_url)
        )
    
    # Stars — переход к выбору тарифа
    if stars_enabled:
        cb_data = f"pay_stars:{order_id}" if order_id else "pay_stars"
        builder.row(
            InlineKeyboardButton(text="⭐ Оплатить звёздами", callback_data=cb_data)
        )
    
    # Кнопка «На главную» — последний ряд
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def tariff_select_kb(tariffs: list, back_callback: str = "buy_key", order_id: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для оплаты Stars.
    
    Args:
        tariffs: Список тарифов из БД
        back_callback: Callback для кнопки «Назад»
        order_id: ID существующего ордера (для оптимизации)
    """
    builder = InlineKeyboardBuilder()
    
    for tariff in tariffs:
        # Если есть order_id, передаем его
        cb_data = f"stars_pay:{tariff['id']}:{order_id}" if order_id else f"stars_pay:{tariff['id']}"
        
        builder.row(
            InlineKeyboardButton(
                text=f"⭐ {tariff['name']} — {tariff['price_stars']} звёзд",
                callback_data=cb_data
            )
        )
    
    # Кнопки навигации
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def back_button_kb(back_callback: str = "start") -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'На главную'."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data=back_callback)
    )
    return builder.as_markup()


def back_and_home_kb(back_callback: str) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопками 'Назад' и 'На главную'.
    
    Args:
        back_callback: Callback для кнопки 'Назад'
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


def cancel_kb(cancel_callback: str) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопкой 'Отмена'.
    
    Args:
        cancel_callback: Callback для кнопки 'Отмена'
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_callback)
    )
    return builder.as_markup()


def my_keys_list_kb(keys: list) -> InlineKeyboardMarkup:
    """
    Клавиатура со списком ключей пользователя.
    
    Args:
        keys: Список ключей из get_user_keys_for_display()
    """
    builder = InlineKeyboardBuilder()
    
    for key in keys:
        # Эмодзи статуса: 🟢 активен, 🔴 истёк, ⚪ выключен
        if key['is_active']:
            status_emoji = "🟢"
        else:
            status_emoji = "🔴"
        
        builder.row(
            InlineKeyboardButton(
                text=f"{status_emoji} {key['display_name']}",
                callback_data=f"key:{key['id']}"
            )
        )
    
    # Кнопка «На главную» — последний ряд
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def key_manage_kb(key_id: int, is_unconfigured: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура управления ключом.
    
    Args:
        key_id: ID ключа
        is_unconfigured: True, если ключ не настроен (Draft)
    """
    builder = InlineKeyboardBuilder()
    
    if is_unconfigured:
        # Для ненастроенного ключа предлагаем настройку
        builder.row(
            InlineKeyboardButton(text="⚙️ Настроить", callback_data=f"key_replace:{key_id}"),
            InlineKeyboardButton(text="📈 Продлить", callback_data=f"key_renew:{key_id}")
        )
        builder.row(
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"key_rename:{key_id}")
        )
    else:
        # Стандартные кнопки
        builder.row(
            InlineKeyboardButton(text="📋 Показать ключ", callback_data=f"key_show:{key_id}"),
            InlineKeyboardButton(text="📈 Продлить", callback_data=f"key_renew:{key_id}")
        )
        
        builder.row(
            InlineKeyboardButton(text="🔄 Заменить", callback_data=f"key_replace:{key_id}"),
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"key_rename:{key_id}")
        )
    
    # ТРЕТИЙ ряд (унифицированный): Инструкция и Мои ключи
    builder.row(
        InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def key_show_kb(key_id: int = None) -> InlineKeyboardMarkup:
    """
    Клавиатура на странице отображения ключа (QR-код).
    Теперь универсальная.
    """
    return key_issued_kb()


def renew_tariff_select_kb(tariffs: list, key_id: int, order_id: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для продления ключа (для Stars).
    
    Args:
        tariffs: Список активных тарифов
        key_id: ID ключа для продления
        order_id: ID ордера (для оптимизации)
    """
    builder = InlineKeyboardBuilder()
    
    for tariff in tariffs:
        # Цена в Stars
        price_stars = tariff['price_stars']
        
        # Формируем callback: renew_pay_stars:KEY_ID:TARIFF_ID[:ORDER_ID]
        cb_data = f"renew_pay_stars:{key_id}:{tariff['id']}"
        if order_id:
            cb_data += f":{order_id}"
            
        builder.row(
            InlineKeyboardButton(
                text=f"⭐ {tariff['name']} — {price_stars} звёзд",
                callback_data=cb_data
            )
        )
    
    # Последний ряд: назад и на главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_renew:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def renew_payment_method_kb(key_id: int, crypto_url: str = None, stars_enabled: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора способа оплаты для продления (первый шаг).
    
    Args:
        key_id: ID ключа
        crypto_url: URL для оплаты криптой (с placeholder тарифом)
        stars_enabled: Доступна ли оплата Stars
    """
    builder = InlineKeyboardBuilder()
    
    # USDT — внешняя ссылка (если настроено)
    if crypto_url:
        builder.row(
            InlineKeyboardButton(text="💰 Оплатить USDT", url=crypto_url)
        )
    
    # Stars — переход к выбору тарифа
    if stars_enabled:
        builder.row(
            InlineKeyboardButton(
                text="⭐ Оплатить звёздами", 
                callback_data=f"renew_stars_tariff:{key_id}"
            )
        )
    
    # Последний ряд: назад и на главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


# ============================================================================
# ЗАМЕНА КЛЮЧА
# ============================================================================

def replace_server_list_kb(servers: list, key_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора сервера для замены ключа.
    
    Args:
        servers: Список серверов
        key_id: ID ключа
    """
    builder = InlineKeyboardBuilder()
    
    for server in servers:
        # Для пользователя не показываем сложные детали, только имя и статус
        status_emoji = "🟢" if server.get('is_active') else "🔴"
        text = f"{status_emoji} {server['name']}"
        
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"replace_server:{server['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"key:{key_id}")
    )
    
    return builder.as_markup()


def replace_inbound_list_kb(inbounds: list, key_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора протокола для замены ключа.
    
    Args:
        inbounds: Список inbound
        key_id: ID ключа
    """
    builder = InlineKeyboardBuilder()
    
    for inbound in inbounds:
        remark = inbound.get('remark', 'VPN') or "VPN"
        protocol = inbound.get('protocol', 'vless').upper()
        text = f"{remark} ({protocol})"
        
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"replace_inbound:{inbound['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_replace:{key_id}")
    )
    
    return builder.as_markup()


def replace_confirm_kb(key_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения замены.
    
    Args:
        key_id: ID ключа
    """
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, заменить",
            callback_data="replace_confirm"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"key:{key_id}"
        )
    )
    
    return builder.as_markup()

# ============================================================================
# НОВЫЙ КЛЮЧ (ПОСЛЕ ОПЛАТЫ)
# ============================================================================

def new_key_server_list_kb(servers: list) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора сервера для создания нового ключа.
    
    Args:
        servers: Список серверов
    """
    builder = InlineKeyboardBuilder()
    
    for server in servers:
        status_emoji = "🟢" if server.get('is_active') else "🔴"
        text = f"{status_emoji} {server['name']}"
        
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"new_key_server:{server['id']}"
            )
        )
    
    # Кнопка «На главную» — на случай если передумал (ключ можно создать потом через поддержку, 
    # но логика бота пока этого не предусматривает -> pending order останется paid но без vpn_key_id.
    # TODO: Реализовать "досоздание" ключа позже.
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def new_key_inbound_list_kb(inbounds: list) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора протокола для создания нового ключа.
    
    Args:
        inbounds: Список inbound
    """
    builder = InlineKeyboardBuilder()
    
    for inbound in inbounds:
        remark = inbound.get('remark', 'VPN') or "VPN"
        protocol = inbound.get('protocol', 'vless').upper()
        text = f"{remark} ({protocol})"
        
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"new_key_inbound:{inbound['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_server_select") # спец. callback для возврата
    )
    
    return builder.as_markup()


def key_issued_kb() -> InlineKeyboardMarkup:
    """
    Универсальная клавиатура после выдачи или при показе ключа.
    
    Layout:
    1. Инструкция | Мои ключи
    2. На главную
    """
    builder = InlineKeyboardBuilder()
    
    # Первый ряд
    builder.row(
        InlineKeyboardButton(text="📄 Инструкция", callback_data="help"),
        InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")
    )
    
    # Второй ряд
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()
