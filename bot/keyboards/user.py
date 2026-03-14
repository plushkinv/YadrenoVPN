"""
Клавиатуры для пользовательской части бота.

Inline-клавиатуры для обычных пользователей.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(is_admin: bool = False, show_trial: bool = False) -> InlineKeyboardMarkup:
    """
    Главное меню пользователя.
    
    Args:
        is_admin: Показывать ли кнопку админ-панели
        show_trial: Показывать ли кнопку «Гифт Пробная подписка»
    """
    builder = InlineKeyboardBuilder()
    
    # Основные кнопки
    builder.row(
        InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"),
        InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_key")
    )
    
    # Кнопка «Пробная подписка» (над Справкой, если доступна)
    if show_trial:
        builder.row(
            InlineKeyboardButton(text="🎁 Пробная подписка", callback_data="trial_subscription")
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


def support_kb(support_link: str) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопкой поддержки и возвратом на главную.
    
    Args:
        support_link: Ссылка на поддержку
    """
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="💬 Support", url=support_link)
    )
    
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def buy_key_kb(
    crypto_url: str = None,
    stars_enabled: bool = False,
    cards_enabled: bool = False,
    yookassa_qr_enabled: bool = False,
    order_id: str = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура для страницы «Купить подписку».

    Args:
        crypto_url: URL для оплаты криптой (если настроен)
        stars_enabled: Показывать ли кнопку оплаты Stars
        cards_enabled: Показывать ли кнопку оплаты картой ЮКасса
        yookassa_qr_enabled: Показывать ли кнопку QR-оплаты через ЮКассу
        order_id: ID созданного ордера (для оптимизации Stars/Cards)
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

    # Карты (Telegram Payments) — переход к выбору тарифа
    if cards_enabled:
        cb_data = f"pay_cards:{order_id}" if order_id else "pay_cards"
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить картой", callback_data=cb_data)
        )

    # QR ЮКасса — переход к выбору тарифа
    if yookassa_qr_enabled:
        builder.row(
            InlineKeyboardButton(text="📱 QR-оплата (Карта/СБП)", callback_data="pay_qr")
        )

    # Кнопка «На главную» — последний ряд
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )

    return builder.as_markup()


def tariff_select_kb(tariffs: list, back_callback: str = "buy_key", order_id: str = None, is_cards: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для оплаты Stars или Картами.
    
    Args:
        tariffs: Список тарифов из БД
        back_callback: Callback для кнопки «Назад»
        order_id: ID существующего ордера (для оптимизации)
        is_cards: True если выбор тарифа для оплаты картой
    """
    builder = InlineKeyboardBuilder()
    
    for tariff in tariffs:
        if is_cards:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 1:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "cards_pay"
        else:
            price_display = f"{tariff['price_stars']} звёзд"
            prefix = "stars_pay"
            
        cb_data = f"{prefix}:{tariff['id']}:{order_id}" if order_id else f"{prefix}:{tariff['id']}"
        
        builder.row(
            InlineKeyboardButton(
                text=f"{'💳' if is_cards else '⭐'} {tariff['name']} — {price_display}",
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


def renew_tariff_select_kb(tariffs: list, key_id: int, order_id: str = None, is_cards: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для продления ключа (для Stars или Карт).
    
    Args:
        tariffs: Список активных тарифов
        key_id: ID ключа для продления
        order_id: ID ордера (для оптимизации)
        is_cards: True если выбор тарифа для оплаты картой
    """
    builder = InlineKeyboardBuilder()
    
    for tariff in tariffs:
        if is_cards:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 1:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_cards"
        else:
            price_display = f"{tariff['price_stars']} звёзд"
            prefix = "renew_pay_stars"
            
        cb_data = f"{prefix}:{key_id}:{tariff['id']}"
        if order_id:
            cb_data += f":{order_id}"
            
        builder.row(
            InlineKeyboardButton(
                text=f"{'💳' if is_cards else '⭐'} {tariff['name']} — {price_display}",
                callback_data=cb_data
            )
        )
    
    # Последний ряд: назад и на главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_renew:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def renew_payment_method_kb(
    key_id: int,
    crypto_url: str = None,
    stars_enabled: bool = False,
    cards_enabled: bool = False,
    yookassa_qr_enabled: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора способа оплаты для продления (первый шаг).

    Args:
        key_id: ID ключа
        crypto_url: URL для оплаты криптой (с placeholder тарифом)
        stars_enabled: Доступна ли оплата Stars
        cards_enabled: Доступна ли оплата Картами
        yookassa_qr_enabled: Доступна ли QR-оплата через ЮКассу
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

    # Карты — переход к выбору тарифа
    if cards_enabled:
        builder.row(
            InlineKeyboardButton(
                text="💳 Оплатить картой",
                callback_data=f"renew_cards_tariff:{key_id}"
            )
        )

    # QR ЮКасса— переход к выбору тарифа
    if yookassa_qr_enabled:
        builder.row(
            InlineKeyboardButton(
                text="📱 QR-оплата (Карта/СБП)",
                callback_data=f"renew_qr_tariff:{key_id}"
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

def key_issued_kb() -> InlineKeyboardMarkup:
    """
    Универсальная клавиатура после выдачи или при показе ключа (QR-код).
    
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


def trial_sub_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура экрана «Пробная подписка».

    Две кнопки:
    - Активировать (trial_activate)
    - На главную (start)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Активировать", callback_data="trial_activate")
    )
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


# ============================================================================
# QR-ОПЛАТА ЮКАССА (direct API)
# ============================================================================

def yookassa_qr_kb(order_id: str, back_callback: str = "buy_key") -> InlineKeyboardMarkup:
    """
    Клавиатура страницы QR-оплаты ЮКассы.

    Args:
        order_id: Наш внутренний order_id
        back_callback: Каллбэк для кнопки «Назад»
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_yookassa_qr:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


def renew_yookassa_qr_tariff_kb(tariffs: list, key_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для QR-оплаты при продлении ключа.

    Args:
        tariffs: Список активных тарифов
        key_id: ID ключа для продления
    """
    builder = InlineKeyboardBuilder()

    for tariff in tariffs:
        price_rub = tariff.get('price_rub')
        if price_rub is None or price_rub <= 0:
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"📱 {tariff['name']} — {price_rub} ₽",
                callback_data=f"renew_pay_qr:{key_id}:{tariff['id']}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_renew:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


def qr_tariff_select_kb(tariffs: list) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для QR-оплаты нового ключа.

    Args:
        tariffs: Список активных тарифов
    """
    builder = InlineKeyboardBuilder()

    for tariff in tariffs:
        price_rub = tariff.get('price_rub')
        if price_rub is None or price_rub <= 0:
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"📱 {tariff['name']} — {price_rub} ₽",
                callback_data=f"qr_pay:{tariff['id']}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_key"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()
