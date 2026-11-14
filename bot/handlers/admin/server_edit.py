"""Stage server settings in one administrator dialog and validate before commit."""

from __future__ import annotations

import datetime
import logging
import urllib.parse
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from localization import text as _tr
from database.requests import get_server_by_id, update_server
from bot.keyboards.admin_servers import edit_server_kb
from bot.services.panel_auth_notifications import panel_endpoint_key
from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.services.vpn_api import invalidate_client_cache, test_server_connection
from bot.states.admin_states import AdminStates, get_param_by_index, get_total_params
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import render_admin_dialog, render_admin_dialog_from_input
from bot.utils.admin_monitoring_view import begin_monitoring_turn
from bot.utils.text import escape_html, get_message_text_for_storage
from . import servers

router = Router()
logger = logging.getLogger(__name__)
_saving: set[str] = set()
_CONNECTION_FIELDS = {'protocol', 'host', 'port', 'web_base_path', 'login', 'password', 'api_token'}


async def _draft(state: FSMContext) -> dict | None:
    if await state.get_state() != AdminStates.edit_server.state:
        return None
    data = await state.get_data()
    draft = data.get('server_edit')
    if not isinstance(draft, dict) or not draft.get('token'):
        return None
    return dict(draft, values=dict(draft['values']), changes=dict(draft['changes']))


async def _current(state: FSMContext, token: str) -> bool:
    draft = await _draft(state)
    return draft is not None and draft['token'] == token


async def _callback_draft(callback: CallbackQuery, state: FSMContext, *, cancel=False):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return None
    draft = await _draft(state)
    data = await state.get_data()
    if (draft is None or callback.message is None
            or callback.message.message_id != data.get('_admin_dialog_message_id')):
        await callback.answer(_tr('server_edit.closed'), show_alert=True)
        return None
    if draft['token'] in _saving and not cancel:
        await callback.answer(_tr('server_edit.checking_short'))
        return None
    return draft


def _keyboard(draft: dict, *, busy=False):
    return edit_server_kb(
        draft['index'], get_total_params(draft['auth_method']),
        allow_clear_group=get_param_by_index(draft['index'], draft['auth_method'])['key'] == 'inbound_group_id',
        busy=busy,
    )


def _text(draft: dict, error: str = '') -> str:
    server = draft['values']
    param = get_param_by_index(draft['index'], draft['auth_method'])
    if param['key'] == 'panel_url':
        value = f"{server.get('protocol', 'https')}://{server['host']}:{server['port']}{server['web_base_path']}"
    else:
        value = server.get(param['key'], '')
    lines = [
        _tr('servers.get_edit_text.1', p0=escape_html(server['name']),
            p1=draft['index'] + 1, p2=get_total_params(draft['auth_method'])),
    ]
    if error:
        lines.extend([error, ''])
    lines.extend([
        _tr('servers.get_edit_text.2', p0=param['label']),
        _tr('server_edit.value', value=servers._masked_server_value(param['key'], value)),
        servers._inbound_group_help_text(allow_skip=False) if param['key'] == 'inbound_group_id'
        else _tr('servers.get_edit_text.4') + '\n<i>' + escape_html(param['hint']) + '</i>',
        '', _tr('server_edit.hint'),
    ])
    return '\n'.join(lines)


async def _show(message: Message, state: FSMContext, draft: dict, *, error='', input_message=False):
    render = render_admin_dialog_from_input if input_message else render_admin_dialog
    return await render(message, state, _text(draft, error), reply_markup=_keyboard(draft))


async def _finish(message: Message, state: FSMContext, server_id: int, *, notice=None):
    await state.clear()
    # A duplicate Save acknowledges the in-flight check and supersedes its
    # monitoring turn, but the editor still owns the successful return screen.
    turn = await begin_monitoring_turn(state)
    if get_server_by_id(server_id):
        await servers.render_server_view(message, server_id, state, turn=turn, notice=notice)
    else:
        await state.set_state(AdminStates.servers_list)
        await servers.render_servers_list(message, state, turn=turn, notice=_tr('servers.show_server_view.1'))


@router.callback_query(F.data.startswith('admin_server_edit:'))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if server is None:
        await callback.answer(_tr('servers.show_server_view.1'), show_alert=True)
        return
    await callback.answer()
    draft = dict(token=uuid.uuid4().hex, server_id=server_id, index=0,
                 auth_method=servers.get_server_auth_method(server), values=server, changes={})
    await state.set_state(AdminStates.edit_server)
    await state.set_data({'server_id': server_id, 'server_edit': draft})
    await _show(callback.message, state, draft)


@router.callback_query(F.data.in_({'admin_server_edit_prev', 'admin_server_edit_next'}))
async def edit_server_step(callback: CallbackQuery, state: FSMContext):
    draft = await _callback_draft(callback, state)
    if draft is None:
        return
    step = -1 if callback.data.endswith('_prev') else 1
    draft['index'] = max(0, min(get_total_params(draft['auth_method']) - 1, draft['index'] + step))
    await state.update_data(server_edit=draft)
    await callback.answer()
    await _show(callback.message, state, draft)


