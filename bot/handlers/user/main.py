"""
Главный роутер пользовательской части.

Обрабатывает команду /start и главное меню пользователя.
"""
import logging
import uuid
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_all_servers
from bot.keyboards.user import main_menu_kb
from bot.states.user_states import RenameKey, ReplaceKey

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# КОМАНДА /START
# ============================================================================

def get_welcome_text(is_admin: bool = False) -> str:
    """Формирует приветственный текст с реальными тарифами из БД."""
    from database.requests import get_all_tariffs, get_setting
    
    # 1. Получаем статический текст из БД
    welcome_text = get_setting('main_page_text', "🔐 *Добро пожаловать в VPN-бот!*")
    
    lines = [welcome_text, ""]
    
    # 2. Получаем тарифы из БД (только активные)
    tariffs = get_all_tariffs()
    
    if tariffs:
        lines.append("📋 *Тарифы:*")
        for tariff in tariffs:
            # Форматируем длительность
            days = tariff['duration_days']
            if days >= 365:
                duration = f"{days // 365} год" if days // 365 == 1 else f"{days // 365} года"
            elif days >= 30:
                months = days // 30
                if months == 1:
                    duration = "1 месяц"
                elif months in [2, 3, 4]:
                    duration = f"{months} месяца"
                else:
                    duration = f"{months} месяцев"
            else:
                duration = f"{days} дней"
            
            # Форматируем цену
            price_usd = tariff['price_cents'] / 100
            price_stars = tariff['price_stars']
            
            lines.append(f"• {duration} — ${price_usd:.0f} / {price_stars} ⭐")
        
        lines.append("")  # Пустая строка после тарифов
    
    lines.append("Выберите действие:")
    
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Регистрируем/обновляем пользователя
    user = get_or_create_user(user_id, username)
    
    # Проверяем бан
    if user.get('is_banned'):
        await message.answer(
            "⛔ *Доступ заблокирован*\n\n"
            "Ваш аккаунт заблокирован. Обратитесь в поддержку.",
            parse_mode="Markdown"
        )
        return
    
    # Сбрасываем состояние FSM
    await state.clear()
    
    # Проверяем админа
    is_admin = user_id in ADMIN_IDS
    
    text = get_welcome_text(is_admin)
    
    # Проверяем аргументы запуска (deep linking)
    args = command.args
    if args and args.startswith("bill"):
        from bot.services.billing import process_crypto_payment
        from bot.handlers.user.payments import start_new_key_config
        
        # Обрабатываем платеж
        success, text, order = process_crypto_payment(args)
        
        await message.answer(text, parse_mode="Markdown")
        
        # Если успех и это новый ключ — запускаем настройку
        if success and order and not order.get('vpn_key_id'):
            # order_id нужен для привязки
            await start_new_key_config(message, state, order['order_id'])
            return

    await message.answer(
        text,
        reply_markup=main_menu_kb(is_admin=is_admin),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "start")
async def callback_start(callback: CallbackQuery, state: FSMContext):
    """Возврат на главный экран по кнопке."""
    user_id = callback.from_user.id
    
    # Проверяем бан
    if is_user_banned(user_id):
        await callback.answer("⛔ Доступ заблокирован", show_alert=True)
        return
    
    # Сбрасываем состояние FSM
    await state.clear()
    
    # Проверяем админа
    is_admin = user_id in ADMIN_IDS
    
    text = get_welcome_text(is_admin)
    
    # Пытаемся отредактировать сообщение (если текст)
    # Если это фото/файл (после выдачи ключа), edit_text упадёт.
    try:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_kb(is_admin=is_admin),
            parse_mode="Markdown"
        )
    except Exception:
        # Удаляем фото/файл и отправляем новое сообщение
        try:
            await callback.message.delete()
        except:
            pass
        await callback.message.answer(
            text,
            reply_markup=main_menu_kb(is_admin=is_admin),
            parse_mode="Markdown"
        )

    await callback.answer()


# ============================================================================
# КОМАНДЫ (дублируют кнопки)
# ============================================================================

