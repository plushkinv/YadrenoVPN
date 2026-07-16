"""
Keyboards for the user part of the bot.

Inline keyboards for ordinary users.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def payment_auto_complete_kb() -> InlineKeyboardMarkup:
    """Navigation shown after a payment was completed by background polling."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys"))
    builder.row(InlineKeyboardButton(text="🈴 На главную", callback_data="start"))
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
    Payment keyboard taking into account balance.
    
    Shown when referral_reward_type='balance' and personal_balance > 0.
    
    IMPORTANT: Only ruble payment methods (TG payments/YuKassa), without Stars/Crypto!
    
    Minimum amount logic:
    - YuKassa directly: minimum 1 ₽ - always available when the method is enabled
    - TG payments via Telegram Payments: minimum ~100 ₽ (10,000 kopecks)
    - Direct YuKassa script for additional payment: minimum 1 ₽
    
    Args:
        tariff_id: ID of the selected tariff
        key_id: Key ID when renewing (None for new key)
        balance_cents: User balance in kopecks
        tariff_price_cents: Tariff price in kopecks
        balance_to_deduct: How much will be deducted from the balance
        remaining_cents: How much you need to pay
        cards_enabled: Is TG payments available?
        yookassa_qr_enabled: Is Yookassa available?
        cards_via_yookassa_direct: True if the direct YuKassa script is available from 1 ₽,
                                   False if via Telegram Payments (minimum ~100₽)
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
    Keyboard for choosing a tariff for paying with Stars, Cards, Crypto or Balance.

    Args:
        tariffs: List of tariffs from the database (only used if groups_data=None)
        back_callback: Callback for the back button
        order_id: ID of the existing order (for optimization)
        is_cards: True if choosing a tariff for payment by card
        is_crypto: True if choosing a tariff for payment with crypto (simple mode)
        is_balance: True if choosing a tariff for payment from the balance
        is_qr: True if choosing a tariff for QR payment (YuKassa)
        is_demo: True if choosing a tariff for demo RF payment
        is_wata: True if choosing a tariff for payment via WATA
        groups_data: List of dict with keys 'group' and 'tariffs' for grouping.
                     If None, tariffs are displayed without grouping.
    """
    builder = InlineKeyboardBuilder()
    
    def _add_tariff_buttons(tariff_list):
        """Adds rate buttons to builder."""
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
                # WATA minimum 10 ₽
                if price_rub is None or price_rub < 10:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "wata_pay"
                emoji = '🌊'
            elif is_platega:
                price_rub = tariff.get('price_rub')
                # Platega minimum 10 ₽
                if price_rub is None or price_rub < 10:
                    continue
                price_display = f"{price_rub} ₽"
                prefix = "platega_pay"
                emoji = '💸'
            elif is_cardlink:
                price_rub = tariff.get('price_rub')
                # Cardlink minimum 10 ₽
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
        # Grouped mode: titles + tariffs
        for group_item in groups_data:
            group = group_item['group']
            group_tariffs = group_item['tariffs']
            
            if not group_tariffs:
                continue
            
            # Group header (button-noop)
            builder.row(
                InlineKeyboardButton(
                    text=f"📂⬇ {group['name']}",
                    callback_data="noop"
                )
            )
            _add_tariff_buttons(group_tariffs)
    else:
        # Normal mode without grouping
        _add_tariff_buttons(tariffs)
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start")
    )
    
    return builder.as_markup()


def cancel_kb(cancel_callback: str) -> InlineKeyboardMarkup:
    """
    Keyboard with 'Cancel' button.
    
    Args:
        cancel_callback: Callback for the 'Cancel' button
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_callback)
    )
    return builder.as_markup()


def renew_tariff_select_kb(tariffs: list, key_id: int, order_id: str = None, is_cards: bool = False, is_crypto: bool = False, is_balance: bool = False, is_qr: bool = False, is_demo: bool = False, is_wata: bool = False, is_platega: bool = False, is_cardlink: bool = False) -> InlineKeyboardMarkup:
    """
    Keyboard for selecting a tariff for renewing a key (for Stars, Cards or Balance).

    Args:
        tariffs: List of active tariffs
        key_id: ID of the key to renew
        order_id: Order ID (for optimization)
        is_cards: True if choosing a tariff for payment by card
        is_crypto: True if choosing a tariff for payment with crypto (simple mode)
        is_balance: True if choosing a tariff for payment from the balance
        is_qr: True if choosing a tariff for QR payment (YuKassa)
        is_demo: True if choosing a tariff for demo RF payment
        is_wata: True if choosing a tariff for WATA payment
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
# KEY REPLACEMENT
# ============================================================================

def custom_payment_tariff_select_kb(
    tariffs: list,
    provider_id: str,
    *,
    minimum_amount_cents: int = 0,
    back_callback: str = "buy_key",
) -> InlineKeyboardMarkup:
    """Tariff selection keyboard for a custom payment provider."""
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
    """Keyboard for selecting a renewal tariff for a custom payment provider."""
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
    Server selection keyboard for key replacement.
    
    Args:
        servers: List of servers
        key_id: Key ID
    """
    builder = InlineKeyboardBuilder()
    
    for server in servers:
        # We do not show complex details for the user, only the name and status
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
    Protocol selection keyboard for key replacement.
    
    Args:
        inbounds: List of inbounds
        key_id: Key ID
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
    Replacement confirmation keypad.
    
    Args:
        key_id: Key ID
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
# NEW KEY (AFTER PAYMENT)
# ============================================================================

def new_key_server_list_kb(servers: list) -> InlineKeyboardMarkup:
    """
    Server selection keyboard to create a new key.
    
    Args:
        servers: List of servers
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
    Protocol selection keyboard for creating a new key.
    
    Args:
        inbounds: List of inbounds
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
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_server_select") # specialist. callback to return
    )
    
    return builder.as_markup()


# ============================================================================
# SINGLE QR KEYBOARD FOR ALL PAYMENT PROVIDERS
# ============================================================================

def qr_payment_kb(
    order_id: str,
    check_prefix: str,
    back_callback: str = "buy_key",
    qr_url: str = None,
) -> InlineKeyboardMarkup:
    """
    Universal QR payment keyboard for any provider.

    Args:
        order_id: Our internal order_id
        check_prefix: Callback prefix for the “✅ I paid” button
                       (e.g. 'check_yookassa_qr', 'check_wata', 'check_platega', 'check_cardlink')
        back_callback: Callback for the back button
        qr_url: Payment link (URL)
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


# Aliases for backward compatibility (delegated to qr_payment_kb)
def yookassa_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Alias ​​→ qr_payment_kb(check_prefix='check_yookassa_qr')."""
    return qr_payment_kb(order_id, 'check_yookassa_qr', back_callback, qr_url)

def wata_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Alias ​​→ qr_payment_kb(check_prefix='check_wata')."""
    return qr_payment_kb(order_id, 'check_wata', back_callback, qr_url)

def platega_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Alias ​​→ qr_payment_kb(check_prefix='check_platega')."""
    return qr_payment_kb(order_id, 'check_platega', back_callback, qr_url)

def cardlink_qr_kb(order_id: str, back_callback: str = "buy_key", qr_url: str = None) -> InlineKeyboardMarkup:
    """Alias ​​→ qr_payment_kb(check_prefix='check_cardlink')."""
    return qr_payment_kb(order_id, 'check_cardlink', back_callback, qr_url)

