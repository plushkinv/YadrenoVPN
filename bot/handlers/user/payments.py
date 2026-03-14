"""
Обработчики платежей пользователя.

Обрабатывает:
- Callback от криптопроцессинга (bill1-...)
- Оплату Telegram Stars
- Продление ключей
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from bot.utils.text import escape_md

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ОБРАБОТКА CALLBACK ОТ КРИПТОПРОЦЕССИНГА
# ============================================================================

@router.message(Command("start"), F.text.contains("bill"))
async def handle_start_with_payment(message: Message, command: CommandObject, state: FSMContext):
    """
    Обрабатывает /start с параметром bill1-... (callback от криптопроцессинга).
    Фильтруем по наличию "bill" в тексте, чтобы не перехватывать обычный /start.
    """
    # Получаем параметр команды
    start_param = command.args
    
    if not start_param or not start_param.startswith('bill'):
        return  # На всякий случай, хотя фильтр уже отсеял
    
    from bot.services.billing import process_crypto_payment
    from database.requests import get_or_create_user
    
    # Гарантируем создание пользователя
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    user_id = user['id']
    
    # Обрабатываем платёж
    try:
        success, response_text, order = process_crypto_payment(start_param, user_id=user_id)
        
        # Используем единую точку выхода UI
        if success and order:
            await finalize_payment_ui(message, state, response_text, order, user_id=message.from_user.id)
        else:
            from bot.keyboards.admin import home_only_kb
            await message.answer(
                response_text,
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            
    except Exception as e:
        from bot.errors import TariffNotFoundError
        if isinstance(e, TariffNotFoundError):
            from database.requests import get_setting
            from bot.keyboards.user import support_kb
            
            support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
            await message.answer(
                str(e),
                reply_markup=support_kb(support_link),
                parse_mode="Markdown"
            )
        else:
            logger.exception(f"Ошибка платежа: {e}")
            await message.answer("❌ Ошибка обработки", parse_mode="Markdown")



# ============================================================================
# ПРОДЛЕНИЕ: ВЫБОР СПОСОБА ОПЛАТЫ
# ============================================================================

@router.callback_query(F.data.startswith("renew_stars_tariff:"))
async def renew_stars_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Stars)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    # Получаем тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.edit_text(
        f"⭐ *Оплата звёздами*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОПЛАТА STARS ЗА ПРОДЛЕНИЕ
# ============================================================================

@router.callback_query(F.data.startswith("renew_pay_stars:"))
async def renew_stars_invoice(callback: CallbackQuery):
    """Инвойс для продления (Stars)."""
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, 
        create_pending_order, get_key_details_for_user,
        update_order_tariff, update_payment_type
    )
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await callback.answer("Ошибка тарифа или ключа", show_alert=True)
        return
        
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        return

    # Логика создания/обновления ордера
    if order_id:
         # Переиспользуем существующий
         update_order_tariff(order_id, tariff_id)
         update_payment_type(order_id, 'stars')
    else:
         # Создаем новый
         _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='stars',
            vpn_key_id=key_id
        )
    
    # Отправляем invoice
    # payload содержит order_id для идентификации платежа
    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    
    await callback.message.answer_invoice(
        title=bot_name,
        description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.",
        payload=f"renew:{order_id}",
        currency="XTR",
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=tariff['price_stars'])],
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text=f"⭐️ Оплатить {tariff['price_stars']} XTR", pay=True)
        ).row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"renew_invoice_cancel:{key_id}:{tariff_id}")
        ).as_markup()
    )
    
    # Удаляем предыдущее сообщение
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# ОБРАБОТКА TELEGRAM STARS
# ============================================================================

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Подтверждение pre-checkout для Telegram Stars."""
    # Всегда подтверждаем — проверки делаем при создании invoice
    await pre_checkout.answer(ok=True)



@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, state: FSMContext):
    """Обработка успешной оплаты Stars."""
    from bot.services.billing import process_payment_order
    
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    logger.info(f"Успешная оплата Stars: {payload}, charge_id={payment.telegram_payment_charge_id}")
    
    # Парсим payload
    if payload.startswith("renew:"):
        order_id = payload.split(":")[1]
    elif payload.startswith("vpn_key:"):
        order_id = payload.split(":")[1]
    else:
        order_id = payload
    
    # Обрабатываем платеж через единую функцию
    try:
        success, text, order = process_payment_order(order_id)
        
        # Завершаем UI
        if success and order:
            await finalize_payment_ui(message, state, text, order, user_id=message.from_user.id)
        else:
             from bot.keyboards.admin import home_only_kb
             await message.answer(text, reply_markup=home_only_kb(), parse_mode="Markdown")
             
    except Exception as e:
        from bot.errors import TariffNotFoundError
        if isinstance(e, TariffNotFoundError):
            from database.requests import get_setting
            from bot.keyboards.user import support_kb
            
            support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
            await message.answer(
                str(e),
                reply_markup=support_kb(support_link),
                parse_mode="Markdown"
            )
        else:
            logger.exception(f"Ошибка обработки Stars платежа: {e}")
            await message.answer("❌ Произошла ошибка при обработке платежа.", parse_mode="Markdown")


