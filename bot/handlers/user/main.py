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
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError

from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_all_servers
from bot.keyboards.user import main_menu_kb
from bot.states.user_states import RenameKey, ReplaceKey
from bot.utils.text import escape_md

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# КОМАНДА /START
# ============================================================================

def get_welcome_text(is_admin: bool = False) -> str:
    """Формирует приветственный текст с реальными тарифами из БД."""
    from database.requests import (
        get_all_tariffs, get_setting, 
        is_crypto_configured, is_stars_enabled, is_cards_enabled
    )
    from bot.utils.text import escape_md2
    
    # 1. Получаем статический текст из БД (уже в формате MarkdownV2)
    welcome_text = get_setting('main_page_text', "🔐 *Добро пожаловать в VPN\\-бот\\!*")
    
    # Получаем настройки оплат
    crypto_enabled = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    
    # 2. Получаем тарифы из БД (только активные)
    tariffs = get_all_tariffs()
    
    tariff_lines = []
    if tariffs:
        tariff_lines.append("📋 *Тарифы:*")
        for tariff in tariffs:
            prices = []
            
            if crypto_enabled:
                price_usd = tariff['price_cents'] / 100
                price_str = f"{price_usd:g}".replace('.', ',')
                prices.append(f"${escape_md2(price_str)}")
                
            if stars_enabled:
                prices.append(f"{tariff['price_stars']} ⭐")
                
            if cards_enabled and tariff.get('price_rub', 0) > 0:
                prices.append(f"{int(tariff['price_rub'])} ₽")
            
            # Если нет доступных методов оплаты (или все выключены),
            # все равно выведем название тарифа, но без цены (или оставим как есть)
            price_display = " \\/ ".join(prices) if prices else "Цена не установлена"
            
            # Экранируем название
            tariff_lines.append(f"• {escape_md2(tariff['name'])} — {price_display}")
            
    tariff_text = "\n".join(tariff_lines)

    # 3. Нормализуем текст: приводим к единственному виду с %тарифы%
    # %без_тарифов% — полностью отключает вывод тарифов (тег удаляется, тарифы не добавляются)
    # %тарифы%      — вставляет список тарифов в указанное место
    # без тегов     — список тарифов автоматически добавляется в конец
    if "%без_тарифов%" in welcome_text:
        return welcome_text.replace("%без_тарифов%", "")

    if "%тарифы%" not in welcome_text:
        welcome_text = f"{welcome_text}\n\n%тарифы%"

    return welcome_text.replace("%тарифы%", tariff_text)


@router.message(Command("start"), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    username = message.from_user.username
    
    logger.info(f"CMD_START: User {user_id} started bot")

    # Сбрасываем любое активное FSM-состояние (важно: до проверок)
    await state.clear()

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
    
    # Проверяем админа
    is_admin = user_id in ADMIN_IDS
    
    text = get_welcome_text(is_admin)
    
    # Проверяем аргументы запуска (deep linking)
    args = command.args
    if args and args.startswith("bill"):
        from bot.services.billing import process_crypto_payment
        from bot.handlers.user.payments import finalize_payment_ui
        
        # Обрабатываем платеж (вернет (success, text, order))
        try:
            success, text, order = process_crypto_payment(args, user_id=user['id'])
            
            if success and order:
                # Используем единый финализатор UI
                await finalize_payment_ui(message, state, text, order)
            else:
                 # Обычная ошибка (возвращенная текстом)
                 await message.answer(text, parse_mode="Markdown")
                 
        except Exception as e:
            # Проверяем, является ли это нашей ошибкой
            from bot.errors import TariffNotFoundError
            
            if isinstance(e, TariffNotFoundError):
                 from bot.database.requests import get_setting
                 from bot.keyboards.user import support_kb
                 
                 support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                 await message.answer(str(e), reply_markup=support_kb(support_link), parse_mode="Markdown")
            else:
                # Неизвестная ошибка
                logger.exception(f"Ошибка обработки платежа: {e}")
                await message.answer("❌ Произошла ошибка при обработке платежа.", parse_mode="Markdown")
        
        return

    # Вычисляем, показывать ли кнопку пробной подписки
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial
    show_trial = (
        is_trial_enabled() and
        get_trial_tariff_id() is not None and
        not has_used_trial(user_id)
    )

    try:
        await message.answer(
            text,
            reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial),
            parse_mode="MarkdownV2"
        )
    except TelegramForbiddenError:
        logger.warning(f"User {user_id} blocked the bot during /start")
    except Exception as e:
        logger.error(f"Error sending start message to {user_id}: {e}")



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

    # Вычисляем, показывать ли кнопку пробной подписки
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial
    show_trial = (
        is_trial_enabled() and
        get_trial_tariff_id() is not None and
        not has_used_trial(user_id)
    )
    
    # Пытаемся отредактировать сообщение (если текст)
    # Если это фото/файл (после выдачи ключа), edit_text упадёт.
    try:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial),
            parse_mode="MarkdownV2"
        )
    except Exception:
        # Удаляем фото/файл и отправляем новое сообщение
        try:
            await callback.message.delete()
        except:
            pass
        await callback.message.answer(
            text,
            reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial),
            parse_mode="MarkdownV2"
        )

    await callback.answer()


