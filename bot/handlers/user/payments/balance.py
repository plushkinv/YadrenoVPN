import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS
from bot.handlers.user.payments.base import _format_price_compact, _is_cards_via_yookassa_direct

logger = logging.getLogger(__name__)

router = Router()

async def _show_balance_payment_screen(callback: CallbackQuery, state: FSMContext, tariff_id: int, user_internal_id: int, key_id: int=None):
    """
    Показать экран оплаты с учётом баланса по ТЗ.
    
    Вызывается по кнопке «💎 Использовать баланс».
    
    Расчёт:
        balance_to_deduct = min(balance, price)
        remaining_cents = price - balance_to_deduct
    
    Сохраняет в FSM state: balance_to_deduct, tariff_price_cents, tariff_id, key_id
    """
    from database.requests import get_tariff_by_id, get_user_balance, is_cards_enabled, is_yookassa_qr_configured
    from bot.keyboards.user import balance_payment_kb
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if tariff_price_cents <= 0:
        await callback.answer('❌ Ошибка: цена тарифа не задана', show_alert=True)
        return
    balance_cents = get_user_balance(user_internal_id)
    balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = max(0, tariff_price_cents - balance_to_deduct)
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id)
    price_str = _format_price_compact(tariff_price_cents)
    balance_str = _format_price_compact(balance_cents)
    deduct_str = _format_price_compact(balance_to_deduct)
    remaining_str = _format_price_compact(remaining_cents)
    text = f"💳 <b>Оплата тарифа «{escape_html(tariff['name'])}»</b>\n\n💰 Сумма: {price_str}\n💎 Ваш баланс: {balance_str}\n\n✅ С баланса будет списано: {deduct_str}\n💳 К оплате: {remaining_str}"
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    cards_via_yookassa_direct = _is_cards_via_yookassa_direct()
    available_methods = []
    if yookassa_qr_enabled:
        available_methods.append('qr')
    if cards_enabled:
        if cards_via_yookassa_direct:
            available_methods.append('card')
        elif remaining_cents >= 10000:
            available_methods.append('card')
    if remaining_cents > 0 and (not available_methods):
        text += '\n\n💡 <b>Для доплаты этой суммы нет подходящего способа оплаты.</b>\nПоднакопите ещё немного на реферальном балансе\nили оплатите тариф без использования баланса.'
    await safe_edit_or_send(callback.message, text, reply_markup=balance_payment_kb(tariff_id=tariff_id, key_id=key_id, balance_cents=balance_cents, tariff_price_cents=tariff_price_cents, balance_to_deduct=balance_to_deduct, remaining_cents=remaining_cents, cards_enabled=cards_enabled, yookassa_qr_enabled=yookassa_qr_enabled, cards_via_yookassa_direct=cards_via_yookassa_direct))
    await callback.answer()

