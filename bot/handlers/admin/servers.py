"""
Роутер управления серверами.

Обрабатывает:
- Список серверов
- Добавление сервера (6-шаговый диалог)
- Просмотр сервера
- Редактирование (листание параметров)
- Активация/деактивация
- Удаление
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import urllib.parse

from config import ADMIN_IDS
from database.requests import (
    get_all_servers,
    get_server_by_id,
    add_server,
    update_server_field,
    delete_server,
    toggle_server_active
)
from bot.utils.admin import is_admin
from bot.services.vpn_api import (
    get_client_from_server_data,
    test_server_connection,
    invalidate_client_cache,
    format_traffic
)
from bot.states.admin_states import (
    AdminStates,
    SERVER_PARAMS,
    get_param_by_index,
    get_total_params
)
from bot.keyboards.admin import (
    servers_list_kb,
    server_view_kb,
    add_server_step_kb,
    add_server_confirm_kb,
    add_server_test_failed_kb,
    edit_server_kb,
    confirm_delete_kb,
    back_and_home_kb
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================




async def get_servers_list_text() -> str:
    """Формирует текст списка серверов со статистикой."""
    servers = get_all_servers()
    
    if not servers:
        return (
            "🖥️ *Сервера*\n\n"
            "Серверов пока нет.\n"
            "Нажмите «➕ Добавить сервер» чтобы добавить первый!"
        )
    
    lines = ["🖥️ *Сервера*\n"]
    
    for server in servers:
        status_emoji = "🟢" if server['is_active'] else "🔴"
        lines.append(f"{status_emoji} *{server['name']}* (`{server['host']}:{server['port']}`)")
        
        if server['is_active']:
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
                    lines.append(f"   ⚠️ Недоступен")
            except Exception as e:
                logger.warning(f"Ошибка статистики {server['name']}: {e}")
                lines.append(f"   ⚠️ Ошибка подключения")
        else:
            lines.append("   ⏸️ Деактивирован")
        
        lines.append("")
    
    return "\n".join(lines)


async def render_server_view(message: Message, server_id: int, state: FSMContext):
    """Отрисовывает экран просмотра сервера."""
    server = get_server_by_id(server_id)
    
    if not server:
        return
    
    await state.set_state(AdminStates.server_view)
    await state.update_data(server_id=server_id)
    
    # Маскируем пароль
    password_masked = "•" * min(len(server['password']), 8)
    
    status_emoji = "🟢" if server['is_active'] else "🔴"
    status_text = "Активен" if server['is_active'] else "Деактивирован"
    
    lines = [
        f"🖥️ *{server['name']}*\n",
        f"🔗 URL панели: `{server.get('protocol', 'https')}://{server['host']}:{server['port']}{server['web_base_path']}`",
        f"👤 Логин: `{server['login']}`",
        f"🔐 Пароль: `{password_masked}`\n",
        f"📊 *Статистика:*",
        f"   {status_emoji} Статус: {status_text}",
    ]
    
    if server['is_active']:
        try:
            client = get_client_from_server_data(server)
            stats = await client.get_stats()
            
            if stats.get('online'):
                traffic = format_traffic(stats.get('total_traffic_bytes', 0))
                lines.append(f"   🔑 Онлайн: {stats.get('online_clients', 0)}")
                lines.append(f"   📈 Трафик: {traffic}")
                
                if stats.get('cpu_percent') is not None:
                    lines.append(f"   💻 CPU: {stats['cpu_percent']}%")
            else:
                lines.append(f"   ⚠️ Сервер недоступен")
        except Exception as e:
            logger.warning(f"Ошибка статистики {server['name']}: {e}")
            lines.append(f"   ⚠️ Ошибка подключения")
    
    await message.edit_text(
        "\n".join(lines),
        reply_markup=server_view_kb(server_id, server['is_active']),
        parse_mode="Markdown"
    )


# ============================================================================
# СПИСОК СЕРВЕРОВ
# ============================================================================

@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery, state: FSMContext):
    """Показывает список серверов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.servers_list)
    await state.update_data(server_data={})  # Очищаем временные данные
    
    text = await get_servers_list_text()
    servers = get_all_servers()
    
    await callback.message.edit_text(
        text,
        reply_markup=servers_list_kb(servers),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_servers_refresh")
async def refresh_servers_list(callback: CallbackQuery, state: FSMContext):
    """Обновляет статистику серверов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer("🔄 Обновляю статистику...")
    
    text = await get_servers_list_text()
    servers = get_all_servers()
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=servers_list_kb(servers),
            parse_mode="Markdown"
        )
    except Exception:
        # Игнорируем ошибку "message is not modified"
        pass


# ============================================================================
# ПРОСМОТР СЕРВЕРА
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_view:"))
async def show_server_view(callback: CallbackQuery, state: FSMContext):
    """Показывает детали сервера."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.server_view)
    await state.update_data(server_id=server_id)
    
    # Используем helper для отрисовки
    await render_server_view(callback.message, server_id, state)
    await callback.answer()


# ============================================================================
# ДОБАВЛЕНИЕ СЕРВЕРА
# ============================================================================

# Состояния добавления в порядке
ADD_STATES = [
    AdminStates.add_server_name,
    AdminStates.add_server_url,
    AdminStates.add_server_login,
    AdminStates.add_server_password,
]


def get_add_step_text(step: int, data: dict) -> str:
    """Формирует текст для шага добавления сервера."""
    param = get_param_by_index(step - 1)
    total = get_total_params()
    
    lines = [f"📝 *Добавление сервера ({step}/{total})*\n"]
    
    # Показываем уже введённые данные
    for i in range(step - 1):
        p = get_param_by_index(i)
        value = data.get(p['key'], '—')
        # Маскируем пароль
        if p['key'] == 'password':
            value = "•" * min(len(str(value)), 8)
        lines.append(f"✅ {p['label']}: `{value}`")
    
    if step > 1:
        lines.append("")
    
    lines.append(f"Введите *{param['label'].lower()}*:")
    lines.append(f"_({param['hint']})_")
    
    return "\n".join(lines)


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    """Начинает диалог добавления сервера."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(ADD_STATES[0])
    await state.update_data(server_data={}, add_step=1)
    
    text = get_add_step_text(1, {})
    
    await callback.message.edit_text(
        text,
        reply_markup=add_server_step_kb(1),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_back")
async def add_server_back(callback: CallbackQuery, state: FSMContext):
    """Возврат на предыдущий шаг добавления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    
    if current_step <= 1:
        # Возврат к списку серверов
        await show_servers_list(callback, state)
        return
    
    # На шаг назад
    new_step = current_step - 1
    await state.set_state(ADD_STATES[new_step - 1])
    await state.update_data(add_step=new_step)
    
    text = get_add_step_text(new_step, data.get('server_data', {}))
    
    await callback.message.edit_text(
        text,
        reply_markup=add_server_step_kb(new_step),
        parse_mode="Markdown"
    )
    await callback.answer()


async def process_add_step(message: Message, state: FSMContext):
    """Обрабатывает ввод на шаге добавления."""
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    server_data = data.get('server_data', {})
    
    param = get_param_by_index(current_step - 1)
    value = message.text.strip()
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Парсинг URL
    if param['key'] == 'panel_url':
        url_str = value
        if not url_str.startswith(('http://', 'https://')):
            url_str = 'https://' + url_str
            
        try:
            parsed = urllib.parse.urlparse(url_str)
            protocol = parsed.scheme
            host = parsed.hostname
            if not host:
                raise ValueError("Не удалось определить хост")
                
            port = parsed.port
            if not port:
                port = 443 if protocol == 'https' else 80
                
            path = parsed.path
            if not path.endswith('/'):
                path += '/'
                
            server_data['protocol'] = protocol
            server_data['host'] = host
            server_data['port'] = port
            server_data['web_base_path'] = path
            
            # Сохраняем исходный ввод чисто для отображения на следующих шагах
            server_data['panel_url'] = url_str
            
        except Exception as e:
            await message.answer(
                "❌ Неверный формат ссылки. Убедитесь, что указан хост и по умолчанию подставляется `https://`.\nПример: `123.45.67.89:2053/api/`",
                parse_mode="Markdown"
            )
            return
    else:
        # Конвертация (если нужно)
        if 'convert' in param:
            value = param['convert'](value)
        
        # Сохраняем значение
        server_data[param['key']] = value

    await state.update_data(server_data=server_data)
    
    # Удаляем сообщение пользователя (опционально)
    try:
        await message.delete()
    except:
        pass
    
    # Переход к следующему шагу или подтверждению
    if current_step < get_total_params():
        new_step = current_step + 1
        await state.set_state(ADD_STATES[new_step - 1])
        await state.update_data(add_step=new_step)
        
        text = get_add_step_text(new_step, server_data)
        
        # Редактируем предыдущее сообщение бота
        # Для этого сохраняем message_id
        bot_message = await message.answer(
            text,
            reply_markup=add_server_step_kb(new_step),
            parse_mode="Markdown"
        )
    else:
        # Все данные введены — проверяем подключение
        await state.set_state(AdminStates.add_server_confirm)
        await state.update_data(add_step=get_total_params() + 1)
        
        await message.answer(
            "⏳ *Проверка подключения...*",
            parse_mode="Markdown"
        )
        
        # Тестируем подключение
        test_result = await test_server_connection(server_data)
        
        if test_result['success']:
            stats = test_result.get('stats', {})
            traffic = format_traffic(stats.get('total_traffic_bytes', 0))
            
            text = (
                f"✅ *Проверка подключения успешна!*\n\n"
                f"📊 Статистика:\n"
                f"   🔑 Онлайн: {stats.get('online_clients', 0)}\n"
                f"   📈 Трафик: {traffic}\n\n"
                f"Сохранить сервер?"
            )
            kb = add_server_confirm_kb()
        else:
            text = (
                f"❌ *Ошибка подключения*\n\n"
                f"`{test_result['message']}`\n\n"
                f"Проверьте введённые данные или сохраните сервер для настройки позже."
            )
            kb = add_server_test_failed_kb()
        
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")


# Хендлеры для каждого состояния добавления
@router.message(AdminStates.add_server_name)
async def add_server_name_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_url)
async def add_server_url_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_login)
async def add_server_login_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_password)
async def add_server_password_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.callback_query(F.data == "admin_server_add_test")
async def add_server_retest(callback: CallbackQuery, state: FSMContext):
    """Повторная проверка подключения."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    
    await callback.message.edit_text(
        "⏳ *Проверка подключения...*",
        parse_mode="Markdown"
    )
    
    test_result = await test_server_connection(server_data)
    
    if test_result['success']:
        stats = test_result.get('stats', {})
        traffic = format_traffic(stats.get('total_traffic_bytes', 0))
        
        text = (
            f"✅ *Проверка подключения успешна!*\n\n"
            f"📊 Статистика:\n"
            f"   🔑 Онлайн: {stats.get('online_clients', 0)}\n"
            f"   📈 Трафик: {traffic}\n\n"
            f"Сохранить сервер?"
        )
        kb = add_server_confirm_kb()
    else:
        text = (
            f"❌ *Ошибка подключения*\n\n"
            f"`{test_result['message']}`\n\n"
            f"Проверьте введённые данные или сохраните сервер для настройки позже."
        )
        kb = add_server_test_failed_kb()
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_save")
async def add_server_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет новый сервер."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    
    try:
        server_id = add_server(
            name=server_data['name'],
            host=server_data['host'],
            port=server_data['port'],
            web_base_path=server_data['web_base_path'],
            login=server_data['login'],
            password=server_data['password'],
            protocol=server_data.get('protocol', 'https')
        )
        
        await callback.message.edit_text(
            f"✅ *Сервер успешно добавлен!*\n\n"
            f"🖥️ {server_data['name']}\n"
            f"🔗 {server_data.get('protocol', 'https')}://{server_data['host']}:{server_data['port']}{server_data['web_base_path']}",
            parse_mode="Markdown"
        )
        
        # Показываем сервер через секунду
        await callback.answer("✅ Сервер добавлен!")
        
        # Перенаправляем на просмотр нового сервера
        # Перенаправляем на просмотр нового сервера
        await render_server_view(callback.message, server_id, state)
        
    except Exception as e:
        logger.error(f"Ошибка добавления сервера: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка сохранения*\n\n`{e}`",
            reply_markup=back_and_home_kb("admin_servers"),
            parse_mode="Markdown"
        )
        await callback.answer("❌ Ошибка", show_alert=True)


# ============================================================================
# РЕДАКТИРОВАНИЕ СЕРВЕРА
# ============================================================================

def get_edit_text(server: dict, current_param: int) -> str:
    """Формирует текст для экрана редактирования."""
    param = get_param_by_index(current_param)
    total = get_total_params()
    
    # Получаем текущее значение
    if param['key'] == 'panel_url':
        current_value = f"{server.get('protocol', 'https')}://{server.get('host', '')}:{server.get('port', '')}{server.get('web_base_path', '')}"
    else:
        current_value = server.get(param['key'], '')
    
    # Маскируем пароль
    if param['key'] == 'password':
        display_value = "•" * min(len(str(current_value)), 8)
    else:
        display_value = current_value
    
    lines = [
        f"✏️ *Редактирование: {server['name']}* ({current_param + 1}/{total})\n",
        f"📌 Параметр: *{param['label']}*",
        f"📝 Текущее значение: `{display_value}`\n",
        f"Введите новое значение или используйте кнопки навигации:",
        f"_({param['hint']})_"
    ]
    
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_server_edit:"))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование сервера."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.edit_server)
    await state.update_data(server_id=server_id, edit_param=0)
    
    text = get_edit_text(server, 0)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_server_kb(0, get_total_params()),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_prev")
async def edit_server_prev(callback: CallbackQuery, state: FSMContext):
    """Предыдущий параметр при редактировании."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    new_param = max(0, current_param - 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_server_kb(new_param, get_total_params()),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_next")
