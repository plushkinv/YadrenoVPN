"""
Сервис уведомлений администраторов.

Отправляет уведомления об оплатах всем админам,
если включена настройка payment_notifications_enabled.
"""
import logging
from typing import Optional, Dict, Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_IDS
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)

# Маппинг payment_type → человеко-понятное название
PAYMENT_TYPE_LABELS: Dict[str, str] = {
    'stars': '⭐ Telegram Stars',
    'crypto': '💰 Крипто (USDT)',
    'cards': '💳 Карта (TG Payments)',
    'yookassa_qr': '📱 ЮКасса (QR/СБП)',
    'wata': '🌊 WATA',
    'platega': '💸 Platega (СБП)',
    'cardlink': '🔗 Cardlink',
    'balance': '💎 Баланс',
    'trial': '🎁 Пробная подписка',
    'demo': '🧪 Демо',
}


def _format_payment_amount(order: Dict[str, Any]) -> str:
    """
    Форматирует сумму платежа в зависимости от типа оплаты.

    Args:
        order: Словарь ордера с данными тарифа

    Returns:
        Отформатированная строка суммы
    """
    payment_type = order.get('payment_type', '')

    if payment_type == 'crypto':
        cents = order.get('amount_cents', 0) or 0
        usd = cents / 100
        usd_str = f'{usd:g}'.replace('.', ',')
        return f'${usd_str} USDT'

    if payment_type == 'stars':
        stars = order.get('amount_stars', 0) or 0
        return f'{stars} ⭐'

    if payment_type == 'trial':
        return 'Бесплатно'

    # Для рублёвых методов (cards, yookassa_qr, wata, platega, cardlink, balance, demo)
    price_rub = order.get('price_rub', 0) or 0
    if price_rub > 0:
        price_str = f'{price_rub:g}'.replace('.', ',')
        return f'{price_str} ₽'

    return '—'


def _get_action_text(order: Dict[str, Any]) -> str:
    """
    Определяет тип действия: новый ключ, продление, пробная.

    Args:
        order: Словарь ордера

    Returns:
        Текст действия
    """
    payment_type = order.get('payment_type', '')

    if payment_type == 'trial':
        return '🎁 Пробная подписка'

    # Если vpn_key_id существовал ДО обработки (ключ уже был) → продление
    # Если vpn_key_id был NULL → новый ключ
    vpn_key_id = order.get('vpn_key_id')
    if vpn_key_id:
        return '🔄 Продление'
    return '🆕 Новый ключ'


def _format_user_name(user: Optional[Dict[str, Any]]) -> str:
    """Формирует имя пользователя для плейсхолдера %имя%."""
    if not user:
        return 'пользователь'

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
    return f"ID {telegram_id}" if telegram_id else 'пользователь'


def _format_user_login(user: Optional[Dict[str, Any]]) -> str:
    """Формирует логин пользователя для плейсхолдера %логин%."""
    if user and user.get('username'):
        return f"@{user['username']}"
    return 'не указан'


def _format_rub_cents(cents: int) -> str:
    """Форматирует копейки в рубли без лишних нулей."""
    rub = (cents or 0) / 100
    rub_str = f'{rub:g}'.replace('.', ',')
    return f'{rub_str} ₽'


def _format_referral_purchase_amount(order: Dict[str, Any], event: Dict[str, Any]) -> str:
    """Форматирует сумму покупки реферала по фактическому типу оплаты."""
    payment_type = event.get('payment_type') or order.get('payment_type', '')
    amount_raw = event.get('amount_raw') or 0

    if payment_type == 'crypto':
        usd = amount_raw / 100
        usd_str = f'{usd:g}'.replace('.', ',')
        return f'${usd_str} USDT'

    if payment_type == 'stars':
        return f'{amount_raw} ⭐'

    if amount_raw:
        return _format_rub_cents(amount_raw)

    price_rub = order.get('price_rub', 0) or 0
    return f'{price_rub:g}'.replace('.', ',') + ' ₽' if price_rub else '—'


def _format_referral_reward(event: Dict[str, Any]) -> str:
    """Форматирует начисленный бонус рефовода."""
    if event.get('reward_type') == 'balance':
        return _format_rub_cents(event.get('reward_cents', 0) or 0)
    return f"{event.get('reward_days', 0) or 0} дн."


def _apply_placeholders(template: str, replacements: Dict[str, str]) -> str:
    """Подставляет уже экранированные HTML-значения в шаблон."""
    text = template
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


