import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS

logger = logging.getLogger(__name__)
router = Router()

PAYMENT_DEEPLINK_PREFIX = 'pay_'
PAYMENT_DEEPLINK_PROVIDERS = {'yookassa', 'wata', 'platega', 'cardlink'}


def parse_payment_deeplink(start_param: str) -> Optional[dict]:
    """
    Разбирает единый deep-link возврата из платёжной формы.

    Формат: pay_{provider}_{order_id}
    """
    if not start_param or not start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        return None

    payload = start_param[len(PAYMENT_DEEPLINK_PREFIX):]
    provider, separator, order_id = payload.partition('_')
    if not separator or provider not in PAYMENT_DEEPLINK_PROVIDERS or not order_id:
        return None

    return {
        'provider': provider,
        'order_id': order_id,
    }


async def handle_payment_deeplink(
    message: Message,
    state: FSMContext,
    start_param: str,
    user_internal_id: int,
    telegram_id: int,
) -> bool:
    """
    Обрабатывает платёжные deep-link из /start.

    Возвращает True, если параметр относится к платежам и дальнейшая обработка /start не нужна.
    """
    if not start_param:
        return False

    if start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        parsed = parse_payment_deeplink(start_param)
        if not parsed:
            from bot.keyboards.admin import home_only_kb
            await safe_edit_or_send(
                message,
                '⚠️ <b>Платёжная ссылка устарела или повреждена</b>\n\n'
                'Откройте оплату заново из бота и попробуйте ещё раз.',
                reply_markup=home_only_kb(),
                force_new=True,
            )
            return True

        provider = parsed['provider']
        order_id = parsed['order_id']

        if provider == 'yookassa':
            from bot.handlers.user.payments.yookassa import _run_yookassa_check
            await _run_yookassa_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'wata':
            from bot.handlers.user.payments.wata import _run_wata_check
            await _run_wata_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'platega':
            from bot.handlers.user.payments.platega import _run_platega_check
            await _run_platega_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'cardlink':
            from bot.handlers.user.payments.cardlink import _run_cardlink_check
            await _run_cardlink_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        return True

    # Совместимость со старыми ссылками Cardlink из настроек магазина.
    if start_param.startswith('cl_'):
        from database.requests import find_latest_pending_cardlink_order_for_user
        from bot.handlers.user.payments.cardlink import _run_cardlink_check
        from bot.keyboards.admin import home_only_kb

        order = find_latest_pending_cardlink_order_for_user(user_internal_id)
        if not order:
            await safe_edit_or_send(
                message,
                '⚠️ <b>Активная оплата Cardlink не найдена</b>\n\n'
                'Возможно, платёж уже обработан или ещё не создан.\n'
                'Откройте «Купить ключ» и попробуйте снова.',
                reply_markup=home_only_kb(),
                force_new=True,
            )
            return True

        await _run_cardlink_check(
            message, state, order_id=order['order_id'],
            telegram_id=telegram_id, callback=None
        )
        return True

    return False


def _format_price_compact(cents: int) -> str:
    """Форматирование цены в компактном виде."""
    if cents >= 10000:
        return f'{cents // 100} ₽'
    else:
        return f'{cents / 100:.2f} ₽'.replace('.', ',')

def _is_cards_via_yookassa_direct() -> bool:
    """
    Проверяет, используется ли оплата картами через ЮKassa напрямую (webhook).
    
    Returns:
        True если карты через ЮKassa напрямую (минимум 1₽),
        False если через Telegram Payments API (минимум ~100₽)
    """
    from database.requests import get_setting
    return get_setting('cards_via_yookassa_direct', '0') == '1'

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Подтверждение pre-checkout для Telegram Stars."""
    await pre_checkout.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, state: FSMContext):
    """
    Обработка успешной оплаты Stars или Cards.
    
    Делегирует общую post-payment логику в complete_payment_flow().
    """
    from bot.services.billing import complete_payment_flow
    payment = message.successful_payment
    payload = payment.invoice_payload
    currency = payment.currency
    payment_type = 'stars' if currency == 'XTR' else 'cards'
    logger.info(f'Успешная оплата {payment_type}: {payload}, charge_id={payment.telegram_payment_charge_id}')
    
    if payload.startswith('renew:'):
        order_id = payload.split(':')[1]
    elif payload.startswith('vpn_key:'):
        order_id = payload.split(':')[1]
    else:
        order_id = payload
    
    await complete_payment_flow(
        order_id=order_id,
        message=message,
        state=state,
        telegram_id=message.from_user.id,
        payment_type=payment_type,
        referral_amount=payment.total_amount
    )

async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Завершает UI после успешной оплаты.
    Показывает сообщение и либо перекидывает на настройку (draft), либо на главную.
    """
    from bot.keyboards.admin import home_only_kb
    from database.requests import get_key_details_for_user
    import logging
    logger = logging.getLogger(__name__)
    from bot.handlers.user.payments.keys_config import start_new_key_config
    key_id = order.get('vpn_key_id')
    logger.info(f"finalize_payment_ui: Order={order.get('order_id')}, Key={key_id}, User={user_id}")
    is_draft = False
    if key_id:
        key = get_key_details_for_user(key_id, user_id)
        if key:
            logger.info(f"Key details found: ID={key['id']}, ServerID={key.get('server_id')}")
            if not key.get('server_id'):
                is_draft = True
        else:
            logger.warning(f'Key {key_id} not found for user {user_id} via details check!')
    else:
        logger.info('No key_id in order object.')
    logger.info(f'Result: is_draft={is_draft}')
    if is_draft:
        await safe_edit_or_send(message, text, force_new=True)
        await start_new_key_config(message, state, order['order_id'], key_id)
    else:
        from bot.handlers.user.keys import show_key_details
        await show_key_details(telegram_id=user_id, key_id=key_id, message=message, is_callback=False, prepend_text=text)

