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
from config import ADMIN_IDS

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
    success, response_text, order = process_crypto_payment(start_param, user_id=user_id)
    
    # Используем единую точку выхода UI
    if success and order:
        await finalize_payment_ui(message, state, response_text, order)
    else:
        from bot.keyboards.admin import home_only_kb
        await message.answer(
            response_text,
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )



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
    await callback.message.answer_invoice(
        title=f"Продление VPN: {tariff['name']}",
        description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.",
        payload=f"renew:{order_id}",
        currency="XTR",
        prices=[LabeledPrice(label=f"VPN {tariff['name']}", amount=tariff['price_stars'])],
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
        order_id = payment.telegram_payment_charge_id
    else:
        order_id = payload
    
    # Обрабатываем платеж через единую функцию
    success, text, order = process_payment_order(order_id)
    
    # Завершаем UI
    if success and order:
        await finalize_payment_ui(message, state, text, order)
    else:
        # Если ошибка (например, не найден ордер или дубль, но process_payment возвращает True для дублей)
        # Если success=True, но order=None (например, дубль без контекста?)
        # process_payment возвращает order даже для дублей
        pass
        
    if not success:
         from bot.keyboards.admin import home_only_kb
         await message.answer(text, reply_markup=home_only_kb(), parse_mode="Markdown")


async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict):
    """
    Завершает UI после успешной оплаты.
    Показывает сообщение и либо перекидывает на настройку (draft), либо на главную.
    """
    from bot.keyboards.admin import home_only_kb
    from database.requests import get_key_details_for_user
    import logging
    
    # Локальный логгер, если глобальный недоступен
    logger = logging.getLogger(__name__)
    
    key_id = order.get('vpn_key_id')
    user_id = message.from_user.id 
    
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

    logger.info(f"Result: is_draft={is_draft}")
            
    if is_draft:
        # Если это черновик - сначала поздравляем, потом сразу запускаем настройку
        await message.answer(text, parse_mode="Markdown")
        await start_new_key_config(message, state, order['order_id'], key_id)
    else:
        # Если это продление или готовый ключ
        await message.answer(
            text,
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )


