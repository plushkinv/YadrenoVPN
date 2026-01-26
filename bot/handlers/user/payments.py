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
    from bot.keyboards.admin import home_only_kb
    
    # Обрабатываем платёж
    success, response_text, order = process_crypto_payment(start_param)
    
    # Если это успешная оплата нового ключа — запускаем конфигурацию
    if success and order and not order.get('vpn_key_id'):
        # Вызываем процедуру выбора сервера для нового ключа
        await start_new_key_config(message, state, order['order_id'])
        return
    
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
    """Выбор тарифа для продления (оплата Stars)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb, back_and_home_kb
    
    # Парсим callback: renew_stars_tariff:key_id
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    # Проверяем ключ
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    # Получаем тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
        await callback.message.edit_text(
            "⭐ *Оплата звёздами*\n\n"
            "😔 Нет доступных тарифов для продления.",
            reply_markup=back_and_home_kb(back_callback=f"key_renew:{key_id}"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        f"⭐ *Оплата звёздами*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОПЛАТА STARS ЗА ПРОДЛЕНИЕ
# ============================================================================

@router.callback_query(F.data.startswith("renew_stars:"))
async def renew_stars_invoice(callback: CallbackQuery):
    """Отправка invoice для оплаты Stars (продление)."""
    from database.requests import (
        get_key_details_for_user, get_tariff_by_id, get_user_internal_id,
        create_pending_order
    )
    
    # Парсим callback: renew_stars:key_id:tariff_id
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    
    telegram_id = callback.from_user.id
    
    # Проверяем ключ
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    # Проверяем тариф
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    # Получаем внутренний ID
    user_id = get_user_internal_id(telegram_id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Создаём pending order и получаем order_id
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
    from database.requests import (
        find_order_by_order_id, complete_order, extend_vpn_key,
        is_order_already_paid, get_active_servers
    )
    from bot.keyboards.admin import home_only_kb
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    logger.info(f"Успешная оплата Stars: {payload}, charge_id={payment.telegram_payment_charge_id}")
    
    # Парсим payload
    if payload.startswith("renew:"):
        order_id = payload.split(":")[1]
    elif payload.startswith("vpn_key:"):
        # Старый формат для новых ключей (TODO: обработать отдельно)
        order_id = payment.telegram_payment_charge_id
    else:
        order_id = payload
    
    # Проверяем дубликат
    if is_order_already_paid(order_id):
        await message.answer(
            "✅ Этот платёж уже был обработан!",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return
    
    # Находим ордер
    order = find_order_by_order_id(order_id)
    if not order:
        # Это может быть новый ключ со старым payload
        logger.warning(f"Ордер не найден: {order_id}")
        await message.answer(
            "✅ Оплата принята!\n\n"
            "⚠️ Возникла проблема с обработкой. Мы свяжемся с вами.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return
    
    # Завершаем ордер
    complete_order(order_id)
    
    # Продлеваем ключ
    if order['vpn_key_id']:
        days = order['duration_days'] or order['period_days']
        if days and extend_vpn_key(order['vpn_key_id'], days):
            await message.answer(
                f"🎉 *Оплата прошла успешно!*\n\n"
                f"Ваш ключ продлён на {days} дней.\n\n"
                f"Спасибо за покупку! 🚀",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "✅ Оплата принята!\n\n"
                "⚠️ Возникла проблема с продлением. Мы разберёмся!",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
    else:
        # Новый ключ — вызываем общую процедуру
        await start_new_key_config(message, state, order_id)


async def start_new_key_config(message: Message, state: FSMContext, order_id: str):
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
    await state.update_data(new_key_order_id=order_id)
    
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
        get_server_by_id, create_vpn_key, update_payment_key_id, 
        find_order_by_order_id, get_user_internal_id,
        get_key_details_for_user
    )
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    from config import DEFAULT_TOTAL_GB
    
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    
    if not order_id:
        await callback.message.edit_text("❌ Ошибка: потерян номер заказа.")
        await state.clear()
        return

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.message.edit_text("❌ Ошибка: заказ не найден.")
        await state.clear()
        return
        
    await callback.message.edit_text("⏳ Создаём ваш ключ...")
    
    try:
        user_id = order['user_id']
        telegram_id = callback.from_user.id
        username = callback.from_user.username
        
        # Данные для генерации email
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        
        client = await get_client(server_id)
        
        # Создаем ключ на сервере
        days = order['duration_days'] or 30
        
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
        
        # Создаем запись в БД
        key_id = create_vpn_key(
            user_id=user_id,
            server_id=server_id,
            tariff_id=order['tariff_id'],
            panel_inbound_id=inbound_id,
            panel_email=panel_email,
            client_uuid=client_uuid,
            days=days
        )
        
        # Привязываем ключ к платежу
        update_payment_key_id(order_id, key_id)
        
        await state.clear()
        
        # Получаем данные ключа для отображения
        new_key = get_key_details_for_user(key_id, telegram_id)
        
        # Используем унифицированную отправку
        await send_key_with_qr(callback, new_key, key_issued_kb(), is_new=True)

    except Exception as e:
        logger.error(f"Ошибка создания ключа (post-payment): {e}")
        await callback.message.edit_text(
            f"❌ Ошибка создания ключа: {e}\n"
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