async def notify_referrers_new_referral(bot: Bot, referral_id: int) -> None:
    """
    Отправляет рефоводам скрытое уведомление о новом реферале.

    Уровни берутся из referral_notification_levels. Ошибки отправки не
    прерывают регистрацию пользователя.
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
                    replacements = {
                        '%имя%': escape_html(_format_user_name(referral_user)),
                        '%логин%': escape_html(_format_user_login(referral_user)),
                        '%telegram_id%': escape_html(str(referral_user.get('telegram_id') or '')),
                        '%уровень%': escape_html(str(level)),
                    }
                    text = _apply_placeholders(template, replacements)
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
    Отправляет рефоводам скрытые уведомления о покупке реферала.

    Вызывается после успешного внешнего платежа и реферального расчёта.
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
                order.setdefault('price_rub', tariff.get('price_rub', 0) or 0)

        for event in referral_events:
            level = event.get('level')
            if level not in enabled_levels:
                continue

            referrer = get_user_by_id(event.get('referrer_id'))
            if not referrer or not referrer.get('telegram_id'):
                continue

            replacements = {
                '%имя%': escape_html(_format_user_name(payer)),
                '%логин%': escape_html(_format_user_login(payer)),
                '%telegram_id%': escape_html(str((payer or {}).get('telegram_id') or '')),
                '%уровень%': escape_html(str(level)),
                '%тариф%': escape_html(str(tariff_name)),
                '%сумма%': escape_html(_format_referral_purchase_amount(order, event)),
                '%дней%': escape_html(f"{event.get('period_days', 0) or 0} дн."),
                '%вознаграждение%': escape_html(_format_referral_reward(event)),
            }
            text = _apply_placeholders(template, replacements)

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
    Отправляет уведомление об оплате всем администраторам.

    Проверяет настройку payment_notifications_enabled.
    Ошибки подавляются — не ломают основной flow.

    Args:
        bot: Экземпляр aiogram Bot
        order: Словарь ордера (из find_order_by_order_id или аналогичный)
    """
    try:
        from database.requests import get_setting, get_user_by_id, get_vpn_key_by_id

        # Проверяем, включены ли уведомления
        if get_setting('payment_notifications_enabled', '0') != '1':
            return

        # Данные пользователя
        user_id_internal = order.get('user_id')
        telegram_id = None
        username = None

        if user_id_internal:
            user = get_user_by_id(user_id_internal)
            if user:
                telegram_id = user.get('telegram_id')
                username = user.get('username')

        # Данные тарифа
        tariff_name = order.get('tariff_name', '—')

        # Подтягиваем price_rub из тарифа (в ордере нет этого поля)
        tariff_id = order.get('tariff_id')
        if tariff_id:
            from database.requests import get_tariff_by_id
            tariff = get_tariff_by_id(tariff_id)
            if tariff:
                order['price_rub'] = tariff.get('price_rub', 0)

        # Данные сервера (из ключа, если привязан)
        server_name = 'Не выбран'
        vpn_key_id = order.get('vpn_key_id')
        if vpn_key_id:
            key = get_vpn_key_by_id(vpn_key_id)
            if key and key.get('server_name'):
                server_name = key['server_name']

        # Тип оплаты
        payment_type = order.get('payment_type', '—')
        payment_label = PAYMENT_TYPE_LABELS.get(payment_type, payment_type)

        # Сумма
        amount_str = _format_payment_amount(order)

        # Действие
        action_str = _get_action_text(order)

        # Заголовок — зависит от действия
        payment_type_for_header = order.get('payment_type', '')
        vpn_key_id_for_header = order.get('vpn_key_id')
        if payment_type_for_header == 'trial':
            header = '🎁 <b>Пробная подписка</b>'
        elif vpn_key_id_for_header:
            header = '🔄 <b>Продление</b>'
        else:
            header = '💰 <b>Новая покупка</b>'

        # Формируем текст
        lines = [header + '\n']

        # Пользователь — ссылка tg://user
        if telegram_id:
            user_link = f'<a href="tg://user?id={telegram_id}">{telegram_id}</a>'
            if username:
                user_link += f' (@{escape_html(username)})'
            lines.append(f'👤 Пользователь: {user_link}')
        else:
            lines.append(f'👤 Пользователь: ID {user_id_internal or "?"}')

        # Хост — показываем только если сервер уже выбран (при продлении)
        if server_name != 'Не выбран':
            lines.append(f'🌐 Хост: {escape_html(server_name)}')
        lines.append(f'🎫 Тариф: {escape_html(tariff_name)}')
        lines.append(f'💳 Метод: {payment_label}')
        lines.append(f'💵 Сумма: {amount_str}')

        text = '\n'.join(lines)

        # Кнопка для перехода в карточку пользователя в админке
        reply_markup = None
        if telegram_id:
            btn_text = f'👤 @{username}' if username else f'👤 {telegram_id}'
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=btn_text, callback_data=f'admin_user_view:{telegram_id}')
            ]])

        # Отправляем всем админам
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, parse_mode='HTML', reply_markup=reply_markup)
            except Exception as e:
                logger.warning(f'Не удалось отправить уведомление админу {admin_id}: {e}')

    except Exception as e:
        logger.error(f'Ошибка отправки уведомления об оплате: {e}')