@router.callback_query(F.data == 'pay_use_balance')
async def pay_use_balance_buy_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа для оплаты с баланса (новый ключ)."""
    from database.requests import get_all_tariffs, get_user_internal_id, is_referral_enabled, get_referral_reward_type, get_user_balance
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer('❌ Оплата с баланса недоступна', show_alert=True)
        return
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
        return
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await safe_edit_or_send(callback.message, '💎 <b>Оплата с баланса</b>\n\n😔 Нет доступных тарифов с ценой в рублях.', reply_markup=home_only_kb())
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, f'💎 <b>Оплата с баланса</b>\n\nВаш баланс: <b>{_format_price_compact(balance_cents)}</b>\n\nВыберите тариф:', reply_markup=tariff_select_kb(rub_tariffs, back_callback='buy_key', is_balance=True))
    await callback.answer()

@router.callback_query(F.data.startswith('pay_use_balance:'))
async def pay_use_balance_renew_handler(callback: CallbackQuery, state: FSMContext):
    """
    Обработка кнопки «Использовать баланс» для продления.
    Callback: pay_use_balance:{key_id}
    """
    from database.requests import get_user_internal_id, get_key_details_for_user, is_referral_enabled, get_referral_reward_type, get_user_balance, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer('❌ Оплата с баланса недоступна', show_alert=True)
        return
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
        return
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await safe_edit_or_send(callback.message, '💎 <b>Оплата с баланса</b>\n\n😔 Нет доступных тарифов с ценой в рублях.', reply_markup=home_only_kb())
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, f"💎 <b>Оплата с баланса</b>\n\n🔑 Ключ: <b>{escape_html(key['display_name'])}</b>\nВаш баланс: <b>{_format_price_compact(balance_cents)}</b>\n\nВыберите тариф:", reply_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_balance=True))
    await callback.answer()

@router.callback_query(F.data.startswith('balance_pay:'))
async def balance_pay_handler(callback: CallbackQuery, state: FSMContext):
    """
    Показ экрана оплаты с балансом после выбора тарифа.
    Callback: balance_pay:{tariff_id} или balance_pay:{tariff_id}:{key_id}
    """
    from database.requests import get_user_internal_id, get_tariff_by_id
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    await _show_balance_payment_screen(callback, state, tariff_id, user_id, key_id=key_id)

@router.callback_query(F.data.startswith('pay_with_balance:'))
async def pay_with_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Полная оплата с баланса (когда remaining_cents == 0).
    Атомарная операция: списать + выдать ключ.
    
    При оплате балансом реферальные вознаграждения НЕ начисляются.
    """
    from database.requests import get_user_internal_id, get_user_balance, deduct_from_balance, get_tariff_by_id, get_or_create_user, create_initial_vpn_key
    from bot.services.user_locks import user_locks
    from bot.services.key_lifecycle import renew_key_access
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    telegram_id = callback.from_user.id
    (user, _) = get_or_create_user(
        telegram_id,
        callback.from_user.username,
        first_name=getattr(callback.from_user, 'first_name', None),
        last_name=getattr(callback.from_user, 'last_name', None),
    )
    user_internal_id = user['id']
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    days = tariff['duration_days']
    async with user_locks[user_internal_id]:
        current_balance = get_user_balance(user_internal_id)
        if current_balance < tariff_price_cents:
            await callback.answer('❌ Недостаточно средств на балансе', show_alert=True)
            return
        actual_deduct = min(current_balance, tariff_price_cents)
        deduct_from_balance(user_internal_id, actual_deduct)
        renew_result = None
        if key_id:
            renew_result = await renew_key_access(
                key_id,
                days,
                reset_traffic=True,
                tariff_id=tariff_id,
            )
            logger.info(f'Ключ {key_id} продлён на {days} дней за баланс {actual_deduct} коп')
        else:
            traffic_limit_bytes = (tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3
            new_key_id = create_initial_vpn_key(user_internal_id, tariff_id, days, traffic_limit=traffic_limit_bytes)
            logger.info(f'Создан черновик ключа {new_key_id} для user {user_internal_id} за баланс {actual_deduct} коп')
    await state.update_data(balance_to_deduct=0)

    def format_price_compact(cents: int) -> str:
        if cents >= 10000:
            return f'{cents // 100} ₽'
        else:
            return f'{cents / 100:.2f} ₽'.replace('.', ',')
    price_str = format_price_compact(actual_deduct)
    
    if key_id:
        # Продление — ключ уже на сервере, просто сообщаем
        text = f'✅ <b>Оплата успешно завершена!</b>\n\nС вашего баланса списано {price_str}\nКлюч продлён на {days} дн.'
        if renew_result and not renew_result['panel_synced']:
            text += '\n\n⚠️ Доступ продлён в БД, но панель синхронизирована не полностью. Если подключение не обновилось сразу, повторите позже или обратитесь в поддержку.'
        # Уведомление администраторов (продление балансом)
        try:
            from bot.services.notifications import notify_admins_payment
            from database.requests import create_pending_order, complete_order as _complete_order, find_order_by_order_id
            (_, bal_order_id) = create_pending_order(user_id=user_internal_id, tariff_id=tariff_id, payment_type='balance', vpn_key_id=key_id)
            _complete_order(bal_order_id)
            bal_order = find_order_by_order_id(bal_order_id)
            if bal_order:
                bal_order['_payment_action'] = 'renewal'
                await notify_admins_payment(callback.bot, bal_order)
        except Exception as notify_err:
            logger.warning(f'Ошибка уведомления о balance-продлении: {notify_err}')
        await safe_edit_or_send(callback.message, text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text='🈴 На главную', callback_data='start')).as_markup())
    else:
        # Новый ключ — нужно настроить (выбор сервера/inbound)
        from bot.handlers.user.payments.base import finalize_payment_ui
        from database.requests import create_pending_order, update_payment_key_id, find_order_by_order_id
        # Создаём ордер для корректной работы finalize_payment_ui
        (_, order_id) = create_pending_order(user_id=user_internal_id, tariff_id=tariff_id, payment_type='balance', vpn_key_id=new_key_id)
        update_payment_key_id(order_id, new_key_id)
        # Уведомление администраторов (новый ключ балансом)
        try:
            from bot.services.notifications import notify_admins_payment
            bal_order = find_order_by_order_id(order_id)
            if bal_order:
                bal_order['_payment_action'] = 'new_key'
                await notify_admins_payment(callback.bot, bal_order)
        except Exception as notify_err:
            logger.warning(f'Ошибка уведомления о balance-покупке: {notify_err}')
        order = {'order_id': order_id, 'vpn_key_id': new_key_id, 'tariff_id': tariff_id}
        await finalize_payment_ui(callback.message, state, f'✅ <b>Оплата успешно завершена!</b>\n\nС вашего баланса списано {price_str}', order, user_id=telegram_id)
    await callback.answer()