async def start_new_key_config(message: Message, state: FSMContext, order_id: str, key_id: int = None):
    """
    Запускает процесс настройки нового ключа (выбор сервера).
    Используется как для Stars, так и для Crypto.
    """
    from database.requests import get_active_servers
    from bot.keyboards.user import new_key_server_list_kb
    from bot.keyboards.admin import home_only_kb
    from bot.states.user_states import NewKeyConfig
    
    servers = get_active_servers()
    
    if not servers:
        logger.error(f"Нет активных серверов для создания ключа (Order: {order_id})")
        await message.answer(
            "🎉 *Оплата прошла успешно!*\n\n"
            "⚠️ К сожалению, сейчас нет доступных серверов.\n"
            "Пожалуйста, свяжитесь с поддержкой.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return

    # Устанавливаем состояние
    await state.set_state(NewKeyConfig.waiting_for_server)
    await state.update_data(new_key_order_id=order_id, new_key_id=key_id)
    
    await message.answer(
        "🎉 *Оплата прошла успешно!*\n\n"
        "🔑 Теперь выберите сервер для вашего нового ключа.",
        reply_markup=new_key_server_list_kb(servers),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("renew_invoice_cancel:"))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Отмена инвойса и возврат к выбору тарифа (Stars)."""
    from bot.keyboards.user import renew_tariff_select_kb
    from database.requests import get_key_details_for_user, get_all_tariffs
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    # tariff_id = int(parts[2]) # не используется для возврата к списку
    
    telegram_id = callback.from_user.id
    
    # Пытаемся удалить сообщение с инвойсом
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    # Получаем тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.answer(
        f"⭐ *Оплата звёздами*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# СОЗДАНИЕ НОВОГО КЛЮЧА (ПОСЛЕ ОПЛАТЫ)
# ============================================================================

@router.callback_query(F.data.startswith("new_key_server:"))
async def process_new_key_server_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для нового ключа."""
    from database.requests import get_server_by_id
    from bot.services.vpn_api import get_client, VPNAPIError
    from bot.keyboards.user import new_key_inbound_list_kb
    from bot.states.user_states import NewKeyConfig
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await state.update_data(new_key_server_id=server_id)
    
    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        
        if not inbounds:
            await callback.answer("❌ На сервере нет доступных протоколов", show_alert=True)
            return
        
        # Если inbound только один — выбираем автоматически
        if len(inbounds) == 1:
            await process_new_key_final(callback, state, server_id, inbounds[0]['id'])
            return

        await state.set_state(NewKeyConfig.waiting_for_inbound)
        
        await callback.message.edit_text(
            f"🖥️ *Сервер:* {server['name']}\n\n"
            "Выберите протокол:",
            reply_markup=new_key_inbound_list_kb(inbounds),
            parse_mode="Markdown"
        )
    except VPNAPIError as e:
        await callback.answer(f"❌ Ошибка подключения: {e}", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("new_key_inbound:"))
async def process_new_key_inbound_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор протокола (inbound) для нового ключа."""
    inbound_id = int(callback.data.split(":")[1])
    
    data = await state.get_data()
    server_id = data.get('new_key_server_id')
    
    await process_new_key_final(callback, state, server_id, inbound_id)


async def process_new_key_final(callback: CallbackQuery, state: FSMContext, server_id: int, inbound_id: int):
    """Финальный этап создания ключа."""
    from database.requests import (
        get_server_by_id, update_vpn_key_config, update_payment_key_id, 
        find_order_by_order_id, get_user_internal_id,
        get_key_details_for_user, create_initial_vpn_key
    )
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    from config import DEFAULT_TOTAL_GB
    
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    
    if not order_id:
        await callback.message.edit_text("❌ Ошибка: потерян номер заказа.")
        await state.clear()
        return

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.message.edit_text("❌ Ошибка: заказ не найден.")
        await state.clear()
        return
    
    # Если key_id не передан через state, ищем в ордере
    if not key_id:
        if order['vpn_key_id']:
            key_id = order['vpn_key_id']
        else:
            # Если ключа нет (экстренный случай), создаем
            days = order.get('period_days') or order.get('duration_days') or 30
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days)
            update_payment_key_id(order_id, key_id)

    await callback.message.edit_text("⏳ Настраиваем ваш ключ...")
    
    try:
        user_id = order['user_id']
        telegram_id = callback.from_user.id
        username = callback.from_user.username
        
        # Данные для генерации email
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        
        client = await get_client(server_id)
        
        # Создаем ключ на сервере
        days = order.get('period_days') or order.get('duration_days') or 30
        
        # Конвертируем байты в ГБ (int) для API
        limit_gb = int(DEFAULT_TOTAL_GB / (1024**3))
        
        res = await client.add_client(
            inbound_id=inbound_id,
            email=panel_email,
            total_gb=limit_gb, 
            expire_days=days,
            limit_ip=1,
            enable=True,
            tg_id=str(telegram_id)
        )
        
        client_uuid = res['uuid']
        
        # Обновляем конфигурацию существующего ключа
        update_vpn_key_config(
            key_id=key_id,
            server_id=server_id,
            panel_inbound_id=inbound_id,
            panel_email=panel_email,
            client_uuid=client_uuid
        )
        
        # Привязываем ключ к платежу (повт.)
        update_payment_key_id(order_id, key_id)
        
        await state.clear()
        
        # Получаем данные ключа для отображения
        new_key = get_key_details_for_user(key_id, telegram_id)
        
        # Используем унифицированную отправку
        await send_key_with_qr(callback, new_key, key_issued_kb(), is_new=True)

    except Exception as e:
        logger.error(f"Ошибка настройки ключа (id={key_id}): {e}")
        await callback.message.edit_text(
            f"❌ Ошибка настройки ключа: {e}\n"
            "Обратитесь в поддержку, указав Order ID: " + str(order_id)
        )


@router.callback_query(F.data == "back_to_server_select")
async def back_to_server_select(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору сервера."""
    from database.requests import get_active_servers
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    
    servers = get_active_servers()
    await state.set_state(NewKeyConfig.waiting_for_server)
    
    await callback.message.edit_text(
        "🔑 Выберите сервер для вашего нового ключа.",
        reply_markup=new_key_server_list_kb(servers),
        parse_mode="Markdown"
    )

