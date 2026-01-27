"""
Обработчики раздела «Пользователи» в админ-панели.

Функционал:
- Статистика пользователей по фильтрам
- Список пользователей с пагинацией
- Выбор пользователя (ввод ID / контакты)
- Управление пользователем (просмотр, бан)
- Управление VPN-ключами (продление, сброс трафика)
- Добавление ключа администратором
"""
import logging
import uuid
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, 
    KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers,
    UsersShared
)
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_users_stats, get_all_users_paginated, get_user_by_telegram_id,
    toggle_user_ban, get_user_vpn_keys, get_user_payments_stats,
    get_vpn_key_by_id, extend_vpn_key, create_vpn_key_admin,
    get_active_servers, get_all_tariffs
)
from bot.utils.admin import is_admin
from bot.utils.text import escape_md
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import (
    users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb,
    key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb,
    add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb,
    back_and_home_kb, home_only_kb
)
from bot.services.vpn_api import (
    get_client_from_server_data, VPNAPIError, format_traffic
)

logger = logging.getLogger(__name__)

router = Router()

# Количество пользователей на странице
USERS_PER_PAGE = 20


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================




def format_user_display(user: dict) -> str:
    """Форматирует имя пользователя для отображения."""
    if user.get('username'):
        return f"@{user['username']}"
    return f"ID: {user['telegram_id']}"


def generate_unique_email(user: dict) -> str:
    """
    Генерирует уникальный email для панели 3X-UI.
    Формат: user_{username/id}_{random_suffix}
    """
    base = f"user_{user['username']}" if user.get('username') else f"user_{user['telegram_id']}"
    suffix = uuid.uuid4().hex[:5]
    return f"{base}_{suffix}"




@router.callback_query(F.data == "admin_users")
async def show_users_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает главный экран раздела пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.users_menu)
    await state.update_data(users_filter='all', users_page=0)
    
    # Получаем статистику
    stats = get_users_stats()
    
    # Формируем текст
    text = (
        "👥 *Пользователи*\n\n"
        "📊 *Статистика:*\n"
        f"👤 Всего: *{stats['total']}*\n"
        f"✅ С активными ключами: *{stats['active']}*\n"
        f"❌ Без активных ключей: *{stats['inactive']}*\n"
        f"🆕 Никогда не покупали: *{stats['never_paid']}*\n"
        f"🚫 Ключ истёк: *{stats['expired']}*\n\n"
        "Отправьте `telegram_id` пользователя или нажмите кнопку ниже."
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=users_menu_kb(stats),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# ============================================================================

@router.callback_query(F.data == "admin_users_list")
async def show_users_list(callback: CallbackQuery, state: FSMContext):
    """Показывает список пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.users_list)
    
    # Получаем текущий фильтр и страницу
    data = await state.get_data()
    current_filter = data.get('users_filter', 'all')
    page = data.get('users_page', 0)
    
    await _show_users_page(callback, state, page, current_filter)


@router.callback_query(F.data.startswith("admin_users_filter:"))
async def set_users_filter(callback: CallbackQuery, state: FSMContext):
    """Устанавливает фильтр пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    filter_type = callback.data.split(":")[1]
    await state.update_data(users_filter=filter_type, users_page=0)
    
    await _show_users_page(callback, state, 0, filter_type)


@router.callback_query(F.data.startswith("admin_users_page:"))
async def change_users_page(callback: CallbackQuery, state: FSMContext):
    """Переход на другую страницу списка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    current_filter = data.get('users_filter', 'all')
    
    await state.update_data(users_page=page)
    await _show_users_page(callback, state, page, current_filter)


async def _show_users_page(
    callback: CallbackQuery, 
    state: FSMContext, 
    page: int, 
    filter_type: str
):
    """Отображает страницу списка пользователей."""
    offset = page * USERS_PER_PAGE
    users, total = get_all_users_paginated(offset, USERS_PER_PAGE, filter_type)
    
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    
    # Формируем текст
    from bot.keyboards.admin import USERS_FILTERS
    filter_name = USERS_FILTERS.get(filter_type, filter_type)
    
    if users:
        text = (
            f"👥 *Пользователи* — {filter_name}\n\n"
            f"Показано: {len(users)} из {total}\n"
            f"Страница {page + 1} из {total_pages}"
        )
    else:
        text = (
            f"👥 *Пользователи* — {filter_name}\n\n"
            "😕 Пользователей не найдено"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=users_list_kb(users, page, total_pages, filter_type),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ВЫБОР ПОЛЬЗОВАТЕЛЯ
# ============================================================================

@router.callback_query(F.data == "admin_users_select")
async def request_user_selection(callback: CallbackQuery, state: FSMContext):
    """Запрос поиска пользователя (по ID, @username или через контакты)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_user_id)
    
    # Reply-клавиатура только с кнопкой выбора контакта
    reply_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="👤 Выбрать пользователя",
                request_users=KeyboardButtonRequestUsers(
                    request_id=1,
                    user_is_bot=False,
                    max_quantity=1
                )
            )]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    # Сначала отправляем новое сообщение с Reply-клавиатурой
    await callback.message.answer(
        "🔍 *Поиск пользователя*\n\n"
        "Отправьте:\n"
        "• telegram\\_id (число)\n"
        "• @username\n"
        "• Или нажмите кнопку «👤 Выбрать пользователя» ниже",
        reply_markup=reply_keyboard,
        parse_mode="Markdown"
    )
    
    # Редактируем старое сообщение, убирая кнопки и добавляя inline-отмену
    await callback.message.edit_text(
        callback.message.text,
        reply_markup=users_input_cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.waiting_user_id, F.users_shared)