async def edit_server_next(callback: CallbackQuery, state: FSMContext):
    """Следующий параметр при редактировании."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    new_param = min(get_total_params() - 1, current_param + 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param)
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_server_kb(new_param, get_total_params()),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.edit_server)
async def edit_server_value(message: Message, state: FSMContext):
    """Обрабатывает ввод нового значения при редактировании."""
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    
    param = get_param_by_index(current_param)
    value = message.text.strip()
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}",
            parse_mode="Markdown"
        )
        return
    
    if param['key'] == 'panel_url':
        url_str = value
        if not url_str.startswith(('http://', 'https://')):
            url_str = 'https://' + url_str
            
        try:
            parsed = urllib.parse.urlparse(url_str)
            protocol = parsed.scheme
            host = parsed.hostname
            if not host:
                raise ValueError("Не удалось определить хост")
                
            port = parsed.port
            if not port:
                port = 443 if protocol == 'https' else 80
                
            path = parsed.path
            if not path.endswith('/'):
                path += '/'
                
            # Сохраняем все 4 параметра в БД
            update_server_field(server_id, 'protocol', protocol)
            update_server_field(server_id, 'host', host)
            update_server_field(server_id, 'port', port)
            success = update_server_field(server_id, 'web_base_path', path)
        except Exception as e:
            await message.answer(
                "❌ Неверный формат ссылки. Убедитесь, что указан хост и по умолчанию подставляется `https://`.\nПример: `123.45.67.89:2053/api/`",
                parse_mode="Markdown"
            )
            return
    else:
        # Конвертация
        if 'convert' in param:
            value = param['convert'](value)
        
        # Сохраняем в БД
        success = update_server_field(server_id, param['key'], value)
    
    if not success:
        await message.answer("❌ Ошибка сохранения")
        return
    
    # Сбрасываем кэш клиента (настройки изменились)
    invalidate_client_cache(server_id)
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass
    
    # Обновляем экран с новым значением
    server = get_server_by_id(server_id)
    text = get_edit_text(server, current_param)
    
    await message.answer(
        f"✅ *{param['label']}* обновлено!\n\n" + text,
        reply_markup=edit_server_kb(current_param, get_total_params()),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_server_edit_done")
async def edit_server_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования — возврат к просмотру."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    # Перенаправляем на просмотр сервера
    # Перенаправляем на просмотр сервера
    await render_server_view(callback.message, server_id, state)


@router.callback_query(F.data == "admin_server_edit_cancel")
async def edit_server_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования — возврат к просмотру."""
    await edit_server_done(callback, state)


