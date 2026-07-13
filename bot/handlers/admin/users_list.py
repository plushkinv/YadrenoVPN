import logging
import uuid
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, extend_vpn_key, create_vpn_key_admin, get_active_servers, get_all_tariffs, get_user_balance, get_user_referral_coefficient, add_to_balance, deduct_from_balance, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, safe_edit_or_send
from bot.handlers.admin.users_manage import _show_user_view
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic

logger = logging.getLogger(__name__)
from bot.utils.text import safe_edit_or_send

router = Router()
USERS_PER_PAGE = 20

@router.callback_query(F.data == 'admin_users')
async def show_users_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the main screen of the users section."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    await state.set_state(AdminStates.users_menu)
    await state.update_data(users_filter='all', users_page=0)
    stats = get_users_stats()
    text = f"👥 <b>Пользователи</b>\n\n📊 <b>Статистика:</b>\n👤 Всего: <b>{stats['total']}</b>\n✅ С активными ключами: <b>{stats['active']}</b>\n❌ Без активных ключей: <b>{stats['inactive']}</b>\n🆕 Никогда не покупали: <b>{stats['never_paid']}</b>\n🚫 Ключ истёк: <b>{stats['expired']}</b>\n📵 Заблокировали бота: <b>{stats.get('bot_blocked', 0)}</b>"
    await safe_edit_or_send(callback.message, text, reply_markup=users_menu_kb(stats))
    await callback.answer()

@router.callback_query(F.data == 'admin_users_list')
async def show_users_list(callback: CallbackQuery, state: FSMContext):
    """Shows a list of users."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    await state.set_state(AdminStates.users_list)
    data = await state.get_data()
    current_filter = data.get('users_filter', 'all')
    page = data.get('users_page', 0)
    await _show_users_page(callback, state, page, current_filter)

@router.callback_query(F.data.startswith('admin_users_filter:'))
async def set_users_filter(callback: CallbackQuery, state: FSMContext):
    """Sets a user filter."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    filter_type = callback.data.split(':')[1]
    await state.update_data(users_filter=filter_type, users_page=0)
    await _show_users_page(callback, state, 0, filter_type)

@router.callback_query(F.data.startswith('admin_users_page:'))
async def change_users_page(callback: CallbackQuery, state: FSMContext):
    """Go to another list page."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    page = int(callback.data.split(':')[1])
    data = await state.get_data()
    current_filter = data.get('users_filter', 'all')
    await state.update_data(users_page=page)
    await _show_users_page(callback, state, page, current_filter)

async def _show_users_page(callback: CallbackQuery, state: FSMContext, page: int, filter_type: str):
    """Displays the user list page."""
    offset = page * USERS_PER_PAGE
    (users, total) = get_all_users_paginated(offset, USERS_PER_PAGE, filter_type)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    from bot.keyboards.admin import USERS_FILTERS
    filter_name = USERS_FILTERS.get(filter_type, filter_type)
    if users:
        text = f'👥 <b>Пользователи</b> — {filter_name}\n\nПоказано: {len(users)} из {total}\nСтраница {page + 1} из {total_pages}'
    else:
        text = f'👥 <b>Пользователи</b> — {filter_name}\n\n😕 Пользователей не найдено'
    await safe_edit_or_send(callback.message, text, reply_markup=users_list_kb(users, page, total_pages, filter_type))
    await callback.answer()

@router.callback_query(F.data == 'admin_users_select')
async def request_user_selection(callback: CallbackQuery, state: FSMContext):
    """Request to search for a user (by ID, @username or via contacts)."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    await state.set_state(AdminStates.waiting_user_id)
    text = '🔍 <b>Поиск пользователя</b>\n\nОтправьте:\n• telegram_id (число)\n• @username\n• panel_email (email клиента из панели)'
    
    reply_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👤 Выбрать пользователя', request_users=KeyboardButtonRequestUsers(request_id=1, user_is_bot=False, max_quantity=1))],
            [KeyboardButton(text='❌ Отмена')]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await safe_edit_or_send(callback.message, text, reply_markup=reply_keyboard, force_new=True)
    await callback.answer()

