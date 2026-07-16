"""
Server management router.

Processes:
- List of servers
- Adding a server with API-token or login/password authentication
- View server
- Editing (scrolling through parameters)
- Activation/deactivation
- Removal
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
    toggle_server_active,
    get_groups_count,
    get_all_groups,
    get_group_by_id,
    update_server,
    get_server_group_ids,
    toggle_server_group
)
from bot.utils.admin import is_admin
from bot.services.admin_monitoring import (
    build_servers_monitoring_text,
    collect_admin_monitoring_snapshot,
)
from bot.services.vpn_api import (
    get_client_from_server_data,
    test_server_connection,
    invalidate_client_cache,
    format_traffic
)
from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.states.admin_states import (
    AdminStates,
    get_param_by_index,
    get_total_params
)
from bot.keyboards.admin import (
    servers_list_kb,
    server_view_kb,
    server_groups_kb,
    add_server_auth_method_kb,
    add_server_step_kb,
    add_server_confirm_kb,
    add_server_test_failed_kb,
    edit_server_kb,
    confirm_delete_kb,
    back_and_home_kb,
    group_select_kb
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send, escape_html

router = Router()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================




async def get_servers_list_text() -> str:
    """Generates detailed monitoring of panels and nodes."""
    snapshot = await collect_admin_monitoring_snapshot()
    return build_servers_monitoring_text(snapshot)


async def render_server_view(message: Message, server_id: int, state: FSMContext):
    """Renders the server browsing screen."""
    server = get_server_by_id(server_id)

    if not server:
        return

    await state.set_state(AdminStates.server_view)
    await state.update_data(server_id=server_id)

    status_emoji = "🟢" if server['is_active'] else "🔴"
    status_text = "Активен" if server['is_active'] else "Деактивирован"
    auth_method = get_server_auth_method(server)

    lines = [
        f"🖥️ <b>{escape_html(server['name'])}</b>\n",
        f"🔗 URL панели: <code>{escape_html(server.get('protocol', 'https'))}://"
        f"{escape_html(server['host'])}:{server['port']}{escape_html(server['web_base_path'])}</code>",
        f"🔐 Подключение: <b>{'API-ключ' if auth_method == AUTH_API_TOKEN else 'Логин и пароль'}</b>",
    ]
    if auth_method == AUTH_API_TOKEN:
        lines.append("🔑 API-ключ: <code>сохранён</code>\n")
    else:
        password_masked = "•" * min(len(server.get('password') or ''), 8)
        lines.extend([
            f"👤 Логин: <code>{escape_html(server.get('login') or '')}</code>",
            f"🔐 Пароль: <code>{password_masked}</code>\n",
        ])

    lines.extend([
        f"🧩 <b>3x-ui API:</b>",
        f"   Версия: <code>{escape_html(server.get('panel_version') or 'не определена')}</code>",
        f"   Профиль: <code>{escape_html(server.get('panel_api_profile') or 'не определён')}</code>",
        f"   Проверка: <code>{escape_html(server.get('panel_checked_at') or 'ещё не выполнялась')}</code>\n",
        f"📊 <b>Статистика:</b>",
        f"   {status_emoji} Статус: {status_text}",
    ])

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

    # Groups—show if there is more than one group
    groups_count = get_groups_count()
    if groups_count > 1:
        group_ids = get_server_group_ids(server_id)
        group_names = []
        for gid in group_ids:
            g = get_group_by_id(gid)
            if g:
                group_names.append(g['name'])
        groups_str = ", ".join(group_names) if group_names else "Основная"
        lines.append(f"\n📂 Группы: <code>{groups_str}</code>")

    await safe_edit_or_send(message, 
        "\n".join(lines),
        reply_markup=server_view_kb(server_id, server['is_active'], groups_count > 1)
    )


# ============================================================================
# SERVER LIST
# ============================================================================

@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery, state: FSMContext):
    """Shows a list of servers."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.servers_list)
    await state.update_data(server_data={})  # Clearing temporary data
    
    text = await get_servers_list_text()
    servers = get_all_servers()
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=servers_list_kb(servers)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_servers_refresh")
async def refresh_servers_list(callback: CallbackQuery, state: FSMContext):
    """Updates server statistics."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer("🔄 Обновляю статистику...")
    
    text = await get_servers_list_text()
    servers = get_all_servers()
    
    try:
        await safe_edit_or_send(callback.message, 
            text,
            reply_markup=servers_list_kb(servers)
        )
    except Exception:
        # Ignore the error "message is not modified"
        pass


# ============================================================================
# VIEW SERVER
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_view:"))
async def show_server_view(callback: CallbackQuery, state: FSMContext):
    """Shows server details."""
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
    
    # Using helper for rendering
    await render_server_view(callback.message, server_id, state)
    await callback.answer()


# ============================================================================
# ADDING A SERVER
# ============================================================================

AUTH_API_TOKEN = "api_token"
AUTH_LOGIN_PASSWORD = "login_password"

ADD_STATES = {
    AUTH_API_TOKEN: [
        AdminStates.add_server_name,
        AdminStates.add_server_url,
        AdminStates.add_server_api_token,
    ],
    AUTH_LOGIN_PASSWORD: [
        AdminStates.add_server_name,
        AdminStates.add_server_url,
        AdminStates.add_server_login,
        AdminStates.add_server_password,
    ],
}


def get_server_auth_method(server: dict) -> str:
    """Derives the persisted authentication method without a schema flag."""
    has_credentials = bool(
        str(server.get('login') or '').strip()
        and str(server.get('password') or '').strip()
    )
    if server.get('api_token') and not has_credentials:
        return AUTH_API_TOKEN
    return AUTH_LOGIN_PASSWORD


def _get_add_states(auth_method: str) -> list:
    """Returns FSM states for one authentication branch."""
    return ADD_STATES.get(auth_method, ADD_STATES[AUTH_LOGIN_PASSWORD])


def _masked_server_value(key: str, value: object) -> str:
    """Returns an HTML-safe value for the add/edit summaries."""
    if key in {'password', 'api_token'}:
        return "•" * min(len(str(value or '')), 8)
    return escape_html(str(value if value is not None else '—'))


def get_add_step_text(step: int, data: dict, auth_method: str) -> str:
    """Generates text for the add server step."""
    param = get_param_by_index(step - 1, auth_method)
    total = get_total_params(auth_method)
    
    lines = [f"📝 <b>Добавление сервера ({step}/{total})</b>\n"]
    
    # Showing already entered data
    for i in range(step - 1):
        p = get_param_by_index(i, auth_method)
        value = data.get(p['key'], '—')
        lines.append(f"✅ {p['label']}: <code>{_masked_server_value(p['key'], value)}</code>")
    
    if step > 1:
        lines.append("")
    
    lines.append(f"Введите <b>{param['label'].lower()}</b>:")
    lines.append(f"<i>({escape_html(param['hint'])})</i>")
    
    return "\n".join(lines)


async def render_add_auth_method(message: Message, state: FSMContext, *, reset: bool) -> None:
    """Shows the authentication choice before any server fields are requested."""
    await state.set_state(AdminStates.add_server_auth_method)
    if reset:
        await state.update_data(
            server_data={},
            auth_method=None,
            add_step=0,
            selected_group_id=None,
            connection_test_passed=False,
        )
    await safe_edit_or_send(
        message,
        "🔐 <b>Подключение сервера</b>\n\n"
        "🔑 <b>API-ключ</b> — рекомендуемый вариант для 3X-UI 3.3.0 и новее. "
        "Бот работает без входа в аккаунт панели, поэтому в панели можно оставить "
        "включённой двухфакторную аутентификацию.\n\n"
        "👤 <b>Логин и пароль</b> — вариант для старых панелей. При включённой "
        "двухфакторной аутентификации автоматический вход по логину и паролю не работает.\n\n"
        "Выберите способ подключения:",
        reply_markup=add_server_auth_method_kb(),
    )


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    """Starts the Add Server dialog."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await render_add_auth_method(callback.message, state, reset=True)
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_method")
async def show_add_server_auth_method(callback: CallbackQuery, state: FSMContext):
    """Returns from group/field selection to the authentication choice."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await render_add_auth_method(callback.message, state, reset=False)
    await callback.answer()


@router.callback_query(
    AdminStates.add_server_auth_method,
    F.data.in_({"admin_server_auth_api_token", "admin_server_auth_login_password"}),
)
async def select_server_auth_method(callback: CallbackQuery, state: FSMContext):
    """Selects API-token or username/password authentication."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    auth_method = (
        AUTH_API_TOKEN
        if callback.data == "admin_server_auth_api_token"
        else AUTH_LOGIN_PASSWORD
    )
    server_data = {'login': '', 'password': ''} if auth_method == AUTH_API_TOKEN else {}
    await state.update_data(
        auth_method=auth_method,
        server_data=server_data,
        add_step=0,
        connection_test_passed=False,
    )

    groups_count = get_groups_count()
    if groups_count > 1:
        groups = get_all_groups()
        await state.set_state(AdminStates.server_select_group)
        await safe_edit_or_send(
            callback.message,
            "📂 <b>Группа сервера</b>\n\nВыберите группу для нового сервера:",
            reply_markup=group_select_kb(
                groups,
                "server_group_select",
                "admin_server_add_method",
            ),
        )
        await callback.answer()
        return

    states = _get_add_states(auth_method)
    await state.set_state(states[0])
    await state.update_data(add_step=1, selected_group_id=1)
    text = get_add_step_text(1, server_data, auth_method)
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=add_server_step_kb(1, get_total_params(auth_method)),
    )
    await callback.answer()


