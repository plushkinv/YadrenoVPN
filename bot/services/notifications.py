"""
Administrator notification service.

Sends payment notifications to all admins,
if the payment_notifications_enabled setting is enabled.
"""
import logging
from typing import Optional, Dict, Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_IDS
from bot.utils.event_placeholders import render_event_message_text
from bot.utils.text import escape_html
from bot.utils.user_ui_texts import render_duration_days, render_ui_text

logger = logging.getLogger(__name__)

# Mapping payment_type → human-readable name.
# payment_type='cards' - historical internal name for TG payments.
PAYMENT_TYPE_LABELS: Dict[str, str] = {
    'stars': '⭐ Telegram Stars',
    'crypto': '💰 Крипто (USDT)',
    'cryptobot': '💎 CryptoBot',
    'cards': '💳 TG payments',
    'yookassa_qr': '📱 ЮКасса',
    'wata': '🌊 WATA',
    'platega': '💸 Platega',
    'cardlink': '🔗 Cardlink',
    'balance': '💎 Баланс',
    'trial': '🎁 Пробная подписка',
    'demo': '🧪 Демо',
    'promo_free': '🎟 Промокод 100%',
}


def _payment_type_label(payment_type: str) -> str:
    provider = None
    try:
        from bot.utils.payment_provider_registry import get_payment_provider_by_type

        provider = get_payment_provider_by_type(payment_type)
    except Exception:
        provider = None
    if provider is not None:
        return provider.label
    return PAYMENT_TYPE_LABELS.get(payment_type, payment_type)


def _format_payment_amount(order: Dict[str, Any]) -> str:
    """
    Formats the payment amount depending on the payment type.

    Args:
        order: Order dictionary with tariff data

    Returns:
        Formatted Amount String
    """
    payment_type = order.get('payment_type', '')
    if payment_type in ('trial', 'promo_free'):
        return 'Бесплатно'

    from bot.services.money import format_money_minor, parse_major_to_minor

    charge_currency = str(
        order.get('charge_currency') or order.get('base_currency') or 'RUB'
    )
    if order.get('charge_amount') not in {None, ''}:
        try:
            return format_money_minor(
                parse_major_to_minor(order.get('charge_amount'), charge_currency),
                charge_currency,
            )
        except (TypeError, ValueError):
            pass
    return format_money_minor(
        order.get('payable_amount_minor') or 0,
        order.get('base_currency') or 'RUB',
    )


def _get_payment_notification_kind(order: Dict[str, Any]) -> str:
    """
    Returns the operation type for the notification.

    During a new purchase, billing creates a draft key and links it to the order.
    Therefore, for an order that has already been processed, the type cannot be determined only by vpn_key_id.
    """
    payment_type = order.get('payment_type', '')
    if payment_type == 'trial':
        return 'trial'

    purpose_action = {
        'key_purchase': 'new_key',
        'key_renewal': 'renewal',
        'balance_topup': 'balance_topup',
    }.get(str(order.get('purpose') or ''))
    if purpose_action:
        return purpose_action

    return 'unknown'


def _get_action_text(order: Dict[str, Any]) -> str:
    """
    Defines the type of action: new key, renewal, trial.

    Args:
        order: Order dictionary

    Returns:
        Action text
    """
    action = _get_payment_notification_kind(order)
    if action == 'trial':
        return '🎁 Пробная подписка'
    if action == 'renewal':
        return '🔄 Продление'
    if action == 'new_key':
        return '🆕 Новый ключ'
    return '💳 Платёж'


def _format_user_name(user: Optional[Dict[str, Any]]) -> str:
    """Generates a display name for the name placeholder."""
    if not user:
        return '—'

    parts = [
        (user.get('first_name') or '').strip(),
        (user.get('last_name') or '').strip(),
    ]
    full_name = ' '.join(part for part in parts if part)
    if full_name:
        return full_name

    username = user.get('username')
    if username:
        return f"@{username}"

    telegram_id = user.get('telegram_id')
    return f"ID {telegram_id}" if telegram_id else '—'


def _format_user_login(user: Optional[Dict[str, Any]]) -> str:
    """Generates a user login for the login placeholder."""
    if user and user.get('username'):
        return f"@{user['username']}"
    return '—'