async def handle_users_shared(message: Message, state: FSMContext):
    """Обработка выбранного пользователя через KeyboardButtonRequestUsers."""
    if not is_admin(message.from_user.id):
        return
    
    users_shared: UsersShared = message.users_shared
    if users_shared.users:
        telegram_id = users_shared.users[0].user_id
        
        # Убираем Reply-клавиатуру
        await message.answer(
            "✅ Пользователь выбран!",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Показываем пользователя
        await _show_user_view(message, state, telegram_id)


@router.message(AdminStates.waiting_user_id, F.text)
async def process_user_search_input(message: Message, state: FSMContext):
    """Обработка ввода telegram_id или @username."""
    if not is_admin(message.from_user.id):
        return
    
    from database.requests import get_user_by_username
    
    text = message.text.strip()
    user = None
    
    # Проверяем: число (telegram_id) или @username
    if text.isdigit():
        # Это telegram_id
        telegram_id = int(text)
        user = get_user_by_telegram_id(telegram_id)
        
        if not user:
            await message.answer(
                f"❌ Пользователь с ID `{telegram_id}` не найден в базе",
                reply_markup=users_input_cancel_kb(),
                parse_mode="Markdown"
            )
            return
    elif text.startswith('@') or text.replace('_', '').isalnum():
        # Это username
        username = text.lstrip('@')
        user = get_user_by_username(username)
        
        if not user:
            await message.answer(
                f"❌ Пользователь @{username} не найден в базе",
                reply_markup=users_input_cancel_kb(),
                parse_mode="Markdown"
            )
            return
    else:
        await message.answer(
            "❌ Введите telegram\\_id (число) или @username",
            reply_markup=users_input_cancel_kb(),
            parse_mode="Markdown"
        )
        return
    
    # Убираем Reply-клавиатуру и показываем пользователя
    await message.answer(
        "✅ Найден!",
        reply_markup=ReplyKeyboardRemove()
    )
    
    await _show_user_view(message, state, user['telegram_id'])


# ============================================================================
# ПРОСМОТР ПОЛЬЗОВАТЕЛЯ
# ============================================================================

@router.callback_query(F.data.startswith("admin_user_view:"))
async def show_user_view_callback(callback: CallbackQuery, state: FSMContext):
    """Показывает карточку пользователя (из callback)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    await _show_user_view_edit(callback, state, telegram_id)


async def _show_user_view(message: Message, state: FSMContext, telegram_id: int):
    """Показывает карточку пользователя (новое сообщение)."""
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await message.answer(
            f"❌ Пользователь с ID {telegram_id} не найден",
            reply_markup=home_only_kb()
        )
        return
    
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=telegram_id)
    
    text, keyboard = _format_user_card(user)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_user_view_edit(callback: CallbackQuery, state: FSMContext, telegram_id: int):
    """Показывает карточку пользователя (редактирование сообщения)."""
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.user_view)
    await state.update_data(current_user_telegram_id=telegram_id)
    
    text, keyboard = _format_user_card(user)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


def _format_user_card(user: dict) -> tuple[str, any]:
    """Форматирует карточку пользователя."""
    telegram_id = user['telegram_id']
    username = user.get('username')
    is_banned = bool(user.get('is_banned'))
    created_at = user.get('created_at', 'неизвестно')
    
    # Заголовок
    if is_banned:
        header = f"🚫 *ЗАБАНЕН* — `{format_user_display(user)}`"
    else:
        header = f"👤 *{format_user_display(user)}*"
    
    # Базовая инфо
    lines = [
        header,
        "",
        f"📱 Telegram ID: `{telegram_id}`",
    ]
    
    if username:
        lines.append(f"👤 Username: @{username}")
    else:
        lines.append("👤 Username: _не указан_")
    
    lines.append(f"📅 Зарегистрирован: {created_at}")
    
    # VPN-ключи
    vpn_keys = get_user_vpn_keys(user['id'])
    lines.append("")
    
    if vpn_keys:
        lines.append(f"🔑 *VPN-ключи ({len(vpn_keys)}):*")
        for key in vpn_keys:
            # Формируем название ключа согласно ТЗ
            if key.get('custom_name'):
                key_name = key['custom_name']
            else:
                # Формат: первые_4_символа...последние_4_символа от client_uuid
                uuid = key.get('client_uuid') or ''
                if len(uuid) >= 8:
                    key_name = f"{uuid[:4]}...{uuid[-4:]}"
                else:
                    key_name = uuid or f"Ключ #{key['id']}"
            
            expires = key.get('expires_at', '?')
            # Проверяем истёк ли ключ
            try:
                expires_dt = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                if expires_dt < datetime.now(expires_dt.tzinfo if expires_dt.tzinfo else None):
                    status = "🔴"
                else:
                    status = "🟢"
            except:
                status = "🔑"
            
            lines.append(f"  {status} `{key_name}` (до {expires})")
    else:
        lines.append("🔑 _VPN-ключей нет_")
    
    # Статистика оплат
    payment_stats = get_user_payments_stats(user['id'])
    lines.append("")
    lines.append("💳 *Оплаты:*")
    
    total_payments = payment_stats.get('total_payments', 0)
    if total_payments > 0:
        total_usd = payment_stats.get('total_amount_cents', 0) / 100
        total_stars = payment_stats.get('total_amount_stars', 0)
        last_payment = payment_stats.get('last_payment_at', '?')
        tariffs = payment_stats.get('tariffs', [])
        
        lines.append(f"  📊 Всего платежей: {total_payments}")
        if total_usd > 0:
            lines.append(f"  💰 Сумма (крипто): ${total_usd:.2f}")
        if total_stars > 0:
            lines.append(f"  ⭐ Сумма (Stars): {total_stars}")
        lines.append(f"  📅 Последняя оплата: {last_payment}")
        if tariffs:
            # Экранируем спецсимволы Markdown в названиях тарифов
            safe_tariffs = [escape_md(t) for t in tariffs]
            lines.append(f"  📋 Тарифы: {', '.join(safe_tariffs)}")
    else:
        lines.append("  _Оплат не было_")
    
    text = "\n".join(lines)
    keyboard = user_view_kb(telegram_id, vpn_keys, is_banned)
    
    return text, keyboard


# ============================================================================
# БАН / РАЗБАН
# ============================================================================

@router.callback_query(F.data.startswith("admin_user_toggle_ban:"))
async def request_ban_confirmation(callback: CallbackQuery, state: FSMContext):
    """Запрос подтверждения бана/разбана."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    is_banned = bool(user.get('is_banned'))
    
    if is_banned:
        action = "разблокировать"
    else:
        action = "заблокировать"
    
    text = (
        f"⚠️ *Подтверждение*\n\n"
        f"Вы уверены, что хотите *{action}* пользователя `{format_user_display(user)}`?"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=user_ban_confirm_kb(telegram_id, is_banned),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_ban_confirm:"))
async def confirm_ban_toggle(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и выполнение бана/разбана."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    new_status = toggle_user_ban(telegram_id)
    
    if new_status is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    if new_status:
        await callback.answer("🚫 Пользователь заблокирован", show_alert=True)
    else:
        await callback.answer("✅ Пользователь разблокирован", show_alert=True)
    
    # Перезагружаем карточку
    await _show_user_view_edit(callback, state, telegram_id)


# ============================================================================
# УПРАВЛЕНИЕ КЛЮЧОМ
# ============================================================================

@router.callback_query(F.data.startswith("admin_key_view:"))
async def show_key_view(callback: CallbackQuery, state: FSMContext):
    """Показывает экран управления ключом."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    key_id = int(callback.data.split(":")[1])
    key = get_vpn_key_by_id(key_id)
    
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    
    # Форматируем информацию о ключе
    # Формируем название ключа согласно ТЗ
    if key.get('custom_name'):
        key_name = key['custom_name']
    else:
        # Формат: первые_4_символа...последние_4_символа от client_uuid
        uuid = key.get('client_uuid') or ''
        if len(uuid) >= 8:
            key_name = f"{uuid[:4]}...{uuid[-4:]}"
        else:
            key_name = uuid or f"Ключ #{key_id}"
    server_name = key.get('server_name', 'Неизвестный сервер')
    tariff_name = key.get('tariff_name', 'Неизвестный тариф')
    expires_at = key.get('expires_at', '?')
    created_at = key.get('created_at', '?')
    
    text = (
        f"🔑 *{key_name}*\n\n"
        f"🖥️ Сервер: {server_name}\n"
        f"📋 Тариф: {tariff_name}\n"
        f"📅 Создан: {created_at}\n"
        f"⏰ Истекает: {expires_at}\n"
    )
    
    # Получаем статистику трафика из панели
    if key.get('server_active'):
        try:
            server_data = {
                'id': key.get('server_id'),
                'name': key.get('server_name'),
                'host': key.get('host'),
                'port': key.get('port'),
                'web_base_path': key.get('web_base_path'),
                'login': key.get('login'),
                'password': key.get('password'),
            }
            
            email = key.get('panel_email')
            if not email:
                if key.get('username'):
                    email = f"user_{key['username']}"
                else:
                    email = f"user_{key['telegram_id']}"
            
            client = get_client_from_server_data(server_data)
            stats = await client.get_client_stats(email)
            
            if stats:
                # Форматируем трафик
                up = stats.get('up', 0)
                down = stats.get('down', 0)
                total_limit = stats.get('total', 0)
                
                used = up + down
                
                # Форматируем использованный трафик
                used_text = format_traffic(used)
                
                # Форматируем лимит
                if total_limit > 0:
                    limit_text = format_traffic(total_limit)
                    remaining = max(0, total_limit - used)
                    remaining_text = format_traffic(remaining)
                    
                    text += (
                        f"\n📊 *Трафик:*\n"
                        f"  📥 Загрузка: {format_traffic(down)}\n"
                        f"  📤 Отправка: {format_traffic(up)}\n"
                        f"  ━━━━━━━━━━━━━\n"
                        f"  ✅ Использовано: {used_text}\n"
                        f"  🎯 Лимит: {limit_text}\n"
                        f"  💾 Остаток: {remaining_text}\n"
                    )
                else:
                    text += (
                        f"\n📊 *Трафик:*\n"
                        f"  📥 Загрузка: {format_traffic(down)}\n"
                        f"  📤 Отправка: {format_traffic(up)}\n"
                        f"  ━━━━━━━━━━━━━\n"
                        f"  ✅ Использовано: {used_text}\n"
                        f"  ∞ Без лимита\n"
                    )
        except VPNAPIError as e:
            logger.warning(f"Не удалось получить статистику трафика для ключа {key_id}: {e}")
            text += "\n⚠️ _Статистика трафика недоступна_\n"
        except Exception as e:
            logger.error(f"Ошибка при получении статистики трафика: {e}")
            text += "\n⚠️ _Ошибка получения статистики_\n"
    
    user_telegram_id = key.get('telegram_id')
    
    await callback.message.edit_text(
        text,
        reply_markup=key_view_kb(key_id, user_telegram_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_key_extend:"))
async def start_key_extend(callback: CallbackQuery, state: FSMContext):
    """Начало продления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    key_id = int(callback.data.split(":")[1])
    await state.set_state(AdminStates.key_extend_days)
    await state.update_data(current_key_id=key_id)
    
    await callback.message.edit_text(
        "📅 *Продление ключа*\n\n"
        "Введите количество дней для продления:",
        reply_markup=key_action_cancel_kb(key_id, 0),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.key_extend_days, F.text)
async def process_key_extend(message: Message, state: FSMContext):
    """Обработка ввода дней для продления."""
    if not is_admin(message.from_user.id):
        return
    
    text = message.text.strip()
    
    if not text.isdigit() or int(text) < 1 or int(text) > 365:
        await message.answer(
            "❌ Введите число от 1 до 365",
            parse_mode="Markdown"
        )
        return
    
    days = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    
    success = extend_vpn_key(key_id, days)
    
    if success:
        await message.answer(f"✅ Ключ продлён на {days} дней!")
        
        # Возвращаемся к просмотру ключа
        key = get_vpn_key_by_id(key_id)
        if key:
            await state.set_state(AdminStates.key_view)
            # Показываем обновлённую информацию
    else:
        await message.answer("❌ Ошибка продления ключа")


@router.callback_query(F.data.startswith("admin_key_reset_traffic:"))
async def reset_key_traffic(callback: CallbackQuery, state: FSMContext):
    """Сброс трафика ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    key_id = int(callback.data.split(":")[1])
    key = get_vpn_key_by_id(key_id)
    
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return
    
    # Проверяем что сервер активен
    if not key.get('server_active'):
        await callback.answer("❌ Сервер неактивен", show_alert=True)
        return
    
    # Собираем данные для API
    server_data = {
        'id': key.get('server_id'),
        'name': key.get('server_name'),
        'host': key.get('host'),
        'port': key.get('port'),
        'web_base_path': key.get('web_base_path'),
        'login': key.get('login'),
        'password': key.get('password'),
    }
    
    inbound_id = key.get('panel_inbound_id')
    
    # Формируем email для панели
    # Сначала пробуем взять из БД (с миграцией v4)
    email = key.get('panel_email')
    
    # Фолбек для старых записей (если вдруг миграция не сработала)
    if not email:
        if key.get('username'):
            email = f"user_{key['username']}"
        else:
            email = f"user_{key['telegram_id']}"
    
    try:
        client = get_client_from_server_data(server_data)
        await client.reset_client_traffic(inbound_id, email)
        await callback.answer("✅ Трафик успешно сброшен!", show_alert=True)
    except VPNAPIError as e:
        logger.error(f"Ошибка сброса трафика: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    except Exception as e:
        logger.error(f"Неожиданная ошибка при сбросе трафика: {e}")
        await callback.answer("❌ Ошибка при сбросе трафика", show_alert=True)


@router.callback_query(F.data.startswith("admin_key_change_traffic:"))
async def start_change_traffic_limit(callback: CallbackQuery, state: FSMContext):
    """Начало изменения лимита трафика."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    key_id = int(callback.data.split(":")[1])
    key = get_vpn_key_by_id(key_id)
    
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return
    
    # Проверяем что сервер активен
    if not key.get('server_active'):
        await callback.answer("❌ Сервер неактивен", show_alert=True)
        return
    
    await state.set_state(AdminStates.key_change_traffic)
    await state.update_data(current_key_id=key_id)
    
    user_telegram_id = key.get('telegram_id')
    await state.update_data(current_user_telegram_id=user_telegram_id)
    
    await callback.message.edit_text(
        "📊 *Изменение лимита трафика*\n\n"
        "Введите новый лимит в ГБ (0 = без лимита):",
        reply_markup=key_action_cancel_kb(key_id, user_telegram_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.key_change_traffic, F.text)
async def process_change_traffic_limit(message: Message, state: FSMContext):
    """Обработка ввода нового лимита трафика."""
    if not is_admin(message.from_user.id):
        return
    
    text = message.text.strip()
    
    if not text.isdigit():
        await message.answer("❌ Введите число (0 = без лимита)")
        return
    
    traffic_gb = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    
    key = get_vpn_key_by_id(key_id)
    if not key:
        await message.answer("❌ Ключ не найден")
        return
    
    # Собираем данные для API
    server_data = {
        'id': key.get('server_id'),
        'name': key.get('server_name'),
        'host': key.get('host'),
        'port': key.get('port'),
        'web_base_path': key.get('web_base_path'),
        'login': key.get('login'),
        'password': key.get('password'),
    }
    
    inbound_id = key.get('panel_inbound_id')
    client_uuid = key.get('client_uuid')
    
    # Формируем email для панели
    email = key.get('panel_email')
    if not email:
        if key.get('username'):
            email = f"user_{key['username']}"
        else:
            email = f"user_{key['telegram_id']}"
    
    try:
        client = get_client_from_server_data(server_data)
        await client.update_client_traffic_limit(
            inbound_id=inbound_id,
            client_uuid=client_uuid,
            email=email,
            total_gb=traffic_gb
        )
        
        traffic_text = f"{traffic_gb} ГБ" if traffic_gb > 0 else "без лимита"
        await message.answer(f"✅ Лимит трафика успешно обновлён: {traffic_text}!")
        
        # Возвращаемся к просмотру ключа
        await state.set_state(AdminStates.key_view)
        
    except VPNAPIError as e:
        logger.error(f"Ошибка обновления лимита трафика: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обновлении лимита трафика: {e}")
        await message.answer("❌ Ошибка при обновлении лимита трафика")


# ============================================================================
# ДОБАВЛЕНИЕ КЛЮЧА АДМИНИСТРАТОРОМ
# ============================================================================

@router.callback_query(F.data.startswith("admin_user_add_key:"))
async def start_add_key(callback: CallbackQuery, state: FSMContext):
    """Начало добавления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    
    # Проверяем что пользователь существует
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    # Получаем список серверов
    servers = get_active_servers()
    
    if not servers:
        await callback.answer("❌ Нет активных серверов", show_alert=True)
        return
    
    await state.set_state(AdminStates.add_key_server)
    await state.update_data(
        add_key_user_id=user['id'],
        add_key_user_telegram_id=telegram_id
    )
    
    await callback.message.edit_text(
        f"➕ *Добавление ключа для {format_user_display(user)}*\n\n"
        "Выберите сервер:",
        reply_markup=add_key_server_kb(servers),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_add_key_server:"))
async def select_add_key_server(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для нового ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from database.requests import get_server_by_id
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await state.update_data(add_key_server_id=server_id)
    
    # Получаем список inbound с сервера
    try:
        client = get_client_from_server_data(server)
        inbounds = await client.get_inbounds()
        
        if not inbounds:
            await callback.answer("❌ На сервере нет inbound", show_alert=True)
            return
        
        await state.set_state(AdminStates.add_key_inbound)
        
        await callback.message.edit_text(
            f"🖥️ *Сервер:* `{server['name']}`\n\n"
            "Выберите протокол (inbound):",
            reply_markup=add_key_inbound_kb(inbounds),
            parse_mode="Markdown"
        )
    except VPNAPIError as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    
    await callback.answer()


@router.callback_query(F.data.startswith("admin_add_key_inbound:"))
async def select_add_key_inbound(callback: CallbackQuery, state: FSMContext):
    """Выбор inbound для нового ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    inbound_id = int(callback.data.split(":")[1])
    await state.update_data(add_key_inbound_id=inbound_id)
    await state.set_state(AdminStates.add_key_traffic)
    
    await callback.message.edit_text(
        "📊 *Лимит трафика*\n\n"
        "Введите лимит в ГБ (0 = без лимита):",
        reply_markup=add_key_step_kb(2),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.add_key_traffic, F.text)
async def process_add_key_traffic(message: Message, state: FSMContext):
    """Обработка ввода лимита трафика."""
    if not is_admin(message.from_user.id):
        return
    
    text = message.text.strip()
    
    if not text.isdigit():
        await message.answer("❌ Введите число (0 = без лимита)")
        return
    
    traffic_gb = int(text)
    await state.update_data(add_key_traffic_gb=traffic_gb)
    await state.set_state(AdminStates.add_key_days)
    
    await message.answer(
        "📅 *Срок действия*\n\n"
        "Введите количество дней:",
        reply_markup=add_key_step_kb(3),
        parse_mode="Markdown"
    )


@router.message(AdminStates.add_key_days, F.text)
async def process_add_key_days(message: Message, state: FSMContext):
    """Обработка ввода срока действия."""
    if not is_admin(message.from_user.id):
        return
    
    text = message.text.strip()
    
    if not text.isdigit() or int(text) < 1 or int(text) > 365:
        await message.answer("❌ Введите число от 1 до 365")
        return
    
    days = int(text)
    await state.update_data(add_key_days=days)
    await state.set_state(AdminStates.add_key_confirm)
    
    # Показываем сводку
    data = await state.get_data()
    
    from database.requests import get_server_by_id
    server = get_server_by_id(data['add_key_server_id'])
    
    traffic_text = f"{data.get('add_key_traffic_gb', 0)} ГБ" if data.get('add_key_traffic_gb', 0) > 0 else "без лимита"
    
    await message.answer(
        "✅ *Подтверждение создания ключа*\n\n"
        f"🖥️ Сервер: {server['name'] if server else '?'}\n"
        f"📊 Трафик: {traffic_text}\n"
        f"📅 Срок: {days} дней\n",
        reply_markup=add_key_confirm_kb(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_add_key_confirm")
async def confirm_add_key(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и создание ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    
    user_id = data.get('add_key_user_id')
    user_telegram_id = data.get('add_key_user_telegram_id')
    server_id = data.get('add_key_server_id')
    inbound_id = data.get('add_key_inbound_id')
    traffic_gb = data.get('add_key_traffic_gb', 0)
    days = data.get('add_key_days', 30)
    
    from database.requests import get_server_by_id
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    # Получаем пользователя для email
    user = get_user_by_telegram_id(user_telegram_id)
    email = generate_unique_email(user)
    
    try:
        # Создаём клиента в панели 3X-UI
        client = get_client_from_server_data(server)
        result = await client.add_client(
            inbound_id=inbound_id,
            email=email,
            total_gb=traffic_gb,
            expire_days=days,
            limit_ip=1,
            tg_id=str(user_telegram_id)
        )
        
        client_uuid = result['uuid']
        
        # Получаем Admin Tariff для записи
        from database.requests import get_admin_tariff
        admin_tariff = get_admin_tariff()
        tariff_id = admin_tariff['id']
        
        # Сохраняем в БД
        key_id = create_vpn_key_admin(
            user_id=user_id,
            server_id=server_id,
            tariff_id=tariff_id,
            panel_inbound_id=inbound_id,
            panel_email=email,
            client_uuid=client_uuid,
            days=days
        )
        
        await callback.answer("✅ Ключ успешно создан!", show_alert=True)
        
        # Возвращаемся к просмотру пользователя
        await _show_user_view_edit(callback, state, user_telegram_id)
        
    except VPNAPIError as e:
        logger.error(f"Ошибка создания ключа: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        await callback.answer("❌ Ошибка при создании ключа", show_alert=True)


@router.callback_query(F.data == "admin_user_add_key_cancel")
async def cancel_add_key(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    user_telegram_id = data.get('add_key_user_telegram_id') or data.get('current_user_telegram_id')
    
    if user_telegram_id:
        await _show_user_view_edit(callback, state, user_telegram_id)
    else:
        # Возвращаемся в меню пользователей
        await show_users_menu(callback, state)


@router.callback_query(F.data == "admin_add_key_back")
async def add_key_back(callback: CallbackQuery, state: FSMContext):
    """Шаг назад при добавлении ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current_state = await state.get_state()
    data = await state.get_data()
    
    if current_state == AdminStates.add_key_inbound.state:
        # Возвращаемся к выбору сервера
        servers = get_active_servers()
        await state.set_state(AdminStates.add_key_server)
        
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await callback.message.edit_text(
            f"➕ *Добавление ключа для {format_user_display(user) if user else '?'}*\n\n"
            "Выберите сервер:",
            reply_markup=add_key_server_kb(servers),
            parse_mode="Markdown"
        )
    else:
        # Для остальных шагов - отмена
        await cancel_add_key(callback, state)
    
    await callback.answer()