async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Завершает UI после успешной оплаты.
    Для новой покупки сразу формирует и отправляет subscription-ссылку.
    """
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import key_issued_kb
    from bot.services.subscription import provision_subscription_for_new_order
    
    key_id = order.get('vpn_key_id')
    
    logger.info(f"finalize_payment_ui: Order={order.get('order_id')}, Key={key_id}, User={user_id}")
    
    is_draft = False
    if key_id:
        key = get_key_details_for_user(key_id, user_id)
        if key:
            logger.info(f"Key details found: ID={key['id']}, ServerID={key.get('server_id')}")
            # Если сервер не выбран - это черновик
            if not key.get('server_id'):
                is_draft = True
        else:
            logger.warning(f"Key {key_id} not found for user {user_id} via details check!")
    else:
        logger.info("No key_id in order object.")

    logger.info(f"Result: is_draft={is_draft}")
            
    if is_draft:
        try:
            subscription_result = await provision_subscription_for_new_order(
                order=order,
                telegram_id=user_id
            )
            subscription_url = subscription_result["subscription_url"]
            await state.clear()
            await message.answer(
                (
                    "✅ *Покупка успешно завершена!*\n\n"
                    f"{text}\n\n"
                    "*Ваша subscription ссылка:*\n"
                    f"`{subscription_url}`\n\n"
                    "Импортируйте её в Hiddify / v2rayNG / Shadowrocket / Streisand "
                    "или любой клиент с поддержкой subscription."
                ),
                reply_markup=key_issued_kb(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.exception("Ошибка автогенерации подписки: %s", e)
            from bot.keyboards.admin import home_only_kb
            await message.answer(
                "✅ Оплата принята, но не удалось автоматически создать подписку.\n"
                "Пожалуйста, обратитесь в поддержку.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
    else:
        # Если это продление или готовый ключ
        from bot.handlers.user.main import show_key_details
        await show_key_details(
            telegram_id=user_id,
            key_id=key_id,
            send_function=message.answer,
            prepend_text=text
        )


@router.callback_query(F.data.startswith("renew_invoice_cancel:"))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Отмена инвойса и возврат к выбору способа оплаты."""
    from bot.keyboards.user import renew_payment_method_kb
    from database.requests import get_key_details_for_user, get_all_tariffs, is_crypto_configured, is_stars_enabled, is_cards_enabled, get_user_internal_id, create_pending_order, get_setting
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    telegram_id = callback.from_user.id
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    crypto_configured = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    
    if not crypto_configured and not stars_enabled and not cards_enabled:
         await callback.message.answer("😔 Способы оплаты временно недоступны.", parse_mode="Markdown")
         return
         
    crypto_url = None
    if crypto_configured:
        tariffs = get_all_tariffs(include_hidden=False)
        if tariffs:
            user_id = get_user_internal_id(telegram_id)
            if user_id:
                 _, order_id = create_pending_order(
                    user_id=user_id,
                    tariff_id=tariffs[0]['id'],
                    payment_type='crypto',
                    vpn_key_id=key_id
                )
                 item_url = get_setting('crypto_item_url')
                 item_id = extract_item_id_from_url(item_url)
                 if item_id:
                     crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, tariff_external_id=None, price_cents=None)

    await callback.message.answer(
        f"💳 *Продление ключа*\n\n"
        f"🔑 Ключ: *{key['display_name']}*\n\n"
        "Выберите способ оплаты:",
        reply_markup=renew_payment_method_kb(key_id, crypto_url, stars_enabled, cards_enabled),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОПЛАТА КАРТАМИ ЮКАССА
# ============================================================================

@router.callback_query(F.data.startswith("pay_cards"))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Картой (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    order_id = None
    if ":" in callback.data:
        order_id = callback.data.split(":")[1]

    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
        await callback.message.edit_text(
            "💳 *Оплата картой*\n\n"
            "😔 Нет доступных тарифов.\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "💳 *Оплата картой*\n\n"
        "Выберите тариф:",
        reply_markup=tariff_select_kb(tariffs, order_id=order_id, is_cards=True),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cards_pay:"))
async def pay_cards_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Картой (Новый ключ)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, update_order_tariff, get_setting
    
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
        
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer("❌ Провайдер платежей не настроен", show_alert=True)
        return
        
        
    days = tariff['duration_days']
        
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        user_id = get_user_internal_id(callback.from_user.id)
        if not user_id:
            await callback.answer("❌ Ошибка пользователя", show_alert=True)
            return

        _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='cards',
            vpn_key_id=None 
        )

    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer("❌ Ошибка: цена тарифа в рублях не задана.", show_alert=True)
        return
        
    from aiogram.exceptions import TelegramBadRequest

    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).",
            payload=f"vpn_key:{order_id}",
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"💳 Оплатить {price_rub} ₽", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data="buy_key")
            ).as_markup()
        )
    except TelegramBadRequest as e:
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer("❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.", show_alert=True)
            return
        logger.exception("Ошибка при отправке инвойса картой (новый ключ).")
        raise e
    
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith("renew_cards_tariff:"))
async def renew_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Картой)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.edit_text(
        f"💳 *Оплата картой*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_cards=True),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("renew_pay_cards:"))
async def renew_cards_invoice(callback: CallbackQuery):
    """Инвойс для продления (Картой)."""
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, 
        create_pending_order, get_key_details_for_user,
        update_order_tariff, get_setting
    )
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await callback.answer("Ошибка тарифа или ключа", show_alert=True)
        return
        
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer("❌ Провайдер платежей не настроен", show_alert=True)
        return
        
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        return

    if order_id:
         update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
         _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='cards',
            vpn_key_id=key_id
        )
    
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer("❌ Ошибка: цена тарифа в рублях не задана.", show_alert=True)
        return
        
    # Формируем клавиатуру оплаты
    # У Telegram Payments кнопка "Оплатить X RUB" добавляется автоматически, 
    # если не передать reply_markup. Но если мы хотим отмену, нужно
    # передать pay=True первой кнопкой.
    from aiogram.exceptions import TelegramBadRequest

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.",
            payload=f"renew:{order_id}",
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"💳 Оплатить {tariff.get('price_rub', 0)} ₽", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"renew_invoice_cancel:{key_id}:{tariff_id}")
            ).as_markup()
        )
    except TelegramBadRequest as e:
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in str(e):
            logger.warning(f"Ошибка платежа (CARDS_RENEW): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer("❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.", show_alert=True)
            return
        logger.exception("Ошибка при отправке инвойса картой (продление ключа).")
        raise e
    
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# QR-ОПЛАТА ЮКАССА (direct API — без Telegram Payments)
# ============================================================================

@router.callback_query(F.data == "pay_qr")
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import qr_tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]

    if not rub_tariffs:
        await callback.message.edit_text(
            "📱 *QR-оплата*\n\n"
            "😔 Для QR-оплаты не настроены цены в рублях.\n"
            "Обратитесь к администратору.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📱 *QR-оплата (Карта/СБП)*\n\n"
        "Выберите тариф:\n\n"
        "_Оплата через ЮКассу — поддерживает банковские карты и СБП._",
        reply_markup=qr_tariff_select_kb(rub_tariffs),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("qr_pay:"))