def _format_referral_purchase_amount(order: Dict[str, Any], event: Dict[str, Any]) -> str:
    """Formats the referral purchase amount by the actual payment type."""
    from bot.services.money import format_money_minor

    return format_money_minor(
        event.get('amount_base_minor') or 0,
        event.get('base_currency') or order.get('base_currency') or 'RUB',
    )


def _format_referral_reward(event: Dict[str, Any]) -> str:
    """Formats the accrued referral bonus."""
    if event.get('reward_type') == 'balance':
        from bot.services.money import format_money_minor

        return format_money_minor(
            event.get('reward_minor', event.get('reward_cents', 0)) or 0,
            event.get('reward_currency') or 'RUB',
        )
    return render_ui_text('format.days_short', days=event.get('reward_days', 0) or 0)


async def notify_referrers_new_referral(bot: Bot, referral_id: int) -> None:
    """
    Sends a hidden notification to referrals about a new referral.

    The levels are taken from referral_notification_levels. No sending errors
    interrupt user registration.
    """
    try:
        from database.requests import (
            get_user_by_id,
            get_user_referrer,
            get_active_referral_levels,
            get_referral_notification_levels,
            get_referral_new_ref_notification_text,
            is_referral_enabled,
            is_referral_new_ref_notifications_enabled,
        )

        if not is_referral_enabled() or not is_referral_new_ref_notifications_enabled():
            return

        active_levels = {level for level, _ in get_active_referral_levels()}
        enabled_levels = set(get_referral_notification_levels()) & active_levels
        if not enabled_levels:
            return

        referral_user = get_user_by_id(referral_id)
        if not referral_user:
            return

        template = get_referral_new_ref_notification_text()
        current_user_id = referral_id

        for level in (1, 2, 3):
            referrer_id = get_user_referrer(current_user_id)
            if not referrer_id:
                break

            if level in enabled_levels:
                referrer = get_user_by_id(referrer_id)
                if referrer and referrer.get('telegram_id'):
                    context = {
                        'referral_name': _format_user_name(referral_user),
                        'referral_login': _format_user_login(referral_user),
                        'referral_telegram_id': str(referral_user.get('telegram_id') or ''),
                        'referral_level': level,
                    }
                    text = await render_event_message_text(
                        template,
                        'referral_new_ref',
                        bot=bot,
                        telegram_id=int(referrer['telegram_id']),
                        context=context,
                    )
                    try:
                        await bot.send_message(referrer['telegram_id'], text, parse_mode='HTML')
                    except Exception as e:
                        logger.warning(
                            f"Не удалось отправить уведомление о реферале user={referrer_id}: {e}"
                        )

            current_user_id = referrer_id

    except Exception as e:
        logger.error(f'Ошибка отправки уведомления о новом реферале: {e}')


async def notify_referrers_purchase(
    bot: Bot,
    order: Dict[str, Any],
    referral_events: list[Dict[str, Any]],
) -> None:
    """
    Sends hidden notifications to referrals about the referral's purchase.

    Called after a successful external payment and referral settlement.
    """
    try:
        from database.requests import (
            get_tariff_by_id,
            get_user_by_id,
            get_active_referral_levels,
            get_referral_notification_levels,
            get_referral_purchase_notification_text,
            is_referral_purchase_notifications_enabled,
        )

        if not referral_events or not is_referral_purchase_notifications_enabled():
            return

        payment_type = order.get('payment_type', '')
        if payment_type in ('balance', 'trial'):
            return

        active_levels = {level for level, _ in get_active_referral_levels()}
        enabled_levels = set(get_referral_notification_levels()) & active_levels
        if not enabled_levels:
            return

        payer_id = order.get('user_id')
        payer = get_user_by_id(payer_id) if payer_id else None
        template = get_referral_purchase_notification_text()

        tariff_name = order.get('tariff_name') or '—'
        tariff_id = order.get('tariff_id')
        if tariff_id:
            tariff = get_tariff_by_id(tariff_id)
            if tariff:
                tariff_name = tariff.get('name') or tariff_name

        for event in referral_events:
            level = event.get('level')
            if level not in enabled_levels:
                continue

            referrer = get_user_by_id(event.get('referrer_id'))
            if not referrer or not referrer.get('telegram_id'):
                continue

            context = {
                'buyer_name': _format_user_name(payer),
                'buyer_login': _format_user_login(payer),
                'buyer_telegram_id': str((payer or {}).get('telegram_id') or ''),
                'referral_level': level,
                'payment_tariff_name': str(tariff_name),
                'payment_amount_text': _format_referral_purchase_amount(order, event),
                'payment_period_text': render_duration_days(
                    event.get('period_days', 0),
                ),
                'referral_reward_text': _format_referral_reward(event),
            }
            text = await render_event_message_text(
                template,
                'referral_purchase',
                bot=bot,
                telegram_id=int(referrer['telegram_id']),
                context=context,
            )

            try:
                await bot.send_message(referrer['telegram_id'], text, parse_mode='HTML')
            except Exception as e:
                logger.warning(
                    f"Не удалось отправить уведомление о покупке referrer={event.get('referrer_id')}: {e}"
                )

    except Exception as e:
        logger.error(f'Ошибка отправки уведомления о покупке реферала: {e}')


