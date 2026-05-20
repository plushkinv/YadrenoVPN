"""
Главный роутер админ-панели.

Обрабатывает вход в админку и главное меню.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import get_all_servers
from bot.services.vpn_api import get_client_from_server_data, format_traffic
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import admin_main_menu_kb, home_only_kb, author_support_kb
from bot.utils.admin import is_admin
from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ПРОВЕРКА АДМИНИСТРАТОРА
# ============================================================================




# ============================================================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# ============================================================================

async def get_admin_stats_text() -> str:
    """
    Формирует текст со статистикой всех серверов.
    
    Returns:
        Отформатированный текст для сообщения
    """
    servers = get_all_servers()
    
    if not servers:
        return (
            "⚙️ <b>Админ-панель</b>\n\n"
            "🖥️ Серверов пока нет.\n"
            "Добавьте первый сервер в разделе «Сервера»."
        )
    
    lines = ["⚙️ <b>Админ-панель</b>\n"]
    
    for server in servers:
        status_emoji = "🟢" if server['is_active'] else "🔴"
        lines.append(f"{status_emoji} <b>{server['name']}</b> (<code>{server['host']}:{server['port']}</code>)")
        
        if server['is_active']:
            # Пробуем получить статистику
            try:
                client = get_client_from_server_data(server)
                stats = await client.get_stats()
                
                if stats.get('online'):
                    traffic = format_traffic(stats.get('total_traffic_bytes', 0))
                    active = stats.get('active_clients', 0)
                    online = stats.get('online_clients', 0)
                    
                    cpu_text = ""
                    if stats.get('cpu_percent') is not None:
                        cpu_text = f" | 💻 {stats['cpu_percent']}% CPU"
                    
                    lines.append(f"   🔑 {online} онлайн | 📊 {traffic}{cpu_text}")
                else:
                    error = stats.get('error', 'Нет подключения')
                    lines.append(f"   ⚠️ {error}")
            except Exception as e:
                logger.warning(f"Ошибка получения статистики {server['name']}: {e}")
                lines.append(f"   ⚠️ Ошибка подключения")
        else:
            lines.append("   ⏸️ Деактивирован")
        
        lines.append("")  # Пустая строка между серверами
    
    return "\n".join(lines)


from aiogram.exceptions import TelegramBadRequest

@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery, state: FSMContext):
    """Показывает главное меню админ-панели."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    await state.set_state(AdminStates.admin_menu)
    from bot.services.page_context import clear_page_context
    clear_page_context(callback.from_user.id)

    # Снимаем застрявшую Reply-клавиатуру (например, после поиска пользователя)
    import asyncio
    from aiogram.types import ReplyKeyboardRemove
    try:
        temp_msg = await callback.message.answer("⏳", reply_markup=ReplyKeyboardRemove())
        async def _delete_temp():
            await asyncio.sleep(2.0)
            try:
                await temp_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_delete_temp())
    except Exception:
        pass

    text = await get_admin_stats_text()
    
    try:
        await safe_edit_or_send(callback.message, 
            text,
            reply_markup=admin_main_menu_kb()
        )
    except TelegramBadRequest as e:
        if "is not modified" not in str(e):
            logger.error(f"Ошибка при обновлении меню: {e}")


# ============================================================================
# РАЗДЕЛ ПОДДЕРЖКИ
# ============================================================================

@router.callback_query(F.data == "admin_author_support")
async def show_author_support(callback: CallbackQuery):
    """Показывает экран поддержки автора."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    await callback.answer()
    
    text = (
        "👤 <b>Автор и поддержка</b>\n\n"
        "<b>Разработчик</b>: <a href=\"https://t.me/plushkin_blog\">Plushkin Blog</a>\n\n"
        "Я собираю деньги на разработку игры в жанре MMORTS с честной экономикой и никакого pay2win. Т.е. нельзя будет ничего купить у автора игры, никаких эксклюзивных вещей или бесконечных ресурсов для богатых.\n\n"
        "Очень нужна ваша поддержка, даже 100р уже вперед. как говорится с мира по нитке ;)\n"
        "💳 <b>Карты РФ</b>: https://yoomoney.ru/fundraise/1GJ73GGRJBC.260318\n"
        "💰 <b>USDT (TON/BSC/ARBITRUM)</b>: https://t.me/Ya_SellerBot?start=item-40\n\n"
        "‼️Другие полезные для тебя боты\n\n"
        "@Ya_FooterBot - <i>сделай автоматическую подпись ко всем постам в своем канале, добавь туда ссылку на свой VPN</i>"
    )
    
    try:
        await safe_edit_or_send(
            callback.message, 
            text,
            reply_markup=author_support_kb()
        )
    except TelegramBadRequest as e:
        if "is not modified" not in str(e):
            logger.error(f"Ошибка при показе поддержки автора: {e}")

# ============================================================================
# ПЕРЕАДРЕСАЦИЯ НА ПОДРОУТЕРЫ
# ============================================================================

# Раздел «Пользователи» реализован в users.py
# Раздел «Настройки бота» реализован в system.py