@router.callback_query(AdminStates.server_select_group, F.data.startswith("server_group_select:"))
async def server_group_selected(callback: CallbackQuery, state: FSMContext):
    """Processing group selection for a new server."""
    group_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    states = _get_add_states(auth_method)
    server_data = data.get('server_data', {})

    await state.set_state(states[0])
    await state.update_data(add_step=1, selected_group_id=group_id)
    
    group = get_group_by_id(group_id)
    group_name = group['name'] if group else 'Основная'
    
    text = (
        f"📂 Группа: <b>{escape_html(group_name)}</b>\n\n"
        + get_add_step_text(1, server_data, auth_method)
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=add_server_step_kb(1, get_total_params(auth_method))
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_back")
async def add_server_back(callback: CallbackQuery, state: FSMContext):
    """Return to the previous adding step."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    states = _get_add_states(auth_method)
    
    if current_step <= 1:
        await render_add_auth_method(callback.message, state, reset=False)
        await callback.answer()
        return
    
    # One step back
    new_step = current_step - 1
    await state.set_state(states[new_step - 1])
    await state.update_data(add_step=new_step)
    
    text = get_add_step_text(new_step, data.get('server_data', {}), auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=add_server_step_kb(new_step, get_total_params(auth_method))
    )
    await callback.answer()


async def process_add_step(message: Message, state: FSMContext):
    """Processes input during the add step."""
    data = await state.get_data()
    current_step = data.get('add_step', 1)
    server_data = data.get('server_data', {})
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    states = _get_add_states(auth_method)
    total_params = get_total_params(auth_method)
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    param = get_param_by_index(current_step - 1, auth_method)
    value = get_message_text_for_storage(message, 'plain')
    
    # Validation
    if not param['validate'](value):
        await safe_edit_or_send(message,
            f"❌ {param['error']}\n\nПопробуйте ещё раз:"
        )
        return
    
    # URL parsing
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
            
            # Save the original input purely for display in the next steps
            server_data['panel_url'] = url_str
            
        except Exception as e:
            await safe_edit_or_send(message,
                "❌ Неверный формат ссылки. Убедитесь, что указан хост и по умолчанию подставляется <code>https://</code>.\nПример: <code>123.45.67.89:2053/api/</code>"
            )
            return
    else:
        # Conversion (if necessary)
        if 'convert' in param:
            value = param['convert'](value)
        
        # Saving the value
        server_data[param['key']] = value

    await state.update_data(server_data=server_data)
    
    # Delete a user's message (optional)
    try:
        await message.delete()
    except:
        pass
    
    # Move to next step or confirmation
    if current_step < total_params:
        new_step = current_step + 1
        await state.set_state(states[new_step - 1])
        await state.update_data(add_step=new_step)
        
        text = get_add_step_text(new_step, server_data, auth_method)
        
        # Editing the previous bot message
        # To do this, save message_id
        await safe_edit_or_send(message,
            text,
            reply_markup=add_server_step_kb(new_step, total_params),
            force_new=True
        )
    else:
        # All data has been entered - check the connection
        await state.set_state(AdminStates.add_server_confirm)
        await state.update_data(add_step=total_params + 1, connection_test_passed=False)
        
        await safe_edit_or_send(message,
            "⏳ <b>Проверка подключения...</b>",
            force_new=True
        )
        
        # Testing the connection
        test_result = await test_server_connection(server_data)
        await state.update_data(
            server_data=server_data,
            connection_test_passed=bool(test_result['success']),
        )
        
        if test_result['success']:
            stats = test_result.get('stats', {})
            traffic = format_traffic(stats.get('total_traffic_bytes', 0))
            
            text = (
                f"✅ <b>Проверка подключения успешна!</b>\n\n"
                f"📊 Статистика:\n"
                f"   🔑 Онлайн: {stats.get('online_clients', 0)}\n"
                f"   📈 Трафик: {traffic}\n\n"
                f"Сохранить сервер?"
            )
            kb = add_server_confirm_kb()
        else:
            retry_hint = (
                "Проверьте URL и API-ключ. Сервер можно сохранить только после "
                "успешной проверки."
                if auth_method == AUTH_API_TOKEN
                else "Проверьте введённые данные или сохраните сервер для настройки позже."
            )
            text = (
                f"❌ <b>Ошибка подключения</b>\n\n"
                f"<code>{escape_html(test_result['message'])}</code>\n\n"
                f"{retry_hint}"
            )
            kb = add_server_test_failed_kb(
                allow_save_anyway=auth_method != AUTH_API_TOKEN,
            )
        
        await safe_edit_or_send(message, text, reply_markup=kb, force_new=True)


# Handlers for each add state
@router.message(AdminStates.add_server_name)
async def add_server_name_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_url)
async def add_server_url_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_api_token)
async def add_server_api_token_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_login)
async def add_server_login_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_password)
async def add_server_password_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.callback_query(F.data == "admin_server_add_test")
async def add_server_retest(callback: CallbackQuery, state: FSMContext):
    """Recheck the connection."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    
    await safe_edit_or_send(callback.message, 
        "⏳ <b>Проверка подключения...</b>"
    )
    
    test_result = await test_server_connection(server_data)
    await state.update_data(
        server_data=server_data,
        connection_test_passed=bool(test_result['success']),
    )
    
    if test_result['success']:
        stats = test_result.get('stats', {})
        traffic = format_traffic(stats.get('total_traffic_bytes', 0))
        
        text = (
            f"✅ <b>Проверка подключения успешна!</b>\n\n"
            f"📊 Статистика:\n"
            f"   🔑 Онлайн: {stats.get('online_clients', 0)}\n"
            f"   📈 Трафик: {traffic}\n\n"
            f"Сохранить сервер?"
        )
        kb = add_server_confirm_kb()
    else:
        retry_hint = (
            "Проверьте URL и API-ключ. Сервер можно сохранить только после "
            "успешной проверки."
            if auth_method == AUTH_API_TOKEN
            else "Проверьте введённые данные или сохраните сервер для настройки позже."
        )
        text = (
            f"❌ <b>Ошибка подключения</b>\n\n"
            f"<code>{escape_html(test_result['message'])}</code>\n\n"
            f"{retry_hint}"
        )
        kb = add_server_test_failed_kb(
            allow_save_anyway=auth_method != AUTH_API_TOKEN,
        )
    
    await safe_edit_or_send(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_save")
async def add_server_save(callback: CallbackQuery, state: FSMContext):
    """Saves the new server."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)

    if auth_method == AUTH_API_TOKEN and not data.get('connection_test_passed'):
        await callback.answer(
            "❌ Сначала успешно проверьте API-ключ",
            show_alert=True,
        )
        return
    
    try:
        server_id = add_server(
            name=server_data['name'],
            host=server_data['host'],
            port=server_data['port'],
            web_base_path=server_data['web_base_path'],
            login=server_data.get('login', ''),
            password=server_data.get('password', ''),
            protocol=server_data.get('protocol', 'https'),
            group_id=data.get('selected_group_id', 1),
            api_token=server_data.get('api_token'),
            panel_version=server_data.get('panel_version'),
            panel_api_profile=server_data.get('panel_api_profile'),
        )
        
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Сервер успешно добавлен!</b>\n\n"
            f"🖥️ {escape_html(server_data['name'])}\n"
            f"🔗 <code>{escape_html(server_data.get('protocol', 'https'))}://"
            f"{escape_html(server_data['host'])}:{server_data['port']}"
            f"{escape_html(server_data['web_base_path'])}</code>"
        )
        
        # Show the server in a second
        await callback.answer("✅ Сервер добавлен!")
        
        # Redirect to view the new server
        # Redirect to view the new server
        await render_server_view(callback.message, server_id, state)
        
    except Exception as e:
        logger.error(f"Ошибка добавления сервера: {e}")
        await safe_edit_or_send(callback.message, 
            f"❌ <b>Ошибка сохранения</b>\n\n<code>{escape_html(str(e))}</code>",
            reply_markup=back_and_home_kb("admin_servers")
        )
        await callback.answer("❌ Ошибка", show_alert=True)


# ============================================================================
# EDITING THE SERVER
# ============================================================================

def get_edit_text(server: dict, current_param: int, auth_method: str) -> str:
    """Generates text for the editing screen."""
    param = get_param_by_index(current_param, auth_method)
    total = get_total_params(auth_method)
    
    # Get the current value
    if param['key'] == 'panel_url':
        current_value = f"{server.get('protocol', 'https')}://{server.get('host', '')}:{server.get('port', '')}{server.get('web_base_path', '')}"
    else:
        current_value = server.get(param['key'], '')
    
    display_value = _masked_server_value(param['key'], current_value)
    
    lines = [
        f"✏️ <b>Редактирование: {escape_html(server['name'])}</b> ({current_param + 1}/{total})\n",
        f"📌 Параметр: <b>{param['label']}</b>",
        f"📝 Текущее значение: <code>{display_value}</code>\n",
        f"Введите новое значение или используйте кнопки навигации:",
        f"<i>({escape_html(param['hint'])})</i>"
    ]
    
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_server_edit:"))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    """Starts editing the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    auth_method = get_server_auth_method(server)
    total_params = get_total_params(auth_method)
    await state.set_state(AdminStates.edit_server)
    await state.update_data(
        server_id=server_id,
        edit_param=0,
        edit_auth_method=auth_method,
    )
    
    text = get_edit_text(server, 0, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_server_kb(0, total_params)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_prev")
async def edit_server_prev(callback: CallbackQuery, state: FSMContext):
    """Previous parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    new_param = max(0, current_param - 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_server_kb(new_param, get_total_params(auth_method))
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_next")
async def edit_server_next(callback: CallbackQuery, state: FSMContext):
    """Next parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    new_param = min(get_total_params(auth_method) - 1, current_param + 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_server_kb(new_param, get_total_params(auth_method))
    )
    await callback.answer()


@router.message(AdminStates.edit_server)
@regular_panel_operation
async def edit_server_value(message: Message, state: FSMContext):
    """Handles the entry of a new value when editing."""
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    param = get_param_by_index(current_param, auth_method)
    value = get_message_text_for_storage(message, 'plain')
    
    # Validation
    if not param['validate'](value):
        await safe_edit_or_send(message,
            f"❌ {param['error']}"
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
                
            # We save all 4 parameters in the database
            update_server_field(server_id, 'protocol', protocol)
            update_server_field(server_id, 'host', host)
            update_server_field(server_id, 'port', port)
            success = update_server_field(server_id, 'web_base_path', path)
        except Exception as e:
            await safe_edit_or_send(message,
                "❌ Неверный формат ссылки. Убедитесь, что указан хост и по умолчанию подставляется <code>https://</code>.\nПример: <code>123.45.67.89:2053/api/</code>"
            )
            return
    elif param['key'] == 'api_token':
        candidate = dict(get_server_by_id(server_id) or {})
        candidate['api_token'] = value
        try:
            await message.delete()
        except Exception:
            pass

        test_result = await test_server_connection(candidate)
        if not test_result['success']:
            await safe_edit_or_send(
                message,
                "❌ <b>API-ключ не сохранён</b>\n\n"
                f"<code>{escape_html(test_result['message'])}</code>\n\n"
                "Проверьте токен и повторите ввод.",
                reply_markup=edit_server_kb(
                    current_param,
                    get_total_params(auth_method),
                ),
                force_new=True,
            )
            return

        success = update_server_field(server_id, 'api_token', value)
    else:
        # Conversion
        if 'convert' in param:
            value = param['convert'](value)
        
        # Saving in the database
        success = update_server_field(server_id, param['key'], value)
    
    if not success:
        await safe_edit_or_send(message, "❌ Ошибка сохранения")
        return
    
    # Resetting the client cache (the settings have changed)
    await invalidate_client_cache(server_id)
    
    # Deleting a user's message
    try:
        await message.delete()
    except:
        pass
    
    # Refresh the screen with the new value
    server = get_server_by_id(server_id)
    text = get_edit_text(server, current_param, auth_method)
    
    await safe_edit_or_send(message,
        f"✅ <b>{param['label']}</b> обновлено!\n\n" + text,
        reply_markup=edit_server_kb(current_param, get_total_params(auth_method)),
        force_new=True
    )


@router.callback_query(F.data == "admin_server_edit_done")
async def edit_server_done(callback: CallbackQuery, state: FSMContext):
    """Finish editing - return to viewing."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    # Redirect to view server
    # Redirect to view server
    await render_server_view(callback.message, server_id, state)


@router.callback_query(F.data == "admin_server_edit_cancel")
async def edit_server_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel editing - return to viewing."""
    await edit_server_done(callback, state)


# ============================================================================
# ACTIVATION / DEACTIVATION
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_toggle:"))
@regular_panel_operation
async def toggle_server(callback: CallbackQuery, state: FSMContext):
    """Toggles server activity."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    new_status = toggle_server_active(server_id)
    
    if new_status is None:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    # Resetting the cache
    await invalidate_client_cache(server_id)
    
    status_text = "активирован 🟢" if new_status else "деактивирован 🔴"
    await callback.answer(f"Сервер {status_text}")
    
    # Refresh the viewing screen
    # Refresh the viewing screen
    await render_server_view(callback.message, server_id, state)


# ============================================================================
# DELETING A SERVER
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_delete:"))
async def confirm_delete_server(callback: CallbackQuery, state: FSMContext):
    """Requests confirmation of deletion."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.delete_server_confirm)
    
    await safe_edit_or_send(callback.message, 
        f"🗑️ <b>Удаление сервера</b>\n\n"
        f"Вы уверены, что хотите удалить сервер?\n\n"
        f"🖥️ <b>{server['name']}</b>\n"
        f"🔗 `{server.get('protocol', 'https')}://{server['host']}:{server['port']}{server['web_base_path']}`\n\n"
        f"⚠️ _Это действие нельзя отменить!_",
        reply_markup=confirm_delete_kb(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_delete_confirm:"))
@regular_panel_operation
async def execute_delete_server(callback: CallbackQuery, state: FSMContext):
    """Deletes the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    server_name = server['name']
    
    # Delete
    success = delete_server(server_id)
    
    if success:
        # Resetting the cache
        await invalidate_client_cache(server_id)
        
        await safe_edit_or_send(callback.message, 
            f"✅ <b>Сервер удалён</b>\n\n"
            f"🖥️ {server_name}"
        )
        await callback.answer("✅ Сервер удалён")
        
        # Return to list
        await show_servers_list(callback, state)
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)


# ============================================================================
# CHANGING SERVER GROUPS (toggle many-to-many)
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_change_group:"))
async def server_change_group_start(callback: CallbackQuery, state: FSMContext):
    """Shows group toggle screen for the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)

    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return

    groups = get_all_groups()
    selected = get_server_group_ids(server_id)

    group_names = [g['name'] for g in groups if g['id'] in selected]
    groups_str = ", ".join(group_names) if group_names else "Основная"

    await safe_edit_or_send(callback.message, 
        f"📂 <b>Группы сервера «{server['name']}»</b>\n\n"
        f"Текущие группы: <b>{groups_str}</b>\n\n"
        "Нажмите на группу чтобы добавить или убрать:",
        reply_markup=server_groups_kb(server_id, groups, selected)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_toggle_group:"))
async def server_toggle_group(callback: CallbackQuery, state: FSMContext):
    """Switches the server's group membership."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    parts = callback.data.split(":")
    server_id = int(parts[1])
    group_id = int(parts[2])

    now_in_group = toggle_server_group(server_id, group_id)

    group = get_group_by_id(group_id)
    group_name = group['name'] if group else str(group_id)

    if now_in_group:
        await callback.answer(f"✅ Добавлен в «{group_name}»")
    else:
        await callback.answer(f"➖ Убран из «{group_name}»")

    # Refresh the screen with an updated list of checkmarks
    server = get_server_by_id(server_id)
    groups = get_all_groups()
    selected = get_server_group_ids(server_id)
    group_names = [g['name'] for g in groups if g['id'] in selected]
    groups_str = ", ".join(group_names) if group_names else "Основная"

    await safe_edit_or_send(callback.message, 
        f"📂 <b>Группы сервера «{server['name']}»</b>\n\n"
        f"Текущие группы: <b>{groups_str}</b>\n\n"
        "Нажмите на группу чтобы добавить или убрать:",
        reply_markup=server_groups_kb(server_id, groups, selected)
    )