@router.message(AdminStates.waiting_user_id, F.users_shared)
async def handle_users_shared(message: Message, state: FSMContext):
    """Handling the selected user via KeyboardButtonRequestUsers."""
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    temp_msg_id = data.get('search_temp_msg_id')
    edit_message_id = data.get('edit_message_id')
    try:
        await message.delete()
        if temp_msg_id:
            await message.bot.delete_message(message.chat.id, temp_msg_id)
    except Exception:
        pass
    users_shared: UsersShared = message.users_shared
    if users_shared.users:
        telegram_id = users_shared.users[0].user_id
        import asyncio
        temp = await message.answer('⏳', reply_markup=ReplyKeyboardRemove())
        async def _delete_temp():
            await asyncio.sleep(2.0)
            try:
                await temp.delete()
            except:
                pass
        asyncio.create_task(_delete_temp())
        await _show_user_view(message, state, telegram_id)

@router.message(AdminStates.waiting_user_id, F.text, ~F.text.startswith('/'))
async def process_user_search_input(message: Message, state: FSMContext):
    """Processing telegram_id, @username or panel_email input."""
    if not is_admin(message.from_user.id):
        return
        
    if message.text == '❌ Отмена':
        import asyncio
        temp = await message.answer('⏳', reply_markup=ReplyKeyboardRemove())
        async def _delete_temp():
            await asyncio.sleep(2.0)
            try:
                await temp.delete()
            except:
                pass
        asyncio.create_task(_delete_temp())
        await state.set_state(AdminStates.users_menu)
        await state.update_data(users_filter='all', users_page=0)
        from database.requests import get_users_stats
        from bot.keyboards.admin import users_menu_kb
        stats = get_users_stats()
        text = f"👥 <b>Пользователи</b>\n\n📊 <b>Статистика:</b>\n👤 Всего: <b>{stats['total']}</b>\n✅ С активными ключами: <b>{stats['active']}</b>\n❌ Без активных ключей: <b>{stats['inactive']}</b>\n🆕 Никогда не покупали: <b>{stats['never_paid']}</b>\n🚫 Ключ истёк: <b>{stats['expired']}</b>\n📵 Заблокировали бота: <b>{stats.get('bot_blocked', 0)}</b>"
        await safe_edit_or_send(message, text, reply_markup=users_menu_kb(stats), force_new=True)
        return
    from database.requests import get_user_by_username, get_user_by_panel_email
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    user = None
    
    # Cancel button to display an error (we use ReplyKeyboardMarkup, since we are already in ReplyKeyboard mode)
    cancel_reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👤 Выбрать пользователя', request_users=KeyboardButtonRequestUsers(request_id=1, user_is_bot=False, max_quantity=1))],
            [KeyboardButton(text='❌ Отмена')]
        ],
        resize_keyboard=True
    )

    if text.isdigit():
        telegram_id = int(text)
        user = get_user_by_telegram_id(telegram_id)
        if not user:
            await safe_edit_or_send(message, f'❌ Пользователь с ID <code>{telegram_id}</code> не найден в базе', reply_markup=cancel_reply_kb, force_new=True)
            return
    elif text.startswith('@') or text.replace('_', '').isalnum():
        username = text.lstrip('@')
        user = get_user_by_username(username)
        if not user:
            # Trying to find by panel_email (email in the 3X-UI panel)
            user = get_user_by_panel_email(text)
            if not user:
                await safe_edit_or_send(message, f'❌ Пользователь @{username} не найден в базе', reply_markup=cancel_reply_kb, force_new=True)
                return
    else:
        # Free text - try as panel_email
        user = get_user_by_panel_email(text)
        if not user:
            await safe_edit_or_send(message, '❌ Введите telegram_id (число), @username или panel_email из панели', reply_markup=cancel_reply_kb, force_new=True)
            return
            
    import asyncio
    temp = await message.answer('⏳', reply_markup=ReplyKeyboardRemove())
    async def _delete_temp():
        await asyncio.sleep(2.0)
        try:
            await temp.delete()
        except:
            pass
    asyncio.create_task(_delete_temp())
    await _show_user_view(message, state, user['telegram_id'])
