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
from bot.keyboards.admin import admin_main_menu_kb, home_only_kb
from bot.utils.admin import is_admin

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
            "⚙️ *Админ-панель*\n\n"
            "🖥️ Серверов пока нет.\n"
            "Добавьте первый сервер в разделе «Сервера»."
        )
    
    lines = ["⚙️ *Админ-панель*\n"]
    
    for server in servers:
        status_emoji = "🟢" if server['is_active'] else "🔴"
        lines.append(f"{status_emoji} *{server['name']}* (`{server['host']}:{server['port']}`)")
        
        if server['is_active']:
            # Пробуем получить статистику
            try:
                client = get_client_from_server_data(server)
                stats = await client.get_stats()
                
                if stats.get('online'):
                    traffic = format_traffic(stats.get('total_traffic_bytes', 0))
                    active = stats.get('active_clients', 0)
                    
                    cpu_text = ""
                    if stats.get('cpu_percent') is not None:
                        cpu_text = f" | 💻 {stats['cpu_percent']}% CPU"
                    
                    lines.append(f"   👥 {active} активных | 📊 {traffic}{cpu_text}")
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


@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery, state: FSMContext):
    """Показывает главное меню админ-панели."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.admin_menu)
    
    text = await get_admin_stats_text()
    
    await callback.message.edit_text(
        text,
        reply_markup=admin_main_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ПЕРЕАДРЕСАЦИЯ НА ПОДРОУТЕРЫ
# ============================================================================

# Раздел «Пользователи» реализован в users.py
# Раздел «Настройки бота» реализован в system.py