@router.callback_query(F.data.startswith('renew_invoice_cancel:'))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Отмена инвойса и возврат к выбору способа оплаты."""
    from database.requests import get_key_details_for_user
    from bot.handlers.user.keys import show_renew_payment_page
    parts = callback.data.split(':')
    key_id = int(parts[1])
    telegram_id = callback.from_user.id

    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return

    await show_renew_payment_page(callback, key, key_id, force_new=True)
    await callback.answer()


# ============================================================================
# ОБЩИЕ ФУНКЦИИ ДЛЯ QR-ПЛАТЁЖНЫХ ПРОВАЙДЕРОВ (wata, platega, cardlink, yookassa)
# ============================================================================

def format_qr_payment_text(
    title: str,
    tariff_name: str,
    price_str: str,
    days: int,
    qr_url: str,
    key_name: str = None,
    hint_text: str = None,
    instruction_text: str = None,
) -> str:
    """
    Формирует текст сообщения с QR-кодом оплаты.

    Единая точка для всех провайдеров — при изменении текста
    достаточно поправить только здесь.

    Args:
        title: Заголовок (напр. '📱 <b>QR-код для оплаты</b>')
        tariff_name: Название тарифа (уже экранировано через escape_html)
        price_str: Отформатированная цена (напр. '100 ₽' или '50.00 ₽')
        days: Количество дней
        qr_url: Ссылка на оплату
        key_name: Название ключа при продлении (уже экранировано); None для покупки
        hint_text: Пользовательская подсказка внизу; если None — стандартная
        instruction_text: Пользовательская инструкция со ссылкой; поддерживает
            плейсхолдер {payment_link}
    """
    lines = [f"{title}\n"]

    if key_name:
        lines.append(f"🔑 <b>Ключ:</b> {key_name}")

    lines.append(f"💳 <b>Тариф:</b> {tariff_name}")
    lines.append(f"💰 <b>Сумма:</b> {price_str}")

    if key_name:
        lines.append(f"⏳ <b>Продление:</b> +{days} дней")
    else:
        lines.append(f"⏳ <b>Срок:</b> {days} дней")

    payment_link = f"<a href=\"{qr_url}\">ссылке на оплату</a>"
    if instruction_text is None:
        instruction_text = f"Отсканируйте QR код для перехода по {payment_link}."
    else:
        instruction_text = instruction_text.format(payment_link=payment_link)
    lines.append(f"\n{instruction_text}")

    if hint_text is None:
        hint_text = 'После оплаты нажмите «✅ Я оплатил».'
    lines.append(f"\n<i>{hint_text}</i>")

    return "\n".join(lines)


async def create_qr_payment_flow(
    callback: CallbackQuery,
    tariff: dict,
    price_rub: float,
    payment_type: str,
    create_func,
    save_func,
    result_key: str,
    title: str,
    check_prefix: str,
    error_name: str,
    qr_filename: str,
    back_callback: str,
    loading_text: str = '⏳ Создаём ссылку на оплату...',
    key: dict = None,
    vpn_key_id: int = None,
    hint_text: str = None,
    instruction_text: str = None,
) -> None:
    """
    Универсальный flow создания QR-счёта для любого провайдера.

    Выполняет: валидацию пользователя → создание ордера → вызов API провайдера →
    сохранение ID платежа → формирование текста → отправку QR-фото.

    Args:
        callback: Callback от кнопки выбора тарифа
        tariff: Словарь тарифа (уже валидирован)
        price_rub: Сумма к оплате в рублях
        payment_type: Тип платежа ('wata', 'platega', 'cardlink', 'yookassa_qr')
        create_func: Async-функция создания платежа (amount_rub, order_id, description, bot_name)
        save_func: Функция сохранения ID платежа в ордере (order_id, payment_id)
        result_key: Ключ в dict результата API (напр. 'wata_link_id')
        title: Заголовок сообщения (напр. '🌊 <b>Оплата WATA</b>')
        check_prefix: Префикс callback проверки (напр. 'check_wata')
        error_name: Название провайдера для ошибок (напр. 'WATA')
        qr_filename: Имя файла QR-картинки (напр. 'wata.png')
        back_callback: Callback кнопки «Назад» на экране QR
        loading_text: Текст загрузки
        key: Словарь ключа при продлении (None для покупки)
        vpn_key_id: ID ключа при продлении (None для покупки)
        hint_text: Пользовательская подсказка (None → стандартная)
        instruction_text: Пользовательская инструкция со ссылкой на оплату
    """
    from database.requests import get_user_internal_id, create_pending_order
    from bot.keyboards.user import qr_payment_kb
    from bot.keyboards.admin import home_only_kb

    # Валидация пользователя
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return

    # Создание ордера
    (_, order_id) = create_pending_order(
        user_id=user_id, tariff_id=tariff['id'],
        payment_type=payment_type, vpn_key_id=vpn_key_id
    )

    await safe_edit_or_send(callback.message, loading_text)

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username

        # Описание для провайдера
        if key:
            description = (
                f"Продление Ключа «{key['display_name']}»: "
                f"«{tariff['name']}» ({tariff['duration_days']} дн.)"
            )
        else:
            description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"

        # Вызов API провайдера
        create_kwargs = {
            'amount_rub': price_rub,
            'order_id': order_id,
            'description': description,
            'bot_name': bot_name,
        }
        try:
            import inspect
            signature = inspect.signature(create_func)
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            if accepts_kwargs or 'user_telegram_id' in signature.parameters:
                create_kwargs['user_telegram_id'] = callback.from_user.id
        except (TypeError, ValueError):
            pass
        result = await create_func(**create_kwargs)
        save_func(order_id, result[result_key])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')

        if not qr_image_data or not qr_url:
            await safe_edit_or_send(
                callback.message,
                f'❌ {error_name} не вернул данные для оплаты. Попробуйте позже.',
                reply_markup=home_only_kb()
            )
            return

        # Формирование текста
        text = format_qr_payment_text(
            title=title,
            tariff_name=escape_html(tariff['name']),
            price_str=f"{int(price_rub)} ₽",
            days=tariff['duration_days'],
            qr_url=qr_url,
            key_name=escape_html(key['display_name']) if key else None,
            hint_text=hint_text,
            instruction_text=instruction_text,
        )

        # Отправка QR-фото
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename=qr_filename)
        await safe_edit_or_send(
            callback.message, text, photo=photo,
            reply_markup=qr_payment_kb(order_id, check_prefix, back_callback, qr_url),
            force_new=True
        )
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания {error_name}-счёта: {e}')
        await safe_edit_or_send(
            callback.message,
            f'❌ <b>Ошибка создания платежа</b>\n\n<i>{escape_html(str(e))}</i>'
            f'\n\nПопробуйте другой способ оплаты.',
            reply_markup=home_only_kb()
        )

    await callback.answer()


async def check_qr_payment_flow(
    message,
    state: FSMContext,
    order_id: str,
    telegram_id: int,
    payment_type: str,
    payment_id_field: str,
    check_func,
    check_arg_is_order_id: bool = False,
    rate_limit_seconds: int = 0,
    rate_limit_prefix: str = '',
    pending_hint: str = None,
    callback: CallbackQuery = None,
    referral_override_func=None,
) -> None:
    """
    Универсальный flow проверки статуса QR-платежа.

    Выполняет: поиск ордера → проверку владельца → проверку «уже оплачено» →
    валидацию payment_id → rate-limiting → вызов API проверки → обработку результата.

    Args:
        message: Объект сообщения (callback.message или Message из deep-link)
        state: FSM-контекст
        order_id: ID ордера
        telegram_id: Telegram ID пользователя
        payment_type: Тип платежа ('wata', 'platega', 'cardlink', 'yookassa_qr')
        payment_id_field: Имя поля ID платежа в ордере ('wata_link_id', 'cardlink_bill_id', ...)
        check_func: Async-функция проверки статуса (payment_id) -> str ('succeeded'/'canceled'/...)
        check_arg_is_order_id: True если check_func принимает order_id вместо payment_id (WATA)
        rate_limit_seconds: Интервал rate-limit (0 — без ограничения)
        rate_limit_prefix: Префикс ключа rate-limit в FSM ('wata', 'platega', ...)
        pending_hint: Дополнительная подсказка в статусе «ожидание» (None → стандарт)
        callback: CallbackQuery (None при deep-link вызове)
        referral_override_func: Функция(order, state) -> int для нестандартного
                                расчёта реферального вознаграждения (yookassa с балансом)
    """
    import time
    from database.requests import (
        find_order_by_order_id, get_user_internal_id,
        is_order_already_paid, update_payment_type
    )
    from bot.services.billing import complete_payment_flow
    from bot.keyboards.admin import home_only_kb

    async def _show_order_not_found() -> None:
        if callback:
            await callback.answer('❌ Ордер не найден', show_alert=True)
        else:
            await safe_edit_or_send(message, '❌ Ордер не найден', reply_markup=home_only_kb())

    # 1. Поиск ордера
    order = find_order_by_order_id(order_id)
    if not order:
        await _show_order_not_found()
        return

    # 2. Проверка владельца ордера
    owner_user_id = get_user_internal_id(telegram_id)
    if not owner_user_id or int(order.get('user_id') or 0) != int(owner_user_id):
        logger.warning(
            'Попытка проверить чужой QR-платёж: order=%s, telegram_id=%s, owner=%s',
            order_id, telegram_id, order.get('user_id')
        )
        await _show_order_not_found()
        return

    # 3. Уже оплачено?
    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await finalize_payment_ui(
            message, state,
            '✅ Оплата уже была обработана ранее.',
            order, user_id=telegram_id
        )
        if callback:
            await callback.answer()
        return

    # 4. Валидация payment_id
    payment_id = order.get(payment_id_field)
    if not payment_id:
        if callback:
            await callback.answer('⚠️ Нет данных о платеже. Попробуйте чуть позже.', show_alert=True)
        else:
            await safe_edit_or_send(
                message,
                '⚠️ Нет данных о платеже. Попробуйте чуть позже.',
                reply_markup=home_only_kb()
            )
        return

    # 5. Rate-limiting
    if rate_limit_seconds > 0:
        state_data = await state.get_data()
        last_check_key = f'{rate_limit_prefix}_last_check_{order_id}'
        last_check = state_data.get(last_check_key, 0)
        now = time.time()
        elapsed = now - last_check
        if last_check and elapsed < rate_limit_seconds:
            wait = int(rate_limit_seconds - elapsed)
            if callback:
                await callback.answer(
                    f'⏳ Подождите {wait} сек. перед повторной проверкой.',
                    show_alert=True
                )
            return
        await state.update_data({last_check_key: now})

    # 6. Уведомление о проверке
    if callback:
        await callback.answer('🔍 Проверяем платёж...')

    # 7. Вызов API проверки
    try:
        check_arg = order_id if check_arg_is_order_id else payment_id
        status = await check_func(check_arg)
    except Exception as e:
        logger.error(f'Ошибка проверки статуса {payment_type} {order_id}: {e}')
        await safe_edit_or_send(
            message,
            '❌ Не удалось проверить статус платежа. Попробуйте позже.',
            reply_markup=home_only_kb(), force_new=True
        )
        return

    # 8. Обработка результата
    if status == 'succeeded':
        update_payment_type(order_id, payment_type)

        # Реферальное вознаграждение
        if referral_override_func:
            referral_amount = await referral_override_func(order, state)
        else:
            from database.requests import get_tariff_by_id
            _tariff = get_tariff_by_id(order.get('tariff_id'))
            referral_amount = int((_tariff.get('price_rub', 0) or 0) * 100) if _tariff else 0

        logger.info(f"{payment_type} referral: order={order_id}, referral_amount={referral_amount}")

        # Удаляем QR-фото
        try:
            await message.delete()
        except Exception:
            pass

        await complete_payment_flow(
            order_id=order_id,
            message=message,
            state=state,
            telegram_id=telegram_id,
            payment_type=payment_type,
            referral_amount=referral_amount
        )
    elif status == 'canceled':
        await safe_edit_or_send(
            message,
            '❌ <b>Платёж отменён</b>\n\nПохоже, платёж был отменён.\nПопробуйте снова выбрать тариф.',
            reply_markup=home_only_kb(), force_new=True
        )
    else:
        pending_text = '⏳ <b>Платёж ещё не поступил</b>\n\nОплатите по ссылке и нажмите «✅ Я оплатил» снова.'
        if pending_hint:
            pending_text += f'\n\n<i>{pending_hint}</i>'
        await safe_edit_or_send(message, pending_text, force_new=True)
