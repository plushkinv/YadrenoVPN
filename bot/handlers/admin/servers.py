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

from localization import text as _tr
import logging
import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import urllib.parse

from config import ADMIN_IDS
from database.requests import (
    get_all_servers,
    get_server_by_id,
    add_server,
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
from bot.utils.admin_dialog import (
    render_admin_dialog,
    render_admin_dialog_from_input,
)
from bot.services.admin_monitoring import (
    build_servers_monitoring_text,
    AdminMonitoringRead,
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

from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from bot.utils.admin_monitoring_view import monitoring_turn, show_monitoring_screen

router = Router()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================




async def render_servers_list(message: Message, state: FSMContext, *, turn=None):
    turn = turn or await monitoring_turn(state)
    await show_monitoring_screen(
        message, turn, AdminMonitoringRead(),
        lambda snapshot: (build_servers_monitoring_text(snapshot), servers_list_kb(snapshot['records'])),
        deliver=safe_edit_or_send,
    )


async def render_server_view(message: Message, server_id: int, state: FSMContext):
    """Renders the server browsing screen."""
    turn = await monitoring_turn(state)
    if not turn.current() or not get_server_by_id(server_id):
        return
    await state.set_state(AdminStates.server_view)
    await state.update_data(server_id=server_id)
    await show_monitoring_screen(
        message, turn, AdminMonitoringRead(server_id), build_server_view,
        deliver=safe_edit_or_send,
    )


def build_server_view(snapshot):
    """Format local server settings and the current opening's monitoring only."""
    if not snapshot['records']:
        return None
    server = snapshot['records'][0]
    server_id = server['id']

    status_emoji = "🟢" if server['is_active'] else "🔴"
    status_text = _tr('servers.build_server_view.1') if server['is_active'] else _tr('servers.build_server_view.2')
    auth_method = get_server_auth_method(server)

    lines = [
        f"🖥️ <b>{escape_html(server['name'])}</b>\n",
        _tr('servers.build_server_view.3', p0=f"{escape_html(server.get('protocol', 'https'))}", p1=f"{escape_html(server['host'])}", p2=f"{server['port']}", p3=f"{escape_html(server['web_base_path'])}"),
        (
            _tr('servers.build_server_view.4', p0=f"{server['inbound_group_id']}")
            if server.get('inbound_group_id') is not None
            else _tr('servers.build_server_view.5')
        ),
        _tr('servers.build_server_view.8', p0=f"{(_tr('servers.build_server_view.6') if auth_method == AUTH_API_TOKEN else _tr('servers.build_server_view.7'))}"),
    ]
    if auth_method == AUTH_API_TOKEN:
        token_status = _tr('servers.build_server_view.9') if server.get('api_token') else _tr('servers.build_server_view.10')
        lines.append(_tr('servers.build_server_view.11', p0=f'{token_status}'))
    else:
        password_masked = "•" * min(len(server.get('password') or ''), 8)
        lines.extend([
            _tr('servers.build_server_view.12', p0=f"{escape_html(server.get('login') or '')}"),
            _tr('servers.build_server_view.13', p0=f'{password_masked}'),
        ])

    lines.extend([
        f"🧩 <b>3x-ui API:</b>",
        _tr('servers.build_server_view.15', p0=f"{escape_html(server.get('panel_version') or _tr('servers.build_server_view.14'))}"),
        _tr('servers.build_server_view.17', p0=f"{escape_html(server.get('panel_checked_at') or _tr('servers.build_server_view.16'))}"),
        _tr('servers.build_server_view.18'),
        _tr('servers.build_server_view.19', p0=f'{status_emoji}', p1=f'{status_text}'),
    ])

    if server['is_active']:
        entry = snapshot['servers'][0]
        if snapshot['loading']:
            lines.append(_tr('servers.build_server_view.20'))
        elif entry['error'] == _tr('admin_monitoring.collect_server_entry.1'):
            lines.append(_tr('servers.build_server_view.21'))
        else:
            stats = entry['stats']
            if stats.get('online'):
                traffic = format_traffic(stats.get('total_traffic_bytes', 0))
                lines.append(_tr('servers.build_server_view.22', p0=f"{stats.get('online_clients', 0)}"))
                lines.append(_tr('servers.build_server_view.23', p0=f'{traffic}'))

                if stats.get('cpu_percent') is not None:
                    lines.append(f"   💻 CPU: {stats['cpu_percent']}%")
            else:
                lines.append(_tr('servers.build_server_view.24'))

    # Groups—show if there is more than one group
    groups_count = get_groups_count()
    if groups_count > 1:
        group_ids = get_server_group_ids(server_id)
        group_names = []
        for gid in group_ids:
            g = get_group_by_id(gid)
            if g:
                group_names.append(g['name'])
        groups_str = ", ".join(group_names) if group_names else _tr('servers.build_server_view.25')
        lines.append(_tr('servers.build_server_view.26', p0=f'{groups_str}'))

    return (
        "\n".join(lines),
        server_view_kb(server_id, server['is_active'], groups_count > 1),
    )


# ============================================================================
# SERVER LIST
# ============================================================================

@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery, state: FSMContext):
    """Shows a list of servers."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    turn = await monitoring_turn(state)
    await callback.answer()
    if not turn.current():
        return
    await state.set_state(AdminStates.servers_list)
    await state.update_data(server_data={})  # Clearing temporary data
    
    await render_servers_list(callback.message, state, turn=turn)


@router.callback_query(F.data == "admin_servers_refresh")
async def refresh_servers_list(callback: CallbackQuery, state: FSMContext):
    """Updates server statistics."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await callback.answer(_tr('servers.refresh_servers_list.1'))
    
    await render_servers_list(callback.message, state)


# ============================================================================
# VIEW SERVER
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_view:"))
async def show_server_view(callback: CallbackQuery, state: FSMContext):
    """Shows server details."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    await callback.answer()
    # Using helper for rendering
    await render_server_view(callback.message, server_id, state)


# ============================================================================
# ADDING A SERVER
# ============================================================================

AUTH_API_TOKEN = "api_token"
AUTH_LOGIN_PASSWORD = "login_password"

ADD_STATES = {
    AUTH_API_TOKEN: [
        AdminStates.add_server_name,
        AdminStates.add_server_url,
        AdminStates.add_server_inbound_group,
        AdminStates.add_server_api_token,
    ],
    AUTH_LOGIN_PASSWORD: [
        AdminStates.add_server_name,
        AdminStates.add_server_url,
        AdminStates.add_server_inbound_group,
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
    return AUTH_LOGIN_PASSWORD if has_credentials else AUTH_API_TOKEN


def _get_add_states(auth_method: str) -> list:
    """Returns FSM states for one authentication branch."""
    return ADD_STATES.get(auth_method, ADD_STATES[AUTH_LOGIN_PASSWORD])


def _masked_server_value(key: str, value: object) -> str:
    """Returns an HTML-safe value for the add/edit summaries."""
    if key in {'password', 'api_token'}:
        return "•" * min(len(str(value or '')), 8)
    if key == 'inbound_group_id' and value in (None, '', '—'):
        return _tr('servers.masked_server_value.1')
    return escape_html(str(value if value is not None else '—'))


def _inbound_group_display(server_data: dict) -> str:
    value = server_data.get('inbound_group_id')
    return str(value) if value is not None else _tr('servers.masked_server_value.1')


def _connection_success_text(server_data: dict, stats: dict) -> str:
    traffic = format_traffic(stats.get('total_traffic_bytes', 0))
    return (
        _tr('servers.connection_success_text.1', p0=f"{stats.get('online_clients', 0)}", p1=f'{traffic}', p2=f'{_inbound_group_display(server_data)}')
    )


def _is_inbound_group_step(step: int, auth_method: str) -> bool:
    return get_param_by_index(step - 1, auth_method)['key'] == 'inbound_group_id'


def _add_step_keyboard(step: int, auth_method: str):
    return add_server_step_kb(
        step,
        get_total_params(auth_method),
        allow_skip=_is_inbound_group_step(step, auth_method),
    )


def _edit_step_keyboard(current_param: int, auth_method: str):
    return edit_server_kb(
        current_param,
        get_total_params(auth_method),
        allow_clear_group=(
            get_param_by_index(current_param, auth_method)['key']
            == 'inbound_group_id'
        ),
    )


def _inbound_group_help_text(*, allow_skip: bool) -> str:
    tail = (
        _tr('servers.inbound_group_help_text.1')
        if allow_skip
        else _tr('servers.inbound_group_help_text.2')
    )
    return (
        _tr('servers.inbound_group_help_text.3', p0=f'{tail}')
    )


def get_add_step_text(
    step: int,
    data: dict,
    auth_method: str,
    *,
    error: str | None = None,
) -> str:
    """Generates text for the add server step."""
    param = get_param_by_index(step - 1, auth_method)
    total = get_total_params(auth_method)
    
    title = (
        _tr('servers.get_add_step_text.1')
        if param['key'] == 'inbound_group_id'
        else _tr('servers.get_add_step_text.2')
    )
    lines = [f"{title} ({step}/{total})\n"]

    if error:
        lines.append(f"❌ {error}\n")
    
    # Showing already entered data
    for i in range(step - 1):
        p = get_param_by_index(i, auth_method)
        value = data.get(p['key'], '—')
        lines.append(f"✅ {p['label']}: <code>{_masked_server_value(p['key'], value)}</code>")
    
    if step > 1:
        lines.append("")
    
    if param['key'] == 'inbound_group_id':
        lines.append(_inbound_group_help_text(allow_skip=True))
    else:
        lines.append(_tr('servers.get_add_step_text.3', p0=f"{param['label'].lower()}"))
        lines.append(f"<i>({escape_html(param['hint'])})</i>")
    
    return "\n".join(lines)


async def render_add_auth_method(
    message: Message,
    state: FSMContext,
    *,
    reset: bool,
) -> Message:
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
    return await render_admin_dialog(
        message,
        state,
        _tr('servers.render_add_auth_method.1'),
        reply_markup=add_server_auth_method_kb(),
    )


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    """Starts the Add Server dialog."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await render_add_auth_method(callback.message, state, reset=True)
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_method")
async def show_add_server_auth_method(callback: CallbackQuery, state: FSMContext):
    """Returns from group/field selection to the authentication choice."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        await render_admin_dialog(
            callback.message,
            state,
            _tr('servers.select_server_auth_method.1'),
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
    await render_admin_dialog(
        callback.message,
        state,
        text,
        reply_markup=_add_step_keyboard(1, auth_method),
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
    group_name = group['name'] if group else _tr('servers.build_server_view.25')
    
    text = (
        _tr('servers.server_group_selected.1', p0=f'{escape_html(group_name)}')
        + get_add_step_text(1, server_data, auth_method)
    )
    
    await render_admin_dialog(callback.message, state,
        text,
        reply_markup=_add_step_keyboard(1, auth_method)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_back")
async def add_server_back(callback: CallbackQuery, state: FSMContext):
    """Return to the previous adding step."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
    
    await render_admin_dialog(callback.message, state,
        text,
        reply_markup=_add_step_keyboard(new_step, auth_method)
    )
    await callback.answer()


@router.callback_query(
    AdminStates.add_server_inbound_group,
    F.data == "admin_server_add_skip_inbound_group",
)
async def skip_add_server_inbound_group(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Keep legacy all-inbound behavior and continue the add-server wizard."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    data = await state.get_data()
    current_step = int(data.get('add_step', 1))
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    states = _get_add_states(auth_method)
    if not _is_inbound_group_step(current_step, auth_method):
        await callback.answer(_tr('servers.skip_add_server_inbound_group.1'), show_alert=True)
        return

    server_data = dict(data.get('server_data', {}))
    server_data['inbound_group_id'] = None
    new_step = current_step + 1
    await state.set_state(states[new_step - 1])
    await state.update_data(
        server_data=server_data,
        add_step=new_step,
        connection_test_passed=False,
    )
    await render_admin_dialog(
        callback.message,
        state,
        get_add_step_text(new_step, server_data, auth_method),
        reply_markup=_add_step_keyboard(new_step, auth_method),
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

    param = get_param_by_index(current_step - 1, auth_method)
    value = get_message_text_for_storage(message, 'plain')

    # Validation
    if not param['validate'](value):
        await render_admin_dialog_from_input(
            message,
            state,
            get_add_step_text(
                current_step,
                server_data,
                auth_method,
                error=param['error'],
            ),
            reply_markup=_add_step_keyboard(current_step, auth_method),
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
                raise ValueError(_tr('servers.process_add_step.1'))
                
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
            
        except Exception:
            await render_admin_dialog_from_input(
                message,
                state,
                get_add_step_text(
                    current_step,
                    server_data,
                    auth_method,
                    error=(
                        _tr('servers.process_add_step.2')
                    ),
                ),
                reply_markup=_add_step_keyboard(current_step, auth_method),
            )
            return
    else:
        # Conversion (if necessary)
        if 'convert' in param:
            value = param['convert'](value)
        
        # Saving the value
        server_data[param['key']] = value

    await state.update_data(server_data=server_data)
    
    # Move to next step or confirmation
    if current_step < total_params:
        new_step = current_step + 1
        await state.set_state(states[new_step - 1])
        await state.update_data(add_step=new_step)
        
        text = get_add_step_text(new_step, server_data, auth_method)
        
        await render_admin_dialog_from_input(message, state,
            text,
            reply_markup=_add_step_keyboard(new_step, auth_method),
        )
    else:
        # All data has been entered - check the connection
        await state.set_state(AdminStates.add_server_confirm)
        await state.update_data(add_step=total_params + 1, connection_test_passed=False)
        
        target = await render_admin_dialog_from_input(message, state,
            _tr('servers.process_add_step.3'),
        )
        
        # Testing the connection
        test_result = await test_server_connection(server_data)
        await state.update_data(
            server_data=server_data,
            connection_test_passed=bool(test_result['success']),
        )
        
        if test_result['success']:
            stats = test_result.get('stats', {})
            text = _connection_success_text(server_data, stats)
            kb = add_server_confirm_kb()
        else:
            text = _tr('servers.process_add_step.4')
            kb = add_server_test_failed_kb()
        
        await render_admin_dialog(target, state, text, reply_markup=kb)


# Handlers for each add state
@router.message(AdminStates.add_server_name)
async def add_server_name_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_url)
async def add_server_url_handler(message: Message, state: FSMContext):
    await process_add_step(message, state)


@router.message(AdminStates.add_server_inbound_group)
async def add_server_inbound_group_handler(message: Message, state: FSMContext):
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)
    
    target = await render_admin_dialog(callback.message, state,
        _tr('servers.process_add_step.3')
    )
    
    test_result = await test_server_connection(server_data)
    await state.update_data(
        server_data=server_data,
        connection_test_passed=bool(test_result['success']),
    )
    
    if test_result['success']:
        stats = test_result.get('stats', {})
        text = _connection_success_text(server_data, stats)
        kb = add_server_confirm_kb()
    else:
        text = _tr('servers.process_add_step.4')
        kb = add_server_test_failed_kb()
    
    await render_admin_dialog(target, state, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_server_add_save")
async def add_server_save(callback: CallbackQuery, state: FSMContext):
    """Saves the new server."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    data = await state.get_data()
    server_data = data.get('server_data', {})
    auth_method = data.get('auth_method', AUTH_LOGIN_PASSWORD)

    if not data.get('connection_test_passed'):
        await callback.answer(
            _tr('servers.add_server_save.1'),
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
            inbound_group_id=server_data.get('inbound_group_id'),
            api_token=server_data.get('api_token'),
            panel_version=server_data.get('panel_version'),
        )
        
        target = await render_admin_dialog(callback.message, state,
            _tr('servers.add_server_save.2', p0=f"{escape_html(server_data['name'])}", p1=f"{escape_html(server_data.get('protocol', 'https'))}", p2=f"{escape_html(server_data['host'])}", p3=f"{server_data['port']}", p4=f"{escape_html(server_data['web_base_path'])}", p5=f'{_inbound_group_display(server_data)}')
        )
        
        # Show the server in a second
        await callback.answer(_tr('servers.add_server_save.3'))
        
        # Redirect to view the new server
        # Redirect to view the new server
        await render_server_view(target, server_id, state)
        
    except Exception as e:
        logger.error(f"Ошибка добавления сервера: {e}")
        await render_admin_dialog(callback.message, state,
            _tr('servers.add_server_save.4', p0=f'{escape_html(str(e))}'),
            reply_markup=back_and_home_kb("admin_servers")
        )
        await callback.answer(_tr('servers.add_server_save.5'), show_alert=True)


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
        _tr('servers.get_edit_text.1', p0=f"{escape_html(server['name'])}", p1=f'{current_param + 1}', p2=f'{total}'),
        _tr('servers.get_edit_text.2', p0=f"{param['label']}"),
        _tr('servers.get_edit_text.3', p0=f'{display_value}'),
    ]
    if param['key'] == 'inbound_group_id':
        lines.append(_inbound_group_help_text(allow_skip=False))
    else:
        lines.extend([
            _tr('servers.get_edit_text.4'),
            f"<i>({escape_html(param['hint'])})</i>",
        ])
    
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_server_edit:"))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    """Starts editing the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    auth_method = get_server_auth_method(server)
    await state.set_state(AdminStates.edit_server)
    await state.update_data(
        server_id=server_id,
        edit_param=0,
        edit_auth_method=auth_method,
    )
    
    text = get_edit_text(server, 0, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=_edit_step_keyboard(0, auth_method)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_clear_inbound_group")
async def clear_server_inbound_group(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Restore legacy all-inbound behavior for one logical server."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = int(data.get('edit_param', 0))
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    if (
        get_param_by_index(current_param, auth_method)['key']
        != 'inbound_group_id'
    ):
        await callback.answer(_tr('servers.clear_server_inbound_group.1'), show_alert=True)
        return
    if not update_server(server_id, inbound_group_id=None):
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return

    await invalidate_client_cache(server_id)
    server = get_server_by_id(server_id)
    await safe_edit_or_send(
        callback.message,
        _tr('servers.clear_server_inbound_group.2')
        + get_edit_text(server, current_param, auth_method),
        reply_markup=_edit_step_keyboard(current_param, auth_method),
    )
    await callback.answer(_tr('servers.clear_server_inbound_group.3'))


@router.callback_query(F.data == "admin_server_edit_prev")
async def edit_server_prev(callback: CallbackQuery, state: FSMContext):
    """Previous parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    new_param = max(0, current_param - 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=_edit_step_keyboard(new_param, auth_method)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_server_edit_next")
async def edit_server_next(callback: CallbackQuery, state: FSMContext):
    """Next parameter when editing."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    current_param = data.get('edit_param', 0)
    auth_method = data.get('edit_auth_method', AUTH_LOGIN_PASSWORD)
    
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    new_param = min(get_total_params(auth_method) - 1, current_param + 1)
    await state.update_data(edit_param=new_param)
    
    text = get_edit_text(server, new_param, auth_method)
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=_edit_step_keyboard(new_param, auth_method)
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
    
    server = get_server_by_id(server_id)
    if not server:
        await safe_edit_or_send(message, _tr('servers.show_server_view.1'))
        return

    updates = {}
    if param['key'] == 'panel_url':
        url_str = value
        if not url_str.startswith(('http://', 'https://')):
            url_str = 'https://' + url_str
            
        try:
            parsed = urllib.parse.urlparse(url_str)
            protocol = parsed.scheme
            host = parsed.hostname
            if not host:
                raise ValueError(_tr('servers.process_add_step.1'))
                
            port = parsed.port
            if not port:
                port = 443 if protocol == 'https' else 80
                
            path = parsed.path
            if not path.endswith('/'):
                path += '/'
                
            updates.update({
                'protocol': protocol,
                'host': host,
                'port': port,
                'web_base_path': path,
            })
        except Exception as e:
            await safe_edit_or_send(message,
                _tr('servers.edit_server_value.1')
            )
            return
    else:
        if 'convert' in param:
            value = param['convert'](value)
        updates[param['key']] = value

    connection_fields = {'panel_url', 'api_token', 'login', 'password'}
    if param['key'] in connection_fields:
        candidate = dict(server)
        candidate['id'] = None
        candidate.update(updates)

        # Validate the supplied authentication material itself. A previously
        # valid Bearer must not conceal an invalid replacement password.
        if param['key'] in {'login', 'password'}:
            candidate['api_token'] = None
            candidate['_force_auth_bootstrap'] = True
        elif param['key'] == 'api_token':
            candidate['login'] = ''
            candidate['password'] = ''

        try:
            await message.delete()
        except Exception:
            pass

        test_result = await test_server_connection(candidate)
        if not test_result['success']:
            await safe_edit_or_send(
                message,
                _tr('servers.process_add_step.4'),
                reply_markup=_edit_step_keyboard(current_param, auth_method),
                force_new=True,
            )
            return

        updates['api_token'] = candidate.get('api_token')
        updates['panel_version'] = candidate.get('panel_version')
        updates['panel_checked_at'] = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace('+00:00', 'Z')
        )

    success = update_server(server_id, **updates)
    
    if not success:
        await safe_edit_or_send(message, _tr('servers.edit_server_value.2'))
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
        _tr('servers.edit_server_value.3', p0=f"{param['label']}") + text,
        reply_markup=_edit_step_keyboard(current_param, auth_method),
        force_new=True
    )


@router.callback_query(F.data == "admin_server_edit_done")
async def edit_server_done(callback: CallbackQuery, state: FSMContext):
    """Finish editing - return to viewing."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await callback.answer()
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    new_status = toggle_server_active(server_id)
    
    if new_status is None:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    # Resetting the cache
    await invalidate_client_cache(server_id)
    
    status_text = _tr('servers.toggle_server.1') if new_status else _tr('servers.toggle_server.2')
    await callback.answer(_tr('servers.toggle_server.3', p0=f'{status_text}'))
    
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.delete_server_confirm)
    
    await safe_edit_or_send(callback.message, 
        _tr('servers.confirm_delete_server.1', p0=f"{server['name']}", p1=f"{server.get('protocol', 'https')}", p2=f"{server['host']}", p3=f"{server['port']}", p4=f"{server['web_base_path']}"),
        reply_markup=confirm_delete_kb(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_delete_confirm:"))
@regular_panel_operation
async def execute_delete_server(callback: CallbackQuery, state: FSMContext):
    """Deletes the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    
    server_name = server['name']
    
    # Delete
    success = delete_server(server_id)
    
    if success:
        # Resetting the cache
        await invalidate_client_cache(server_id)
        
        await safe_edit_or_send(callback.message, 
            _tr('servers.execute_delete_server.1', p0=f'{server_name}')
        )
        await callback.answer(_tr('servers.execute_delete_server.2'))
        
        # Return to list
        await show_servers_list(callback, state)
    else:
        await callback.answer(_tr('servers.execute_delete_server.3'), show_alert=True)


# ============================================================================
# CHANGING SERVER GROUPS (toggle many-to-many)
# ============================================================================

@router.callback_query(F.data.startswith("admin_server_change_group:"))
async def server_change_group_start(callback: CallbackQuery, state: FSMContext):
    """Shows group toggle screen for the server."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)

    if not server:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return

    groups = get_all_groups()
    selected = get_server_group_ids(server_id)

    group_names = [g['name'] for g in groups if g['id'] in selected]
    groups_str = ", ".join(group_names) if group_names else _tr('servers.build_server_view.25')

    await safe_edit_or_send(callback.message, 
        _tr('servers.server_change_group_start.1', p0=f"{server['name']}", p1=f'{groups_str}'),
        reply_markup=server_groups_kb(server_id, groups, selected)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_toggle_group:"))
async def server_toggle_group(callback: CallbackQuery, state: FSMContext):
    """Switches the server's group membership."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    parts = callback.data.split(":")
    server_id = int(parts[1])
    group_id = int(parts[2])

    now_in_group = toggle_server_group(server_id, group_id)

    group = get_group_by_id(group_id)
    group_name = group['name'] if group else str(group_id)

    if now_in_group:
        await callback.answer(_tr('servers.server_toggle_group.1', p0=f'{group_name}'))
    else:
        await callback.answer(_tr('servers.server_toggle_group.2', p0=f'{group_name}'))

    # Refresh the screen with an updated list of checkmarks
    server = get_server_by_id(server_id)
    groups = get_all_groups()
    selected = get_server_group_ids(server_id)
    group_names = [g['name'] for g in groups if g['id'] in selected]
    groups_str = ", ".join(group_names) if group_names else _tr('servers.build_server_view.25')

    await safe_edit_or_send(callback.message, 
        _tr('servers.server_change_group_start.1', p0=f"{server['name']}", p1=f'{groups_str}'),
        reply_markup=server_groups_kb(server_id, groups, selected)
    )
