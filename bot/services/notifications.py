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
        from database.requests import get_setting, get_vpn_key_by_id

        # Проверяем, включены ли уведомления
        if get_setting('payment_notifications_enabled', '0') != '1':
            return

        # Данные пользователя
        user_id_internal = order.get('user_id')
        telegram_id = None
        username = None

        if user_id_internal:
            # Получаем telegram_id из внутреннего user_id
            from database.connection import get_db
            with get_db() as conn:
                cursor = conn.execute(
                    "SELECT telegram_id, username FROM users WHERE id = ?",
                    (user_id_internal,)
                )
                row = cursor.fetchone()
                if row:
                    telegram_id = row['telegram_id']
                    username = row['username']

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