@router.callback_query(F.data.startswith('pay_card_balance:'))
async def pay_card_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Частичная оплата: баланс + TG payments.
    
    Берёт данные из FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Создаёт инвойс на remaining_cents (не на полную цену тарифа!)
    """
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, get_user_balance, create_pending_order, get_setting
    from aiogram.exceptions import TelegramBadRequest
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer('❌ Провайдер платежей не настроен', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Ошибка пользователя', show_alert=True)
        return
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = tariff_price_cents - balance_to_deduct
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id, remaining_cents=remaining_cents)
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=key_id)
    price_rub = remaining_cents / 100
    price_kopecks = remaining_cents
    
    import json
    provider_data = {
        "receipt": {
            "customer": {
                "email": f"user_{order_id}@t.me"
            },
            "items": [
                {
                    "description": f"Доплата за «{tariff['name']}»",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{price_rub:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",
                    "payment_subject": "service"
                }
            ]
        }
    }

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        back_cb = f'key_renew:{key_id}' if key_id else 'buy_key'
        await callback.message.answer_invoice(title=bot_name, description=f"Оплата тарифа «{tariff['name']}» ({tariff['duration_days']} дн.).", payload=f'vpn_key:{order_id}', provider_token=provider_token, currency='RUB', prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)], provider_data=json.dumps(provider_data), reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text=f'💳 Оплатить {price_rub:.2f} ₽', pay=True)).row(InlineKeyboardButton(text='❌ Отмена', callback_data=back_cb)).as_markup())
    except TelegramBadRequest as e:
        if 'CURRENCY_TOTAL_AMOUNT_INVALID' in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма. Тариф: ID {tariff['id']}")
            await callback.answer('❌ Ошибка платежной системы. Сумма тарифа меньше допустимого лимита.', show_alert=True)
            return
        logger.exception('Ошибка при отправке инвойса картой.')
        raise e
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith('pay_qr_balance:'))
async def pay_qr_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Частичная оплата: баланс + ЮКасса.
    
    Берёт данные из FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Создаёт инвойс на remaining_cents / 100 рублей (ЮKassa принимает рубли)
    """
    from database.requests import get_tariff_by_id, get_user_internal_id, get_user_balance, create_pending_order, save_yookassa_payment_id
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from aiogram.types import BufferedInputFile
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    parts = callback.data.split(':')
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    if not tariff_id:
        await callback.answer('❌ Ошибка: тариф не определён', show_alert=True)
        return
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = tariff_price_cents - balance_to_deduct
    remaining_rub = remaining_cents / 100
    await state.update_data(balance_to_deduct=balance_to_deduct, tariff_price_cents=tariff_price_cents, tariff_id=tariff_id, key_id=key_id, remaining_cents=remaining_cents)
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='yookassa_qr', vpn_key_id=key_id)
    await safe_edit_or_send(callback.message, '⏳ Создаём оплату через ЮКассу...')
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(amount_rub=remaining_rub, order_id=order_id, description=description, bot_name=bot_name)
        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])
        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await safe_edit_or_send(callback.message, '❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.', reply_markup=home_only_kb())
            return
        from bot.handlers.user.payments.base import format_qr_payment_text
        text = format_qr_payment_text(
            title='📱 <b>ЮКасса</b>',
            tariff_name=escape_html(tariff['name']),
            price_str=f"{remaining_rub:.2f} ₽",
            days=tariff['duration_days'],
            qr_url=qr_url,
        )
        photo = BufferedInputFile(qr_image_data, filename='qr.png')
        back_cb = f'key_renew:{key_id}' if key_id else 'buy_key'
        await safe_edit_or_send(callback.message, text, photo=photo, reply_markup=yookassa_qr_kb(order_id, back_callback=back_cb, qr_url=qr_url), force_new=True)
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания QR ЮКасса: {e}')
        await safe_edit_or_send(callback.message, f'❌ <b>Ошибка ЮКассы</b>\n\n<i>{escape_html(str(e))}</i>\n\nПопробуйте другой способ оплаты.', reply_markup=home_only_kb())
    await callback.answer()