@router.callback_query(F.data == 'admin_server_edit_clear_inbound_group')
async def clear_server_inbound_group(callback: CallbackQuery, state: FSMContext):
    draft = await _callback_draft(callback, state)
    if draft is None:
        return
    if get_param_by_index(draft['index'], draft['auth_method'])['key'] != 'inbound_group_id':
        await callback.answer(_tr('servers.clear_server_inbound_group.1'), show_alert=True)
        return
    draft['values']['inbound_group_id'] = None
    draft['changes']['inbound_group_id'] = None
    await state.update_data(server_edit=draft)
    await callback.answer()
    await _show(callback.message, state, draft)


def _parse_value(key: str, value: str, param: dict) -> dict:
    if key != 'panel_url':
        return {key: param['convert'](value) if 'convert' in param else value}
    url = value if value.startswith(('http://', 'https://')) else 'https://' + value
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        raise ValueError('Missing panel host')
    path = parsed.path
    return dict(protocol=parsed.scheme, host=parsed.hostname,
                port=parsed.port or (443 if parsed.scheme == 'https' else 80),
                web_base_path=path if path.endswith('/') else path + '/')


@router.message(AdminStates.edit_server, ~Command('start'))
async def edit_server_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    draft = await _draft(state)
    if draft is None or draft['token'] in _saving:
        try:
            await message.delete()
        except Exception:
            pass
        return
    value = get_message_text_for_storage(message, 'plain')
    param = get_param_by_index(draft['index'], draft['auth_method'])
    if not param['validate'](value):
        await _show(message, state, draft, error=param['error'], input_message=True)
        return
    try:
        updates = _parse_value(param['key'], value, param)
    except (ValueError, TypeError):
        await _show(message, state, draft, error=_tr('servers.edit_server_value.1'), input_message=True)
        return
    draft['values'].update(updates)
    draft['changes'].update(updates)
    await state.update_data(server_edit=draft)
    await _show(message, state, draft, input_message=True)


def _connection_candidate(server: dict, updates: dict, auth_method: str) -> dict:
    candidate = dict(server, **updates)
    candidate['id'] = None
    if auth_method == servers.AUTH_API_TOKEN:
        candidate['login'] = candidate['password'] = ''
    elif {'login', 'password'} & updates.keys():
        candidate['api_token'] = None
        candidate['_force_auth_bootstrap'] = True
    elif panel_endpoint_key(candidate) != panel_endpoint_key(server):
        candidate['api_token'] = None
    return candidate


@router.callback_query(F.data == 'admin_server_edit_done')
@regular_panel_operation
async def edit_server_done(callback: CallbackQuery, state: FSMContext):
    draft = await _callback_draft(callback, state)
    if draft is None:
        return
    token = draft['token']
    _saving.add(token)
    target = callback.message
    try:
        await callback.answer()
        if not await _current(state, token):
            return
        server = get_server_by_id(draft['server_id'])
        if server is None:
            await _finish(target, state, draft['server_id'])
            return
        updates = {key: value for key, value in draft['changes'].items() if value != server.get(key)}
        if not updates:
            await _finish(target, state, draft['server_id'], notice=_tr('server_edit.unchanged'))
            return
        if updates.keys() & _CONNECTION_FIELDS:
            candidate = _connection_candidate(server, updates, draft['auth_method'])
            target = await render_admin_dialog(target, state, _tr('server_edit.checking'),
                                               reply_markup=_keyboard(draft, busy=True))
            if not await _current(state, token):
                return
            result = await test_server_connection(candidate)
            if not await _current(state, token):
                return
            if not result['success']:
                await _show(target, state, draft, error=_tr('server_edit.failed'))
                return
            updates.update(api_token=candidate.get('api_token'), panel_version=candidate.get('panel_version'),
                           panel_checked_at=datetime.datetime.now(datetime.timezone.utc)
                           .replace(microsecond=0).isoformat().replace('+00:00', 'Z'))
        if not await _current(state, token):
            return
        if not get_server_by_id(draft['server_id']):
            await _finish(target, state, draft['server_id'])
            return
        try:
            saved = update_server(draft['server_id'], **updates)
        except Exception:
            logger.exception('Could not save server settings server_id=%s', draft['server_id'])
            saved = False
        if not saved:
            await _show(target, state, draft, error=_tr('servers.edit_server_value.2'))
            return
        await invalidate_client_cache(draft['server_id'])
        if await _current(state, token):
            await _finish(target, state, draft['server_id'], notice=_tr('server_edit.saved'))
    finally:
        _saving.discard(token)


@router.callback_query(F.data == 'admin_server_edit_cancel')
async def edit_server_cancel(callback: CallbackQuery, state: FSMContext):
    draft = await _callback_draft(callback, state, cancel=True)
    if draft is None:
        return
    await callback.answer()
    if await _current(state, draft['token']):
        await _finish(callback.message, state, draft['server_id'])