# ============================================================================
# АКТИВАЦИЯ / ДЕАКТИВАЦИЯ
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server(callback: CallbackQuery, state: FSMContext):
    """Переключает активность сервера."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    new_status = toggle_server_active(server_id)
    
    if new_status is None:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    # Сбрасываем кэш
    invalidate_client_cache(server_id)
    
    status_text = "активирован 🟢" if new_status else "деактивирован 🔴"
    await callback.answer(f"Сервер {status_text}")
    
    # Обновляем экран просмотра
    # Обновляем экран просмотра
    await render_server_view(callback.message, server_id, state)


# ============================================================================
# УДАЛЕНИЕ СЕРВЕРА
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_delete:"))
async def confirm_delete_server(callback: CallbackQuery, state: FSMContext):
    """Запрашивает подтверждение удаления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.delete_server_confirm)
    
    await callback.message.edit_text(
        f"🗑️ *Удаление сервера*\n\n"
        f"Вы уверены, что хотите удалить сервер?\n\n"
        f"🖥️ *{server['name']}*\n"
        f"🔗 `{server.get('protocol', 'https')}://{server['host']}:{server['port']}{server['web_base_path']}`\n\n"
        f"⚠️ _Это действие нельзя отменить!_",
        reply_markup=confirm_delete_kb(server_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_delete_confirm:"))
async def execute_delete_server(callback: CallbackQuery, state: FSMContext):
    """Удаляет сервер."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    server_name = server['name']
    
    # Удаляем
    success = delete_server(server_id)
    
    if success:
        # Сбрасываем кэш
        invalidate_client_cache(server_id)
        
        await callback.message.edit_text(
            f"✅ *Сервер удалён*\n\n"
            f"🖥️ {server_name}",
            parse_mode="Markdown"
        )
        await callback.answer("✅ Сервер удалён")
        
        # Возврат к списку
        await show_servers_list(callback, state)
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)