@router.message(Command("mykeys"))
async def cmd_mykeys(message: Message, state: FSMContext):
    """Обработчик команды /mykeys - вызывает логику кнопки 'Мои ключи'."""
    # Проверяем бан
    if is_user_banned(message.from_user.id):
        await message.answer(
            "⛔ *Доступ заблокирован*\n\n"
            "Ваш аккаунт заблокирован. Обратитесь в поддержку.",
            parse_mode="Markdown"
        )
        return
    
    # Сбрасываем состояние FSM
    await state.clear()
    
    # Вызываем общую логику (используем answer вместо edit_text)
    await show_my_keys(message.from_user.id, message.answer)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    """Обработчик команды /help - вызывает логику кнопки 'Справка'."""
    # Проверяем бан
    if is_user_banned(message.from_user.id):
        await message.answer(
            "⛔ *Доступ заблокирован*\n\n"
            "Ваш аккаунт заблокирован. Обратитесь в поддержку.",
            parse_mode="Markdown"
        )
        return
    
    # Сбрасываем состояние FSM
    await state.clear()
    
    # Вызываем общую логику
    await show_help(message.answer)


# ============================================================================
# РАЗДЕЛ «МОИ КЛЮЧИ»
# ============================================================================

async def show_my_keys(telegram_id: int, send_function):
    """
    Общая логика для показа списка ключей.
    
    Args:
        telegram_id: ID пользователя в Telegram
        send_function: Функция для отправки сообщения (message.answer или callback.message.edit_text)
    """
    from database.requests import get_user_keys_for_display
    from bot.keyboards.user import my_keys_list_kb
    from bot.keyboards.admin import home_only_kb
    from bot.services.vpn_api import get_client, format_traffic
    
    keys = get_user_keys_for_display(telegram_id)
    
    if not keys:
        await send_function(
            "🔑 *Мои ключи*\n\n"
            "У вас пока нет VPN-ключей.\n\n"
            "Нажмите «Купить ключ» на главной, чтобы приобрести доступ! 🚀",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return
    
    # Формируем текст со списком
    lines = ["🔑 *Мои ключи*\n"]
    
    for key in keys:
        # Статус эмодзи
        if key['is_active']:
            status_emoji = "🟢"
        else:
            status_emoji = "🔴"
        
        # Инфо о трафике и протоколе (пытаемся получить из API)
        traffic_text = "?/? GB"
        protocol = "VLESS"  # Дефолт
        inbound_name = "VPN"  # Дефолт
        
        if key.get('server_id') and key.get('panel_email'):
            try:
                client = await get_client(key['server_id'])
                stats = await client.get_client_stats(key['panel_email'])
                if stats:
                    # Используем format_traffic для красивого отображения
                    used_str = format_traffic(stats['up'] + stats['down'])
                    limit_str = format_traffic(stats['total']) if stats['total'] > 0 else "∞"
                    
                    traffic_text = f"{used_str} / {limit_str}"
                    protocol = stats['protocol'].upper()
                    inbound_name = stats.get('remark', 'VPN') or "VPN"
            except Exception as e:
                logger.warning(f"Не удалось получить стат. для ключа {key['id']}: {e}")
        
        # Форматируем дату
        expires = key['expires_at'][:10] if key['expires_at'] else "—"
        
        # Сервер
        server = key.get('server_name') or "Не выбран"
        
        # Собираем строку (дизайн пользователя)
        lines.append(f"{status_emoji}*{key['display_name']}* - {traffic_text} - до {expires}")
        lines.append(f"     📍{server} - {inbound_name} ({protocol})")
        lines.append("")
    
    lines.append("Выберите ключ для управления:")
    
    await send_function(
        "\n".join(lines),
        reply_markup=my_keys_list_kb(keys),
        parse_mode="Markdown"
    )


async def show_help(send_function):
    """
    Общая логика для показа справки.
    
    Args:
        send_function: Функция для отправки сообщения (message.answer или callback.message.edit_text)
    """
    from bot.keyboards.admin import home_only_kb
    from bot.keyboards.user import help_kb
    from database.requests import get_setting
    
    # Получаем текст справки из БД
    help_text = get_setting('help_page_text', "❓ *Справка*")
    
    # Получаем ссылки для кнопок
    news_link = get_setting('news_channel_link', 'https://t.me/YadrenoRu')
    support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
    
    await send_function(
        help_text,
        reply_markup=help_kb(news_link, support_link),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    """Показывает справку по кнопке."""
    # Пытаемся отредактировать (если текст)
    # Если это фото/файл (после замены/покупки/показа), edit_text упадёт.
    try:
        await show_help(callback.message.edit_text)
    except Exception:
        # Удаляем фото/файл и отправляем новое сообщение
        try:
            await callback.message.delete()
        except:
            pass
        await show_help(callback.message.answer)
    
    await callback.answer()


@router.callback_query(F.data == "my_keys")
async def my_keys_handler(callback: CallbackQuery):
    """Список VPN-ключей пользователя."""
    telegram_id = callback.from_user.id
    
    # Пытаемся отредактировать (если текст)
    # Если это фото/файл (после замены/покупки/показа), edit_text упадёт.
    try:
        await show_my_keys(telegram_id, callback.message.edit_text)
    except Exception:
        # Удаляем фото/файл и отправляем новое сообщение
        try:
            await callback.message.delete()
        except:
            pass
        await show_my_keys(telegram_id, callback.message.answer)
    
    await callback.answer()


@router.callback_query(F.data.startswith("key:"))
async def key_details_handler(callback: CallbackQuery):
    """Детальная информация о ключе с улучшенной статистикой."""
    from database.requests import get_key_details_for_user, get_key_payments_history
    from bot.keyboards.user import key_manage_kb
    from bot.keyboards.admin import home_only_kb
    from bot.services.vpn_api import get_client, format_traffic
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    # Статус
    if key['is_active']:
        status = "🟢 Активен"
    else:
        status = "🔴 Истёк"
    
    # Получаем детальную статистику по трафику
    traffic_info = "Загрузка..."
    protocol = "VLESS" # Дефолт
    inbound_name = "VPN"  # Дефолт
    
    if key.get('server_active') and key.get('panel_email'):
        try:
            client = await get_client(key['server_id'])
            stats = await client.get_client_stats(key['panel_email'])
            
            if stats:
                used_bytes = stats['up'] + stats['down']
                total_bytes = stats['total']
                
                used_str = format_traffic(used_bytes)
                total_str = format_traffic(total_bytes) if total_bytes > 0 else "Безлимит"
                
                # Вычисляем процент использования
                percent_str = ""
                if total_bytes > 0:
                    percent = (used_bytes / total_bytes) * 100
                    percent_str = f"({percent:.1f}%)"
                
                traffic_info = f"{used_str} из {total_str} {percent_str}"
                protocol = stats.get('protocol', 'vless').upper()
                inbound_name = stats.get('remark', 'VPN') or "VPN"
            else:
                traffic_info = "Нет данных"
        except Exception as e:
            logger.warning(f"Ошибка получения статистики: {e}")
            traffic_info = "Недоступно"
    else:
        traffic_info = "Сервер недоступен"

    # Формируем текст
    expires = key['expires_at'][:10] if key['expires_at'] else "—"
    server = key.get('server_name') or "Не выбран"
    
    lines = [
        f"🔑 *{key['display_name']}*\n",
        f"*Статус:* {status}",
        f"*Сервер:* {server}",
        f"*Протокол:* {inbound_name} ({protocol})",
        f"*Трафик:* {traffic_info}",
        f"*Действует до:* {expires}",
        ""
    ]
    
    # История платежей (Все платежи)
    payments = get_key_payments_history(key_id)
    if payments:
        lines.append("📜 *История операций:*")
        for p in payments:  # Показываем все
            date = p['paid_at'][:10] if p['paid_at'] else "—"
            tariff = p.get('tariff_name') or "Тариф"
            if p['payment_type'] == 'stars':
                amount = f"{p['amount_stars']} ⭐"
            else:
                amount = f"${p['amount_cents']/100:.2f}"
            lines.append(f"   • {date}: {tariff} ({amount})")
    
    msg_text = "\n".join(lines)
    
    # Пытаемся отредактировать сообщение. 
    # Если это было фото (после Show Key), edit_text вызовет ошибку.
    # В этом случае удаляем старое и отправляем новое.
    try:
        await callback.message.edit_text(
            msg_text,
            reply_markup=key_manage_kb(key_id),
            parse_mode="Markdown"
        )
    except Exception:
        # Если не получилось отредактировать (например, это фото)
        await callback.message.delete()
        await callback.message.answer(
            msg_text,
            reply_markup=key_manage_kb(key_id),
            parse_mode="Markdown"
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("key_show:"))
async def key_show_handler(callback: CallbackQuery):
    """Показать ключ для копирования (с QR и JSON)."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import key_show_kb
    from bot.utils.key_sender import send_key_with_qr
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    if not key['client_uuid']:
        await callback.message.edit_text(
            "📋 *Показать ключ*\n\n"
            "⚠️ Ключ ещё не создан на сервере.\n"
            "Обратитесь в поддержку.",
            reply_markup=key_show_kb(key_id),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Используем унифицированную отправку
    # Сначала пытаемся написать "⏳...", если не выйдет (напр. обновляем из файла) - просто шлем
    try:
        await callback.message.edit_text("⏳ Получение данных ключа...")
    except Exception:
        pass
        
    await send_key_with_qr(callback, key, key_show_kb(key_id))
    await callback.answer()


@router.callback_query(F.data.startswith("key_renew:"))
async def key_renew_select_payment(callback: CallbackQuery):
    """Выбор способа оплаты для продления (сразу, без тарифа)."""
    from database.requests import (
        get_all_tariffs, get_key_details_for_user, get_user_internal_id,
        is_crypto_configured, is_stars_enabled, get_setting,
        create_pending_order
    )
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    from bot.keyboards.user import renew_payment_method_kb, back_and_home_kb
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    # Проверяем принадлежность ключа
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    # Получаем методы оплаты
    crypto_configured = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    
    if not crypto_configured and not stars_enabled:
         await callback.message.edit_text(
            "💳 *Продление ключа*\n\n"
            "😔 Способы оплаты временно недоступны.\n"
            "Попробуйте позже.",
            reply_markup=back_and_home_kb(back_callback=f"key:{key_id}"),
            parse_mode="Markdown"
        )
         await callback.answer()
         return

    # Подготовка URL для крипты
    crypto_url = None
    if crypto_configured:
        # Для генерации ссылки нужен PENDING ORDER.
        # Создаём его с placeholder-тарифом (первым активным), т.к. реальный выберет пользователь в Ya.Seller
        tariffs = get_all_tariffs(include_hidden=False)
        if tariffs:
            placeholder_tariff = tariffs[0]
            user_id = get_user_internal_id(telegram_id)
            
            if user_id:
                 _, order_id = create_pending_order(
                    user_id=user_id,
                    tariff_id=placeholder_tariff['id'],
                    payment_type='crypto',
                    vpn_key_id=key_id
                )
                 
                 item_url = get_setting('crypto_item_url')
                 item_id = extract_item_id_from_url(item_url)
                 
                 if item_id:
                     crypto_url = build_crypto_payment_url(
                        item_id=item_id,
                        invoice_id=order_id,
                        tariff_external_id=None, # Не фиксируем тариф, юзер выберет сам
                        price_cents=None
                     )
    
    await callback.message.edit_text(
        f"💳 *Продление ключа*\n\n"
        f"🔑 Ключ: *{key['display_name']}*\n\n"
        "Выберите способ оплаты:",
        reply_markup=renew_payment_method_kb(key_id, crypto_url, stars_enabled),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ЗАМЕНА КЛЮЧА
# ============================================================================

@router.callback_query(F.data.startswith("key_replace:"))
async def key_replace_start_handler(callback: CallbackQuery, state: FSMContext):
    """Начало процедуры замены ключа."""
    from database.requests import get_key_details_for_user, get_active_servers
    from bot.services.vpn_api import get_client
    from bot.keyboards.user import replace_server_list_kb
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    # 1. Проверяем трафик (< 20% использовано)
    if key.get('server_active') and key.get('panel_email'):
        try:
            client = await get_client(key['server_id'])
            stats = await client.get_client_stats(key['panel_email'])
            
            if stats and stats['total'] > 0:
                used = stats['up'] + stats['down']
                percent = used / stats['total']
                
                if percent > 0.20:
                    await callback.answer(
                        f"⛔ Замена невозможна.\nИспользовано {percent*100:.1f}% трафика (макс. 20%).",
                        show_alert=True
                    )
                    return
            elif stats and stats['total'] == 0:
                 # Безлимит? Разрешаем замену
                 pass
        except Exception as e:
            logger.warning(f"Ошибка проверки трафика для замены: {e}")
            # Если ошибка (сервер лежит), можно ли менять?
            # Лучше разрешить, вдруг проблема в сервере и пользователь хочет уйти
            pass
    
    # 2. Показываем выбор сервера
    servers = get_active_servers()
    if not servers:
        await callback.answer("❌ Нет доступных серверов", show_alert=True)
        return
    
    await state.set_state(ReplaceKey.users_server)
    await state.update_data(replace_key_id=key_id)
    
    await callback.message.edit_text(
        "🔄 *Замена ключа*\n\n"
        "Вы можете пересоздать ключ на другом или том же сервере.\n"
        "Старый ключ будет удалён, но срок действия сохранится.\n\n"
        "Выберите сервер:",
        reply_markup=replace_server_list_kb(servers, key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(ReplaceKey.users_server, F.data.startswith("replace_server:"))
async def key_replace_server_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для замены."""
    from database.requests import get_server_by_id
    from bot.services.vpn_api import get_client, VPNAPIError
    from bot.keyboards.user import replace_inbound_list_kb
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await state.update_data(replace_server_id=server_id)
    
    # Получаем inbounds
    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        
        if not inbounds:
            await callback.answer("❌ На сервере нет доступных протоколов", show_alert=True)
            return
            
        data = await state.get_data()
        key_id = data.get('replace_key_id')
        
        await state.set_state(ReplaceKey.users_inbound)
        
        await callback.message.edit_text(
            f"🖥️ *Сервер:* {server['name']}\n\n"
            "Выберите протокол:",
            reply_markup=replace_inbound_list_kb(inbounds, key_id),
            parse_mode="Markdown"
        )
    except VPNAPIError as e:
        await callback.answer(f"❌ Ошибка подключения: {e}", show_alert=True)
    await callback.answer()


@router.callback_query(ReplaceKey.users_inbound, F.data.startswith("replace_inbound:"))
async def key_replace_inbound_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор inbound и подтверждение."""
    from database.requests import get_server_by_id, get_key_details_for_user
    from bot.keyboards.user import replace_confirm_kb
    
    inbound_id = int(callback.data.split(":")[1])
    await state.update_data(replace_inbound_id=inbound_id)
    
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    server_id = data.get('replace_server_id')
    
    key = get_key_details_for_user(key_id, callback.from_user.id)
    server = get_server_by_id(server_id)
    
    await state.set_state(ReplaceKey.confirm)
    
    await callback.message.edit_text(
        "⚠️ *Подтверждение замены*\n\n"
        f"Ключ: *{key['display_name']}*\n"
        f"Новый сервер: *{server['name']}*\n\n"
        "Старый ключ будет удалён и перестанет работать.\n"
        "Вам нужно будет обновить настройки в приложении.\n\n"
        "Вы уверены?",
        reply_markup=replace_confirm_kb(key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(ReplaceKey.confirm, F.data == "replace_confirm")
async def key_replace_execute(callback: CallbackQuery, state: FSMContext):
    """Выполнение замены ключа."""
    from database.requests import get_key_details_for_user, get_server_by_id, update_vpn_key_connection
    from bot.services.vpn_api import get_client, VPNAPIError
    from bot.handlers.admin.users import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    from config import DEFAULT_TOTAL_GB
    
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    new_server_id = data.get('replace_server_id')
    new_inbound_id = data.get('replace_inbound_id')
    
    telegram_id = callback.from_user.id
    current_key = get_key_details_for_user(key_id, telegram_id)
    new_server_data = get_server_by_id(new_server_id)
    
    if not current_key or not new_server_data:
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return
    
    await callback.message.edit_text("⏳ Выполняется замена ключа...")
    
    try:
        # 1. Удаляем старый ключ
        # Если замена на ТОМ ЖЕ сервере -> удаление должно быть строгим (иначе будут дубли)
        # Если замена на ДРУГОМ сервере -> если старый сервер лежит, это не должно мешать переезду.
        
        is_same_server = (current_key['server_id'] == new_server_id)
        
        if current_key.get('server_active') and current_key.get('panel_email'):
            try:
                old_client = await get_client(current_key['server_id'])
                await old_client.delete_client(current_key['panel_inbound_id'], current_key['client_uuid'])
                logger.info(f"Старый ключ {key_id} успешно удалён (uuid: {current_key['client_uuid']})")
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Ошибка удаления старого ключа {key_id}: {error_msg}")
                
                if is_same_server:
                    # Если тот же сервер, ошибка удаления критична, КРОМЕ случая "не найден"
                    # Обычно 3x-ui пишет что-то вроде "Client not found" или success: false
                    if "not found" in error_msg.lower() or "не найден" in error_msg.lower():
                         logger.info("Ключ не найден на сервере, считаем удаленным.")
                    else:
                        # Реальная ошибка (нет связи, авторизация и т.д.)
                        raise VPNAPIError(f"Не удалось удалить старый ключ: {error_msg}. Замена отменена во избежание дублей.")
                else:
                    # Разные серверы - игнорируем ошибку удаления (старый сервер может быть мертв)
                    pass
        
        # 2. Создаем новый ключ
        new_client = await get_client(new_server_id)
        
        # Генерируем новый email и UUID
        # Нужно передать user dict, у нас есть telegram_id и username из current_key
        user_fake_dict = {'telegram_id': telegram_id, 'username': current_key.get('username')}
        new_email = generate_unique_email(user_fake_dict)
        
        # Получаем параметры тарифа для лимитов
        # Используем глобальную настройку из конфига
        limit_gb = int(DEFAULT_TOTAL_GB / (1024**3))
        
        # Важно: Срок действия должен остаться прежним!
        # Вычисляем оставшиеся дни
        expires_at = datetime.fromisoformat(current_key['expires_at'])
        now = datetime.now()
        days_left = (expires_at - now).days
        if days_left < 0: days_left = 0
        
        # Создаем
        res = await new_client.add_client(
            inbound_id=new_inbound_id,
            email=new_email,
            total_gb=limit_gb,
            expire_days=days_left,
            limit_ip=1,
            enable=True,
            tg_id=str(telegram_id)
        )
        
        new_uuid = res['uuid']
        
        # 3. Обновляем в БД
        update_vpn_key_connection(
            key_id=key_id,
            server_id=new_server_id,
            panel_inbound_id=new_inbound_id,
            panel_email=new_email,
            client_uuid=new_uuid
        )
        
        await state.clear()
        
        # Получаем обновленные данные ключа для отправки
        updated_key = get_key_details_for_user(key_id, telegram_id)
        
        # Используем унифицированную отправку
        await send_key_with_qr(callback, updated_key, key_issued_kb(), is_new=True)
        
    except Exception as e:
        logger.error(f"Ошибка при замене ключа: {e}")
        # Если ошибка, но мы уже удалили старый ключ (на том же сервере)...
        # Это сложный кейс, но транзакционность между API и БД не гарантирована.
        await callback.message.edit_text(
            f"❌ Произошла ошибка при замене ключа: {e}\n\n"
            "Попробуйте позже или обратитесь в поддержку."
        )


@router.callback_query(F.data.startswith("key_rename:"))
async def key_rename_start_handler(callback: CallbackQuery, state: FSMContext):
    """Начало переименования ключа."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import cancel_kb
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    await state.set_state(RenameKey.waiting_for_name)
    await state.update_data(key_id=key_id)
    
    await callback.message.edit_text(
        f"✏️ *Переименование ключа*\n\n"
        f"Текущее имя: *{key['display_name']}*\n\n"
        "Введите новое название для ключа (макс. 30 символов):\n"
        "_(Отправьте любой текст)_",
        reply_markup=cancel_kb(cancel_callback=f"key:{key_id}"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(RenameKey.waiting_for_name)
async def key_rename_submit_handler(message: Message, state: FSMContext):
    """Обработка ввода нового имени ключа."""
    from database.requests import update_key_custom_name
    
    data = await state.get_data()
    key_id = data.get('key_id')
    new_name = message.text.strip()
    
    if not key_id:
        await state.clear()
        await message.answer("❌ Ошибка состояния. Попробуйте снова.")
        return
        
    if len(new_name) > 30:
        await message.answer("⚠️ Имя слишком длинное (макс. 30 символов). Попробуйте короче.")
        return
    
    # Обновляем имя
    success = update_key_custom_name(key_id, message.from_user.id, new_name)
    
    if success:
        await message.answer(f"✅ Ключ переименован в *{new_name}*", parse_mode="Markdown")
    else:
        await message.answer("❌ Не удалось переименовать ключ.", parse_mode="Markdown")
        
    # Возвращаем пользователя к ключу
    # Имитируем нажатие кнопки (но через отправку сообщения)
    # Т.к. message нельзя редактировать в callback-стиле так же красиво, мы просто пришлем детали
    
    # Но лучше, для UX, просто очистить стейт и показать ключ снова
    await state.clear()
    
    # Вызываем логику показа ключа (дублируем логику, т.к. хендлер ждет callback)
    # ПРОЩЕ: Сформировать новый CallbackQuery и вызвать хендлер - но это хак.
    # ЛУЧШЕ: Вынести логику показа в отдельную функцию -> Refactoring
    # НО "Quick fix style":
    from database.requests import get_key_details_for_user, get_key_payments_history
    from bot.keyboards.user import key_manage_kb
    
    key = get_key_details_for_user(key_id, message.from_user.id)
    if not key:
        return

    # Статус
    if key['is_active']:
        status = "🟢 Активен"
    else:
        status = "🔴 Истёк"
    
    expires = key['expires_at'][:10] if key['expires_at'] else "—"
    server = key.get('server_name') or "Не выбран"
    
    lines = [
        f"🔑 *{key['display_name']}*\n",
        f"*Статус:* {status}",
        f"*Сервер:* {server}",
        f"*Действует до:* {expires}",
        ""
    ]
    
    await message.answer(
        "\n".join(lines),
        reply_markup=key_manage_kb(key_id),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "buy_key")
async def buy_key_handler(callback: CallbackQuery):
    """Страница «Купить ключ» с условиями и способами оплаты."""
    from database.requests import (
        is_crypto_configured, is_stars_enabled, get_setting, 
        get_user_internal_id, get_all_tariffs, create_pending_order
    )
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    from bot.keyboards.user import buy_key_kb
    from bot.keyboards.admin import home_only_kb
    
    telegram_id = callback.from_user.id
    
    # Проверяем какие методы оплаты доступны
    crypto_url = None
    if is_crypto_configured():
        # Для крипто-оплаты создаём pending order с первым активным тарифом
        # (или можно использовать специальный placeholder тариф)
        user_id = get_user_internal_id(telegram_id)
        if user_id:
            # Получаем первый активный тариф (для генерации ссылки)
            # Примечание: реальный тариф выбирается пользователем в Ya.Seller
            tariffs = get_all_tariffs(include_hidden=False)
            if tariffs:
                first_tariff = tariffs[0]
                
                # Создаём pending order
                _, order_id = create_pending_order(
                    user_id=user_id,
                    tariff_id=first_tariff['id'],
                    payment_type='crypto',
                    vpn_key_id=None  # Новый ключ
                )
                
                # Формируем ссылку с invoice
                crypto_item_url = get_setting('crypto_item_url')
                item_id = extract_item_id_from_url(crypto_item_url)
                
                if item_id:
                    crypto_url = build_crypto_payment_url(
                        item_id=item_id,
                        invoice_id=order_id,
                        tariff_external_id=None,  # Пользователь выберет в боте
                        price_cents=None  # Цена определяется в Ya.Seller
                    )
    
    stars_enabled = is_stars_enabled()
    
    # Если нет ни одного метода оплаты — показываем заглушку
    if not crypto_url and not stars_enabled:
        await callback.message.edit_text(
            "💳 *Купить ключ*\n\n"
            "😔 К сожалению, сейчас оплата недоступна.\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Формируем текст с условиями
    text = """💳 *Купить ключ*

🔐 *Что вы получаете:*
• Доступ к нескольким серверам и протоколам
• 1 ключ = 1 устройство (одновременное подключение)
• Лимит трафика: до 1 ТБ в месяц (сброс каждые 30 дней)

⚠️ *Важно знать:*
• Средства не возвращаются — услуга считается оказанной в момент получения ключа
• Мы не даём никаких гарантий бесперебойной работы сервиса в будущем
• Мы не можем гарантировать, что данная технология обхода блокировок останется доступной в вашей стране

_Приобретая ключ, вы соглашаетесь с этими условиями._

Выберите способ оплаты:"""
    
    await callback.message.edit_text(
        text,
        reply_markup=buy_key_kb(crypto_url=crypto_url, stars_enabled=stars_enabled),
        parse_mode="Markdown"
    )
    await callback.answer()



@router.callback_query(F.data == "help")
async def help_stub(callback: CallbackQuery):
    """Раздел справки."""
    # Вызываем общую логику с обработкой ошибок (если текущее сообщение - фото/файл)
    try:
        await show_help(callback.message.edit_text)
    except Exception:
        # Если это фото/файл, удаляем и присылаем новое
        try:
            await callback.message.delete()
        except:
            pass
        await show_help(callback.message.answer)
        
    await callback.answer()



# ============================================================================
# ОПЛАТА STARS
# ============================================================================

@router.callback_query(F.data == "pay_stars")
async def pay_stars_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Stars."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    # Получаем активные тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
        await callback.message.edit_text(
            "⭐ *Оплата звёздами*\n\n"
            "😔 Нет доступных тарифов.\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "⭐ *Оплата звёздами*\n\n"
        "Выберите тариф:",
        reply_markup=tariff_select_kb(tariffs),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stars_pay:"))
async def pay_stars_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Stars."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id
    
    # Получаем ID тарифа из callback
    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    # Форматируем длительность для описания
    days = tariff['duration_days']
    if days >= 365:
        duration = f"{days // 365} год" if days // 365 == 1 else f"{days // 365} года"
    elif days >= 30:
        months = days // 30
        if months == 1:
            duration = "1 месяц"
        elif months in [2, 3, 4]:
            duration = f"{months} месяца"
        else:
            duration = f"{months} месяцев"
    else:
        duration = f"{days} дней"
    
    # Создаем pending order (Единый механизм)
    from database.requests import get_user_internal_id, create_pending_order
    
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Ошибка пользователя", show_alert=True)
        return

    # Создаем заказ для нового ключа (vpn_key_id=None)
    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='stars',
        vpn_key_id=None 
    )

    # Отправляем инвойс c order_id в payload
    await callback.message.answer_invoice(
        title=f"VPN ключ на {duration}",
        description=f"Доступ к VPN-сервису на {duration}. 1 ключ = 1 устройство.",
        payload=order_id, # Просто order_id, как и в крипте (или можно stars:order_id)
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label=f"VPN {duration}", amount=tariff['price_stars'])],
    )
    
    # Удаляем предыдущее сообщение с выбором тарифа
    await callback.message.delete()
    await callback.answer()