async def qr_pay_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для нового ключа и отправляет QR-фото."""
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, create_pending_order,
        save_yookassa_payment_id
    )
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb

    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)

    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer("❌ Цена в рублях не задана для этого тарифа", show_alert=True)
        return

    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='yookassa_qr',
        vpn_key_id=None
    )

    await callback.message.edit_text("⏳ Создаём QR-код для оплаты...")

    try:
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(
            amount_rub=price_rub,
            order_id=order_id,
            description=description
        )

        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await callback.message.edit_text(
                "❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            return

        text = (
            f"📱 *QR-код для оплаты*\n\n"
            f"💳 *Тариф:* {tariff['name']}\n"
            f"💰 *Сумма:* {int(price_rub)} ₽\n"
            f"⏳ *Срок:* {tariff['duration_days']} дней\n\n"
            f"Отсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n"
            "_После оплаты нажмите «✅ Я оплатил»._"
        )
        
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename="qr.png")

        await callback.message.delete()
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=yookassa_qr_kb(order_id, back_callback="pay_qr"),
            parse_mode="Markdown"
        )

    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка создания QR ЮКасса: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка создания QR*\n\n_{e}_\n\nПопробуйте другой способ оплаты.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )

    await callback.answer()


@router.callback_query(F.data.startswith("check_yookassa_qr:"))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    """
    Проверяет статус QR-платежа ЮКасса по нажатию «✅ Я оплатил».
    При успехе — запускает процесс создания ключа.
    """
    from database.requests import (
        find_order_by_order_id, is_order_already_paid, update_payment_type
    )
    from bot.services.billing import check_yookassa_payment_status, process_payment_order
    from bot.keyboards.admin import home_only_kb

    order_id = callback.data.split(":", 1)[1]

    if is_order_already_paid(order_id):
        order = find_order_by_order_id(order_id)
        if order:
            await finalize_payment_ui(callback.message, state,
                                      "✅ Оплата уже была обработана ранее.", order, user_id=callback.from_user.id)
        await callback.answer()
        return

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.answer("❌ Ордер не найден", show_alert=True)
        return

    yookassa_payment_id = order.get('yookassa_payment_id')
    if not yookassa_payment_id:
        await callback.answer("⚠️ Нет данных о платеже. Попробуйте чуть позже.", show_alert=True)
        return

    await callback.answer("🔍 Проверяем платёж...")

    try:
        status = await check_yookassa_payment_status(yookassa_payment_id)
    except Exception as e:
        logger.error(f"Ошибка проверки статуса ЮКасса {yookassa_payment_id}: {e}")
        await callback.message.answer(
            "❌ Не удалось проверить статус платежа. Попробуйте позже.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return

    if status == 'succeeded':
        update_payment_type(order_id, 'yookassa_qr')

        try:
            success, text, updated_order = process_payment_order(order_id)
            if success and updated_order:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await finalize_payment_ui(callback.message, state, text, updated_order, user_id=callback.from_user.id)
            else:
                await callback.message.answer(
                    text, reply_markup=home_only_kb(), parse_mode="Markdown"
                )
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from database.requests import get_setting
                from bot.keyboards.user import support_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await callback.message.answer(str(e),
                                              reply_markup=support_kb(support_link),
                                              parse_mode="Markdown")
            else:
                logger.exception(f"Ошибка обработки QR-платежа: {e}")
                await callback.message.answer(
                    "❌ Произошла ошибка при обработке платежа.", parse_mode="Markdown"
                )

    elif status == 'canceled':
        await callback.message.answer(
            "❌ *Платёж отменён*\n\n"
            "Похоже, платёж был отменён или истёк срок QR-кода.\n"
            "Попробуйте снова выбрать тариф.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
    else:
        # pending / waiting_for_capture / etc.
        await callback.message.answer(
            "⏳ *Платёж ещё не поступил*\n\n"
            "Оплатите QR-код и нажмите «✅ Я оплатил» снова.\n\n"
            "_Если только что оплатили — подождите пару секунд._",
            parse_mode="Markdown"
        )


# ============================================================================
# QR-ОПЛАТА ЮКАССА ПРИ ПРОДЛЕНИИ КЛЮЧА
# ============================================================================

@router.callback_query(F.data.startswith("renew_qr_tariff:"))
async def renew_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты при продлении ключа."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_yookassa_qr_tariff_kb
    from bot.utils.text import escape_md

    key_id = int(callback.data.split(":")[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]

    if not rub_tariffs:
        await callback.answer("😔 Нет тарифов с ценой в рублях", show_alert=True)
        return

    await callback.message.edit_text(
        f"📱 *QR-оплата (Карта/СБП)*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_yookassa_qr_tariff_kb(rub_tariffs, key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_pay_qr:"))
async def renew_qr_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для продления ключа."""
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, create_pending_order,
        save_yookassa_payment_id, get_key_details_for_user
    )
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from bot.utils.text import escape_md

    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])

    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)

    if not tariff or not key:
        await callback.answer("❌ Ошибка тарифа или ключа", show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer("❌ Цена в рублях не задана", show_alert=True)
        return

    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='yookassa_qr',
        vpn_key_id=key_id
    )

    await callback.message.edit_text("⏳ Создаём QR-код для оплаты...")

    try:
        description = (
            f"Продление Ключа «{key['display_name']}»: "
            f"«{tariff['name']}» ({tariff['duration_days']} дн.)"
        )
        result = await create_yookassa_qr_payment(
            amount_rub=price_rub,
            order_id=order_id,
            description=description
        )

        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await callback.message.edit_text(
                "❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            return

        text = (
            f"📱 *QR-код для оплаты*\n\n"
            f"🔑 *Ключ:* {escape_md(key['display_name'])}\n"
            f"💳 *Тариф:* {tariff['name']}\n"
            f"💰 *Сумма:* {int(price_rub)} ₽\n"
            f"⏳ *Продление:* +{tariff['duration_days']} дней\n\n"
            f"Отсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n"
            "_После оплаты нажмите «✅ Я оплатил»._"
        )
        
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename="qr.png")

        await callback.message.delete()
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=yookassa_qr_kb(order_id, back_callback=f"renew_qr_tariff:{key_id}"),
            parse_mode="Markdown"
        )

    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка QR ЮКасса (продление): {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка создания QR*\n\n_{e}_",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )

    await callback.answer()
