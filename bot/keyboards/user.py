"""
Клавиатуры для пользовательской части бота.

Inline-клавиатуры для обычных пользователей.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(is_admin: bool = False, show_trial: bool = False, show_referral: bool = False) -> InlineKeyboardMarkup:
    """
    Главное меню пользователя.
    
    Args:
        is_admin: Показывать ли кнопку админ-панели
        show_trial: Показывать ли кнопку «Пробная подписка»
        show_referral: Показывать ли кнопку «Реферальная система»
    """
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"),
        InlineKeyboardButton(text="💳 Купить ключ", callback_data="buy_key")
    )
    
    if show_trial:
        builder.row(
            InlineKeyboardButton(text="🎁 Пробная подписка", callback_data="trial_subscription")
        )
    
    if show_referral:
        builder.row(
            InlineKeyboardButton(text="🔗 Реферальная ссылка", callback_data="referral_system"),
            InlineKeyboardButton(text="❓ Справка", callback_data="help")
        )
    else:
        builder.row(
            InlineKeyboardButton(text="❓ Справка", callback_data="help")
        )
    
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")
        )
    
    return builder.as_markup()



def help_kb(
    news_link: str, 
    support_link: str, 
    news_hidden: bool = False, 
    support_hidden: bool = False,
    news_name: str = "Новости",
    support_name: str = "Поддержка"
) -> InlineKeyboardMarkup:
    """
    Клавиатура справки с внешними ссылками.
    
    Args:
        news_link: Ссылка на канал новостей
        support_link: Ссылка на чат поддержки
        news_hidden: Скрыта ли кнопка новостей
        support_hidden: Скрыта ли кнопка поддержки
        news_name: Название кнопки новостей
        support_name: Название кнопки поддержки
    """
    builder = InlineKeyboardBuilder()
    
    # Формируем ряд с кнопками-ссылками (только нескрытые)
    visible_buttons = []
    if not news_hidden:
        visible_buttons.append(InlineKeyboardButton(text=f"📢 {news_name}", url=news_link))
    if not support_hidden:
        visible_buttons.append(InlineKeyboardButton(text=f"💬 {support_name}", url=support_link))
    
    if visible_buttons:
        builder.row(*visible_buttons)
    
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
    crypto_configured: bool = False,
    stars_enabled: bool = False,
    cards_enabled: bool = False,
    yookassa_qr_enabled: bool = False,
    order_id: str = None,
    show_balance_button: bool = False,
    demo_enabled: bool = False,
    wata_enabled: bool = False,
    platega_enabled: bool = False,
    cardlink_enabled: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура для страницы «Купить ключ».

    Args:
        crypto_configured: Настроена ли крипто-оплата
        stars_enabled: Показывать ли кнопку оплаты Stars
        cards_enabled: Показывать ли кнопку оплаты картой ЮКасса
        yookassa_qr_enabled: Показывать ли кнопку QR-оплаты через ЮКассу
        order_id: ID созданного ордера (для оптимизации Stars/Cards)
        show_balance_button: Показывать ли кнопку «Использовать баланс»
        wata_enabled: Показывать ли кнопку оплаты WATA (Карта/СБП)
        platega_enabled: Показывать ли кнопку оплаты Platega (СБП)
        cardlink_enabled: Показывать ли кнопку оплаты Cardlink (Карта/СБП)
    """
    builder = InlineKeyboardBuilder()

    # Кнопки оплаты (показываем только включённые методы)
    # USDT
    if crypto_configured:
        cb_data = f"pay_crypto:{order_id}" if order_id else "pay_crypto"
        builder.row(InlineKeyboardButton(text="🪙 Оплатить USDT", callback_data=cb_data))

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
            InlineKeyboardButton(text="💳 TG payments", callback_data=cb_data)
        )

    # QR ЮКасса — переход к выбору тарифа
    if yookassa_qr_enabled:
        builder.row(
            InlineKeyboardButton(text="📱 ЮКасса (QR/СБП)", callback_data="pay_qr")
        )

    # WATA — переход к выбору тарифа
    if wata_enabled:
        builder.row(
            InlineKeyboardButton(text="🌊 WATA (Карта/СБП)", callback_data="pay_wata")
        )

    # Platega — переход к выбору тарифа
    if platega_enabled:
        builder.row(
            InlineKeyboardButton(text="💸 Platega (СБП)", callback_data="pay_platega")
        )

    # Cardlink — переход к выбору тарифа
    if cardlink_enabled:
        builder.row(
            InlineKeyboardButton(text="🔗 Cardlink (Карта/СБП)", callback_data="pay_cardlink")
        )

    # Демо оплата (РФ) — переход к выбору тарифа
    if demo_enabled:
        cb_data = f"demo_tariffs:{order_id}" if order_id else "demo_tariffs"
        builder.row(
            InlineKeyboardButton(text="🏦 Демо оплата (РФ карта)", callback_data=cb_data)
        )

    # Кнопка «Использовать баланс» — только при выполнении всех трёх условий
    # (is_referral_enabled + reward_type='balance' + personal_balance > 0)
    # На этом экране больше ничего про баланс не показывать
    if show_balance_button:
        builder.row(
            InlineKeyboardButton(text="💎 Использовать баланс", callback_data="pay_use_balance")
        )

    # Кнопка «На главную» — последний ряд
    builder.row(
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )

    return builder.as_markup()


