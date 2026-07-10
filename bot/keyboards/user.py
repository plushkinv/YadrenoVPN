"""
Клавиатуры для пользовательской части бота.

Inline-клавиатуры для обычных пользователей.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
    
    ВАЖНО: Только рублёвые методы доплаты (TG payments/ЮКасса), без Stars/Crypto!
    
    Логика минимальных сумм:
    - ЮКасса напрямую: минимум 1 ₽ — всегда доступна при включённом методе
    - TG payments через Telegram Payments: минимум ~100 ₽ (10000 копеек)
    - Прямой сценарий ЮKassa для доплаты: минимум 1 ₽
    
    Args:
        tariff_id: ID выбранного тарифа
        key_id: ID ключа при продлении (None для нового ключа)
        balance_cents: Баланс пользователя в копейках
        tariff_price_cents: Цена тарифа в копейках
        balance_to_deduct: Сколько будет списано с баланса
        remaining_cents: Сколько нужно доплатить
        cards_enabled: Доступна ли оплата TG payments
        yookassa_qr_enabled: Доступна ли ЮКасса
        cards_via_yookassa_direct: True если прямой сценарий ЮKassa доступен от 1 ₽,
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
                    text="💳 Доплатить через TG payments",
                    callback_data=f"pay_card_balance:{tariff_id}:{key_id if key_id else '0'}"
                )
            )
        
        if 'qr' in available_methods:
            builder.row(
                InlineKeyboardButton(
                    text="📱 Доплатить через ЮКассу",
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


# ============================================================================
# ЗАМЕНА КЛЮЧА
# ============================================================================

def custom_payment_tariff_select_kb(
    tariffs: list,
    provider_id: str,
    *,
    minimum_amount_cents: int = 0,
    back_callback: str = "buy_key",
) -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа для кастомного платёжного провайдера."""
    builder = InlineKeyboardBuilder()
    min_rub = minimum_amount_cents / 100

    for tariff in tariffs:
        price_rub = float(tariff.get('price_rub') or 0)
        if price_rub <= 0 or price_rub < min_rub:
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"💳 {tariff['name']} — {_format_rub_button_price(price_rub)}",
                callback_data=f"pet:{provider_id}:{tariff['id']}",
            )
        )

    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start"),
    )
    return builder.as_markup()


def custom_payment_renew_tariff_select_kb(
    tariffs: list,
    provider_id: str,
    key_id: int,
    *,
    minimum_amount_cents: int = 0,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа продления для кастомного платёжного провайдера."""
    builder = InlineKeyboardBuilder()
    min_rub = minimum_amount_cents / 100

    for tariff in tariffs:
        price_rub = float(tariff.get('price_rub') or 0)
        if price_rub <= 0 or price_rub < min_rub:
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"💳 {tariff['name']} — {_format_rub_button_price(price_rub)}",
                callback_data=f"ret:{provider_id}:{key_id}:{tariff['id']}",
            )
        )

    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_renew:{key_id}"),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start"),
    )
    return builder.as_markup()


def _format_rub_button_price(price_rub: float) -> str:
    if float(price_rub).is_integer():
        return f"{int(price_rub)} ₽"
    return f"{price_rub:g} ₽".replace('.', ',')


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


# ============================================================================
# ЕДИНАЯ QR-КЛАВИАТУРА ДЛЯ ВСЕХ ПЛАТЁЖНЫХ ПРОВАЙДЕРОВ
# ============================================================================

def qr_payment_kb(
    order_id: str,
    check_prefix: str,
    back_callback: str = "buy_key",
    qr_url: str = None,
) -> InlineKeyboardMarkup:
    """
    Универсальная клавиатура QR-оплаты для любого провайдера.

    Args:
        order_id: Наш внутренний order_id
        check_prefix: Префикс callback для кнопки «✅ Я оплатил»
                       (напр. 'check_yookassa_qr', 'check_wata', 'check_platega', 'check_cardlink')
        back_callback: Каллбэк для кнопки «Назад»
        qr_url: Ссылка на оплату (URL)
    """
    builder = InlineKeyboardBuilder()

    if qr_url:
        builder.row(
            InlineKeyboardButton(text="💳 Оплатить", url=qr_url)
        )

    builder.row(
        InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"{check_prefix}:{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    return builder.as_markup()


# Алиасы для обратной совместимости (делегируют в qr_payment_kb)
def yookassa_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Алиас → qr_payment_kb(check_prefix='check_yookassa_qr')."""
    return qr_payment_kb(order_id, 'check_yookassa_qr', back_callback, qr_url)

def wata_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Алиас → qr_payment_kb(check_prefix='check_wata')."""
    return qr_payment_kb(order_id, 'check_wata', back_callback, qr_url)

def platega_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Алиас → qr_payment_kb(check_prefix='check_platega')."""
    return qr_payment_kb(order_id, 'check_platega', back_callback, qr_url)

def cardlink_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Алиас → qr_payment_kb(check_prefix='check_cardlink')."""
    return qr_payment_kb(order_id, 'check_cardlink', back_callback, qr_url)