# ============================================================================
# ПРОБНАЯ ПОДПИСКА
# ============================================================================

@router.callback_query(F.data == "trial_subscription")
async def show_trial_subscription(callback: CallbackQuery):
    """Показывает страницу пробной подписки."""
    from database.requests import (
        is_trial_enabled, get_trial_tariff_id, has_used_trial, get_setting
    )
    from bot.keyboards.user import trial_sub_kb
    from bot.keyboards.admin import home_only_kb

    user_id = callback.from_user.id

    # Повторная проверка условий
    if not is_trial_enabled():
        await callback.answer("❌ Пробная подписка недоступна", show_alert=True)
        return

    if get_trial_tariff_id() is None:
        await callback.answer("❌ Тариф не настроен", show_alert=True)
        return

    if has_used_trial(user_id):
        await callback.answer("ℹ️ Вы уже использовали пробный период", show_alert=True)
        return

    # Получаем текст страницы из настроек
    trial_text = get_setting('trial_page_text', '🎁 *Пробная подписка*')

    await callback.message.edit_text(
        trial_text,
        reply_markup=trial_sub_kb(),
        parse_mode="MarkdownV2"
    )
    await callback.answer()


@router.callback_query(F.data == "trial_activate")
async def activate_trial_subscription(callback: CallbackQuery, state: FSMContext):
    """Активирует пробную подписку: создаёт ключ через стандартный механизм."""
    from database.requests import (
        is_trial_enabled, get_trial_tariff_id, has_used_trial, get_tariff_by_id,
        get_or_create_user, mark_trial_used, create_initial_vpn_key,
        create_pending_order, complete_order
    )
    from bot.handlers.user.payments import start_new_key_config
    from bot.keyboards.admin import home_only_kb

    user_id = callback.from_user.id

    # Повторная проверка (защита от повторных активаций)
    if not is_trial_enabled():
        await callback.answer("❌ Пробная подписка недоступна", show_alert=True)
        return

    tariff_id = get_trial_tariff_id()
    if tariff_id is None:
        await callback.answer("❌ Тариф не настроен", show_alert=True)
        return

    if has_used_trial(user_id):
        await callback.answer("ℹ️ Вы уже использовали пробный период", show_alert=True)
        return

    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    # Получаем внутренний ID пользователя
    user = get_or_create_user(user_id, callback.from_user.username)
    internal_user_id = user['id']

    # Ставим флаг пробного периода
    mark_trial_used(internal_user_id)
    logger.info(
        f"Пользователь {user_id} активировал пробный период (tарифf ID={tariff_id})"
    )

    # Создаём ключ в БД (черновик — без сервера)
    duration_days = tariff['duration_days']
    key_id = create_initial_vpn_key(internal_user_id, tariff_id, duration_days)

    # Создаём РЕАЛЬНЫЙ ордер в таблице payments (триал = бесплатно, сразу paid)
    _, order_id = create_pending_order(
        user_id=internal_user_id,
        tariff_id=tariff_id,
        payment_type='trial',
        vpn_key_id=key_id
    )
    complete_order(order_id)  # Помечаем как оплаченный сразу

    await state.update_data(new_key_order_id=order_id, new_key_id=key_id)

    # Удаляем текущее сообщение и запускаем выбор сервера
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

    await start_new_key_config(callback.message, state, order_id, key_id)



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
        lines.append(f"{status_emoji}*{escape_md(key['display_name'])}* - {traffic_text} - до {expires}")
        lines.append(f"     📍{escape_md(server)} - {escape_md(inbound_name)} ({escape_md(protocol)})")
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
    # Валидируем URL, чтобы избежать ошибки "URL host is empty"
    default_news = 'https://t.me/YadrenoRu'
    default_support = 'https://t.me/YadrenoChat'
    
    news_link = get_setting('news_channel_link', default_news)
    support_link = get_setting('support_channel_link', default_support)
    
    # Если ссылка не является валидным URL — используем дефолт
    if not news_link or not news_link.startswith(('http://', 'https://')):
        news_link = default_news
    if not support_link or not support_link.startswith(('http://', 'https://')):
        support_link = default_support
    
    # Ошибки Markdown парсинга обрабатываются глобально в SafeParseSession
    await send_function(
        help_text,
        reply_markup=help_kb(news_link, support_link),
        parse_mode="MarkdownV2"
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


async def show_key_details(telegram_id: int, key_id: int, send_function, prepend_text: str = ""):
    """Общая логика для показа деталей ключа."""
    from database.requests import get_key_details_for_user, get_key_payments_history
    from bot.keyboards.user import key_manage_kb
    from bot.services.vpn_api import get_client, format_traffic
    import logging
    
    logger = logging.getLogger(__name__)
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await send_function("❌ Ключ не найден")
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
    
    # Инициализация переменных отображения
    inbound_name = "—"
    protocol = "—"
    is_unconfigured = not key.get('server_id')

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
        if is_unconfigured:
            traffic_info = "⚠️ Требует настройки"
        else:
            traffic_info = "Сервер недоступен"

    # Формируем текст
    expires = key['expires_at'][:10] if key['expires_at'] else "—"
    server = key.get('server_name') or "Не выбран"
    
    lines = []
    if prepend_text:
        lines.append(prepend_text)
        lines.append("")
        
    lines.extend([
        f"🔑 *{escape_md(key['display_name'])}*\n",
        f"*Статус:* {status}",
        f"*Сервер:* {escape_md(server)}",
        f"*Протокол:* {escape_md(inbound_name)} ({escape_md(protocol)})",
        f"*Трафик:* {traffic_info}",
        f"*Действует до:* {expires}",
        ""
    ])
    
    # История платежей (Все платежи)
    payments = get_key_payments_history(key_id)
    if payments:
        lines.append("📜 *История операций:*")
        for p in payments:  # Показываем все
            date = p['paid_at'][:10] if p['paid_at'] else "—"
            tariff = escape_md(p.get('tariff_name') or "Тариф")
            amount_val = p['amount_cents']/100
            amount_str = f"{amount_val:g}".replace('.', ',')
            if p['payment_type'] == 'stars':
                amount = f"{p['amount_stars']} ⭐"
            else:
                amount = f"${amount_str}"
            lines.append(f"   • {date}: {tariff} ({amount})")
    
    msg_text = "\n".join(lines)
    
    await send_function(
        msg_text,
        reply_markup=key_manage_kb(key_id, is_unconfigured=is_unconfigured),
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("key:"))
async def key_details_handler(callback: CallbackQuery):
    """Детальная информация о ключе с улучшенной статистикой."""
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    
    # Пытаемся отредактировать сообщение. 
    # Если это было фото (после Show Key), edit_text вызовет ошибку.
    # В этом случае удаляем старое и отправляем новое.
    try:
        await show_key_details(telegram_id, key_id, callback.message.edit_text)
    except Exception:
        # Если не получилось отредактировать (например, это фото)
        await callback.message.delete()
        await show_key_details(telegram_id, key_id, callback.message.answer)
    
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
        is_crypto_configured, is_stars_enabled, is_cards_enabled, get_setting,
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
    cards_enabled = is_cards_enabled()
    
    if not crypto_configured and not stars_enabled and not cards_enabled:
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
        reply_markup=renew_payment_method_kb(key_id, crypto_url, stars_enabled, cards_enabled),
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
    
    # 0. Проверяем, активен ли ключ
    if not key['is_active']:
        await callback.answer(
            "⏳ Срок действия ключа истёк.\nПродлите его перед заменой.",
            show_alert=True
        )
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
        
        if current_key.get('server_id') and current_key.get('server_active') and current_key.get('panel_email'):
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
        delta = expires_at - now
        
        # Округляем в большую сторону (любой остаток времени считается за день)
        days_left = delta.days
        if delta.seconds > 0:
            days_left += 1
            
        # Страховка от 0 дней (API требует > 0)
        if days_left < 1: 
            days_left = 1
        
        # Создаем
        flow = await new_client.get_inbound_flow(new_inbound_id)
        
        res = await new_client.add_client(
            inbound_id=new_inbound_id,
            email=new_email,
            total_gb=limit_gb,
            expire_days=days_left,
            limit_ip=1,
            enable=True,
            tg_id=str(telegram_id),
            flow=flow
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
        is_crypto_configured, is_stars_enabled, is_cards_enabled, get_setting,
        get_user_internal_id, get_all_tariffs, create_pending_order
    )
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    from bot.keyboards.user import buy_key_kb
    from bot.keyboards.admin import home_only_kb
    
    telegram_id = callback.from_user.id
    
    # Проверяем какие методы оплаты доступны
    # Проверяем какие методы оплаты доступны
    crypto_url = None
    existing_order_id = None  # Сохраняем ID ордера для переиспользования в Stars

    if is_crypto_configured():
        # Для крипто-оплаты создаём pending order с первым активным тарифом
        # (или можно использовать специальный placeholder тариф)
        user_id = get_user_internal_id(telegram_id)
        if user_id:
            # Создаём pending order без конкретного тарифа
            # (тарифа и сумма определятся при оплате в CryptoBot)
            _, order_id = create_pending_order(
                user_id=user_id,
                tariff_id=None,
                payment_type=None,
                vpn_key_id=None  # Новый ключ
            )
            existing_order_id = order_id
            
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
    cards_enabled = is_cards_enabled()
    
    # Если нет ни одного метода оплаты — показываем заглушку
    if not crypto_url and not stars_enabled and not cards_enabled:
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
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=buy_key_kb(crypto_url=crypto_url, stars_enabled=stars_enabled, cards_enabled=cards_enabled, order_id=existing_order_id),
            parse_mode="Markdown"
        )
    except Exception:
        try:
            await callback.message.delete()
        except:
            pass
        await callback.message.answer(
            text,
            reply_markup=buy_key_kb(crypto_url=crypto_url, stars_enabled=stars_enabled, cards_enabled=cards_enabled, order_id=existing_order_id),
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

@router.callback_query(F.data.startswith("pay_stars"))
async def pay_stars_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Stars."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    # Парсим order_id из callback (pay_stars:ORDER_ID или просто pay_stars)
    order_id = None
    if ":" in callback.data:
        order_id = callback.data.split(":")[1]

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
        reply_markup=tariff_select_kb(tariffs, order_id=order_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stars_pay:"))
async def pay_stars_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Stars."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, update_order_tariff, update_payment_type
    
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    tariff = get_tariff_by_id(tariff_id)
    
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    days = tariff['duration_days']
    
    # Логика создания/обновления ордера
    from database.requests import get_user_internal_id, create_pending_order
    
    if order_id:
        # Переиспользуем существующий ордер (меняем тариф и тип оплаты)
        update_order_tariff(order_id, tariff_id, payment_type='stars')
        
    else:
        # Новая логика (если вдруг старый callback или прямой вызов)
        user_id = get_user_internal_id(callback.from_user.id)
        if not user_id:
            await callback.answer("❌ Ошибка пользователя", show_alert=True)
            return

        _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='stars',
            vpn_key_id=None 
        )

    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        price_stars = tariff['price_stars']

        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).",
            payload=order_id, # Просто order_id, как и в крипте
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_stars)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"⭐️ Оплатить {price_stars} XTR", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data="buy_key")
            ).as_markup()
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Ошибка при выставлении счета Stars: {e}")
        await callback.answer("❌ Произошла ошибка при создании счета", show_alert=True)
        return
    
    # Удаляем предыдущее сообщение с выбором тарифа
    await callback.message.delete()
    await callback.answer()