def balance_payment_kb(
    tariff_id: int,
    key_id: int = None,
    balance_cents: int = 0,
    tariff_price_cents: int = 0,
    balance_to_deduct: int = 0,
    remaining_cents: int = 0,
    cards_enabled: bool = False,
    yookassa_qr_enabled: bool = False,
    cards_via_yookassa_direct: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура оплаты с учётом баланса.
    
    Показывается когда referral_reward_type='balance' и personal_balance > 0.
    
    ВАЖНО: Только рублёвые методы доплаты (Cards/QR), без Stars/Crypto!
    
    Логика минимальных сумм:
    - QR (ЮKassa напрямую): минимум 1 ₽ — всегда доступен
    - Card через Telegram Payments: минимум ~100 ₽ (10000 копеек)
    - Card через ЮKassa напрямую (webhook): минимум 1 ₽
    
    Args:
        tariff_id: ID выбранного тарифа
        key_id: ID ключа при продлении (None для нового ключа)
        balance_cents: Баланс пользователя в копейках
        tariff_price_cents: Цена тарифа в копейках
        balance_to_deduct: Сколько будет списано с баланса
        remaining_cents: Сколько нужно доплатить
        cards_enabled: Доступна ли оплата Картами
        yookassa_qr_enabled: Доступна ли QR-оплата
        cards_via_yookassa_direct: True если карты через ЮKassa напрямую (минимум 1₽),
                                   False если через Telegram Payments (минимум ~100₽)
    """
    builder = InlineKeyboardBuilder()
    
    can_pay_full = remaining_cents == 0
    
    if can_pay_full:
        suffix = f":{tariff_id}:{key_id}" if key_id else f":{tariff_id}"
        builder.row(
            InlineKeyboardButton(
                text="💎 Оплатить балансом",
                callback_data=f"pay_with_balance{suffix}"
            )
        )
    else:
        available_methods = []
        
        if yookassa_qr_enabled:
            available_methods.append('qr')
        
        if cards_enabled:
            if cards_via_yookassa_direct:
                available_methods.append('card')
            elif remaining_cents >= 10000:
                available_methods.append('card')
        
        if 'card' in available_methods:
            builder.row(
                InlineKeyboardButton(
                    text="💳 Доплатить картой",
                    callback_data=f"pay_card_balance:{tariff_id}:{key_id if key_id else '0'}"
                )
            )
        
        if 'qr' in available_methods:
            builder.row(
                InlineKeyboardButton(
                    text="📱 Доплатить по QR (СБП)",
                    callback_data=f"pay_qr_balance:{tariff_id}:{key_id if key_id else '0'}"
                )
            )
    
    back_cb = f"key_renew:{key_id}" if key_id else "buy_key"
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)
    )
    
    return builder.as_markup()


def tariff_select_kb(tariffs: list, back_callback: str = "buy_key", order_id: str = None, is_cards: bool = False, is_crypto: bool = False, is_balance: bool = False, is_qr: bool = False, groups_data: list = None, is_demo: bool = False, is_wata: bool = False, is_platega: bool = False, is_cardlink: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для оплаты Stars, Картами, Криптой или Балансом.

    Args:
        tariffs: Список тарифов из БД (используется только если groups_data=None)
        back_callback: Callback для кнопки «Назад»
        order_id: ID существующего ордера (для оптимизации)
        is_cards: True если выбор тарифа для оплаты картой
        is_crypto: True если выбор тарифа для оплаты криптой (простой режим)
        is_balance: True если выбор тарифа для оплаты с баланса
        is_qr: True если выбор тарифа для QR-оплаты (ЮКасса)
        is_demo: True если выбор тарифа для демонстрационной РФ оплаты
        is_wata: True если выбор тарифа для оплаты через WATA
        groups_data: Список dict с ключами 'group' и 'tariffs' для группировки.
                     Если None — tariffs отображаются без группировки.
    """
    builder = InlineKeyboardBuilder()
    
    def _add_tariff_buttons(tariff_list):
        """Добавляет кнопки тарифов в builder."""
        for tariff in tariff_list:
            if is_crypto:
                price_usd = tariff['price_cents'] / 100
                price_str = f"{price_usd:g}".replace('.', ',')
                price_display = f"${price_str}"
                prefix = "crypto_pay"
                emoji = '🪙'
            elif is_cards:
                price_rub = tariff.get('price_rub')
                if price_rub is None or price_rub <= 1:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "cards_pay"
                emoji = '💳'
            elif is_demo:
                price_rub = tariff.get('price_rub')
                if price_rub is None or price_rub <= 1:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "demo_pay"
                emoji = '🏦'
            elif is_qr:
                price_rub = tariff.get('price_rub')
                if price_rub is None or price_rub <= 0:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "qr_pay"
                emoji = '📱'
            elif is_wata:
                price_rub = tariff.get('price_rub')
                # WATA минимум 10 ₽
                if price_rub is None or price_rub < 10:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "wata_pay"
                emoji = '🌊'
            elif is_platega:
                price_rub = tariff.get('price_rub')
                # Platega минимум 10 ₽
                if price_rub is None or price_rub < 10:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "platega_pay"
                emoji = '💸'
            elif is_cardlink:
                price_rub = tariff.get('price_rub')
                # Cardlink минимум 10 ₽
                if price_rub is None or price_rub < 10:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "cardlink_pay"
                emoji = '🔗'
            elif is_balance:
                price_rub = tariff.get('price_rub')
                if price_rub is None or price_rub <= 1:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "balance_pay"
                emoji = '💎'
            else:
                price_display = f"{tariff['price_stars']} звёзд"
                prefix = "stars_pay"
                emoji = '⭐'
                
            cb_data = f"{prefix}:{tariff['id']}:{order_id}" if order_id else f"{prefix}:{tariff['id']}"
            
            builder.row(
                InlineKeyboardButton(
                    text=f"{emoji} {tariff['name']} — {price_display}",
                    callback_data=cb_data
                )
            )
    
    if groups_data:
        # Группированный режим: заголовки + тарифы
        for group_item in groups_data:
            group = group_item['group']
            group_tariffs = group_item['tariffs']
            
            if not group_tariffs:
                continue
            
            # Заголовок группы (кнопка-noop)
            builder.row(
                InlineKeyboardButton(
                    text=f"📂⬇ {group['name']}",
                    callback_data="noop"
                )
            )
            _add_tariff_buttons(group_tariffs)
    else:
        # Обычный режим без группировки
        _add_tariff_buttons(tariffs)
    
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


def key_manage_kb(key_id: int, is_unconfigured: bool = False, is_active: bool = True, is_traffic_exhausted: bool = False, has_sub_id: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура управления ключом.

    Args:
        key_id: ID ключа
        is_unconfigured: True, если ключ не настроен (Draft)
        is_active: True, если ключ активен (срок действия не истек)
        is_traffic_exhausted: True, если трафик исчерпан
        has_sub_id: True, если у ключа есть sub_id (показывает «📋 Показать подписку»)
    """
    show_label = "📋 Показать подписку" if has_sub_id else "📋 Показать ключ"
    builder = InlineKeyboardBuilder()

    if not is_active:
        # Для неактивных ключей (даже если не настроен) нет показа и замены, есть удаление
        builder.row(
            InlineKeyboardButton(text="📈 Продлить", callback_data=f"key_renew:{key_id}")
        )
        
        builder.row(
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"key_delete:{key_id}"),
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"key_rename:{key_id}")
        )
    elif is_unconfigured:
        # Для ненастроенного активного ключа предлагаем настройку
        builder.row(
            InlineKeyboardButton(text="⚙️ Настроить", callback_data=f"key_replace:{key_id}"),
            InlineKeyboardButton(text="📈 Продлить", callback_data=f"key_renew:{key_id}")
        )
        builder.row(
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"key_rename:{key_id}")
        )
    elif is_traffic_exhausted:
        # Трафик исчерпан — только продлить и удалить
        builder.row(
            InlineKeyboardButton(text="📈 Продлить", callback_data=f"key_renew:{key_id}")
        )
        builder.row(
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"key_delete:{key_id}"),
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"key_rename:{key_id}")
        )
    else:
        # Стандартные кнопки активного ключа
        builder.row(
            InlineKeyboardButton(text=show_label, callback_data=f"key_show:{key_id}"),
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


def renew_tariff_select_kb(tariffs: list, key_id: int, order_id: str = None, is_cards: bool = False, is_crypto: bool = False, is_balance: bool = False, is_qr: bool = False, is_demo: bool = False, is_wata: bool = False, is_platega: bool = False, is_cardlink: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора тарифа для продления ключа (для Stars, Карт или Баланса).

    Args:
        tariffs: Список активных тарифов
        key_id: ID ключа для продления
        order_id: ID ордера (для оптимизации)
        is_cards: True если выбор тарифа для оплаты картой
        is_crypto: True если выбор тарифа для оплаты криптой (простой режим)
        is_balance: True если выбор тарифа для оплаты с баланса
        is_qr: True если выбор тарифа для QR-оплаты (ЮКасса)
        is_demo: True если выбор тарифа для демонстрационной РФ оплаты
        is_wata: True если выбор тарифа для оплаты WATA
    """
    builder = InlineKeyboardBuilder()
    
    for tariff in tariffs:
        if is_crypto:
            price_usd = tariff['price_cents'] / 100
            price_str = f"{price_usd:g}".replace('.', ',')
            price_display = f"${price_str}"
            prefix = "renew_pay_crypto"
            emoji = '🪙'
        elif is_cards:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 1:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_cards"
            emoji = '💳'
        elif is_qr:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 0:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_qr"
            emoji = '📱'
        elif is_wata:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub < 10:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_wata"
            emoji = '🌊'
        elif is_platega:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub < 10:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_platega"
            emoji = '💸'
        elif is_cardlink:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub < 10:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_pay_cardlink"
            emoji = '🔗'
        elif is_demo:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 1:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "renew_demo_pay"
            emoji = '🏦'
        elif is_balance:
            price_rub = tariff.get('price_rub')
            if price_rub is None or price_rub <= 1:
                continue
            price_display = f"{price_rub} ₽"
            prefix = "balance_pay"
            emoji = '💎'
        else:
            price_display = f"{tariff['price_stars']} звёзд"
            prefix = "renew_pay_stars"
            emoji = '⭐'
            
        if is_balance:
            cb_data = f"{prefix}:{tariff['id']}:{key_id}"
        else:
            cb_data = f"{prefix}:{key_id}:{tariff['id']}"
        if order_id:
            cb_data += f":{order_id}"
            
        builder.row(
            InlineKeyboardButton(
                text=f"{emoji} {tariff['name']} — {price_display}",
                callback_data=cb_data
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_renew:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def renew_payment_method_kb(
    key_id: int,
    crypto_configured: bool = False,
    stars_enabled: bool = False,
    cards_enabled: bool = False,
    yookassa_qr_enabled: bool = False,
    show_balance_button: bool = False,
    demo_enabled: bool = False,
    wata_enabled: bool = False,
    platega_enabled: bool = False,
    cardlink_enabled: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора способа оплаты для продления (первый шаг).

    Args:
        key_id: ID ключа
        crypto_configured: Настроена ли крипто-оплата
        stars_enabled: Доступна ли оплата Stars
        cards_enabled: Доступна ли оплата Картами
        yookassa_qr_enabled: Доступна ли QR-оплата через ЮКассу
        show_balance_button: Показывать ли кнопку «Использовать баланс»
        wata_enabled: Доступна ли оплата через WATA
    """
    builder = InlineKeyboardBuilder()

    # USDT
    if crypto_configured:
        builder.row(
            InlineKeyboardButton(text="🪙 Оплатить USDT", callback_data=f"renew_crypto_tariff:{key_id}")
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
                text="💳 TG payments",
                callback_data=f"renew_cards_tariff:{key_id}"
            )
        )

    # QR ЮКасса— переход к выбору тарифа
    if yookassa_qr_enabled:
        builder.row(
            InlineKeyboardButton(
                text="📱 ЮКасса (QR/СБП)",
                callback_data=f"renew_qr_tariff:{key_id}"
            )
        )

    # WATA — переход к выбору тарифа
    if wata_enabled:
        builder.row(
            InlineKeyboardButton(
                text="🌊 WATA (Карта/СБП)",
                callback_data=f"renew_wata_tariff:{key_id}"
            )
        )

    # Platega — переход к выбору тарифа
    if platega_enabled:
        builder.row(
            InlineKeyboardButton(
                text="💸 Platega (СБП)",
                callback_data=f"renew_platega_tariff:{key_id}"
            )
        )

    # Cardlink — переход к выбору тарифа
    if cardlink_enabled:
        builder.row(
            InlineKeyboardButton(
                text="🔗 Cardlink (Карта/СБП)",
                callback_data=f"renew_cardlink_tariff:{key_id}"
            )
        )

    # Демо оплата (РФ) — переход к выбору тарифа
    if demo_enabled:
        builder.row(
            InlineKeyboardButton(
                text="🏦 Демо оплата (РФ карта)",
                callback_data=f"renew_demo_tariffs:{key_id}"
            )
        )

    # Кнопка «Использовать баланс» — только при выполнении всех трёх условий
    # (is_referral_enabled + reward_type='balance' + personal_balance > 0)
    # На этом экране больше ничего про баланс не показывать
    if show_balance_button:
        builder.row(
            InlineKeyboardButton(text="💎 Использовать баланс", callback_data=f"pay_use_balance:{key_id}")
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

def yookassa_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура страницы QR-оплаты ЮКассы.

    Args:
        order_id: Наш внутренний order_id
        back_callback: Каллбэк для кнопки «Назад»
        qr_url: Ссылка на оплату (URL)
    """
    builder = InlineKeyboardBuilder()
    
    if qr_url:
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить", url=qr_url)
        )
        
    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_yookassa_qr:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


# renew_yookassa_qr_tariff_kb и qr_tariff_select_kb удалены —
# QR-оплата теперь использует общие renew_tariff_select_kb(is_qr=True) и tariff_select_kb(is_qr=True)


# ============================================================================
# WATA-ОПЛАТА (карта/СБП через ссылку)
# ============================================================================

def wata_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура страницы оплаты WATA (карта/СБП).

    Args:
        order_id: Наш внутренний order_id
        back_callback: Каллбэк для кнопки «Назад»
        qr_url: Ссылка на оплату (URL)
    """
    builder = InlineKeyboardBuilder()

    if qr_url:
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить", url=qr_url)
        )

    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_wata:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


# ============================================================================
# PLATEGA-ОПЛАТА (СБП через ссылку)
# ============================================================================

def platega_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура страницы оплаты Platega (СБП).

    Args:
        order_id: Наш внутренний order_id
        back_callback: Каллбэк для кнопки «Назад»
        qr_url: Ссылка на оплату (URL)
    """
    builder = InlineKeyboardBuilder()

    if qr_url:
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить", url=qr_url)
        )

    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_platega:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


# ============================================================================
# CARDLINK-ОПЛАТА (Карта/СБП через ссылку)
# ============================================================================

def cardlink_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура страницы оплаты Cardlink (Карта/СБП).

    Args:
        order_id: Наш внутренний order_id
        back_callback: Каллбэк для кнопки «Назад»
        qr_url: Ссылка на оплату (URL)
    """
    builder = InlineKeyboardBuilder()

    if qr_url:
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить", url=qr_url)
        )

    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_cardlink:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


def referral_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура для раздела реферальной системы."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="start"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()