async def notify_admins_payment(bot: Bot, order: Dict[str, Any]) -> None:
    """
    Sends payment notification to all administrators.

    Checks the payment_notifications_enabled setting.
    Errors are suppressed and do not break the main flow.

    Args:
        bot: An instance of aiogram Bot
        order: Order dictionary (from find_order_by_order_id or similar)
    """
    try:
        from database.requests import get_setting, get_user_by_id, get_vpn_key_by_id

        # Checking if notifications are enabled
        if get_setting('payment_notifications_enabled', '0') != '1':
            return

        # User data
        user_id_internal = order.get('user_id')
        telegram_id = None
        username = None

        if user_id_internal:
            user = get_user_by_id(user_id_internal)
            if user:
                telegram_id = user.get('telegram_id')
                username = user.get('username')

        # Tariff data
        tariff_name = order.get('tariff_name', '—')

        # Server data (from the key, if linked)
        server_name = 'Не выбран'
        vpn_key_id = order.get('vpn_key_id')
        if vpn_key_id:
            key = get_vpn_key_by_id(vpn_key_id)
            if key and key.get('server_name'):
                server_name = key['server_name']

        # Payment type
        payment_type = order.get('payment_type', '—')
        payment_label = _payment_type_label(payment_type)

        # Sum
        amount_str = _format_payment_amount(order)

        # Action
        action = _get_payment_notification_kind(order)

        # Title - depends on the action
        if action == 'trial':
            header = '🎁 <b>Пробная подписка</b>'
        elif action == 'renewal':
            header = '🔄 <b>Продление</b>'
        elif action == 'balance_topup':
            header = '💰 <b>Пополнение баланса</b>'
        elif action == 'new_key':
            header = '💰 <b>Новая покупка</b>'
        else:
            header = '💳 <b>Платёж</b>'

        # Forming the text
        lines = [header + '\n']

        # User - link tg://user
        if telegram_id:
            user_link = f'<a href="tg://user?id={telegram_id}">{telegram_id}</a>'
            if username:
                user_link += f' (@{escape_html(username)})'
            lines.append(f'👤 Пользователь: {user_link}')
        else:
            lines.append(f'👤 Пользователь: ID {user_id_internal or "?"}')

        # We show the host only for a real renewal of an existing key.
        if action == 'renewal' and server_name != 'Не выбран':
            lines.append(f'🌐 Хост: {escape_html(server_name)}')
        if action == 'balance_topup':
            from bot.services.money import format_money_minor

            nominal = int(
                order.get('nominal_amount_minor') or 0
            )
            lines.append(
                f"💎 Зачислено: {format_money_minor(nominal, order.get('base_currency') or 'RUB')}"
            )
        else:
            lines.append(f'🎫 Тариф: {escape_html(tariff_name)}')
        lines.append(f'💳 Метод: {payment_label}')
        lines.append(f'💵 Сумма: {amount_str}')
        if order.get('promo_code'):
            lines.append(
                f"🎟 Промокод: {escape_html(str(order.get('promo_code')))} "
                f"(-{int(order.get('discount_percent') or 0)}%)"
            )

        text = '\n'.join(lines)

        # Button to go to the user card in the admin panel
        reply_markup = None
        if telegram_id:
            btn_text = f'👤 @{username}' if username else f'👤 {telegram_id}'
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=btn_text, callback_data=f'admin_user_view:{telegram_id}')
            ]])

        # Sent to all admins
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, parse_mode='HTML', reply_markup=reply_markup)
            except Exception as e:
                logger.warning(f'Не удалось отправить уведомление админу {admin_id}: {e}')

    except Exception as e:
        logger.error(f'Ошибка отправки уведомления об оплате: {e}')
