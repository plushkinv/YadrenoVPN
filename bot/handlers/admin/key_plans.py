"""Administrator flow for fully reissuing a VPN key tariff plan."""
from __future__ import annotations

from localization import text as _tr

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    key_action_cancel_kb,
    key_plan_confirm_kb,
    key_plan_custom_confirm_kb,
    key_plan_select_kb,
)
from bot.services.key_plans import reissue_key_plan
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from database.requests import (
    get_admin_custom_tariff,
    get_tariff_by_id,
    get_tariffs_by_group,
    get_vpn_key_by_id,
    get_all_groups,
    is_key_tariff_group_allowed,
)

router = Router()


def _duration_text(days: int) -> str:
    return _tr('key_plans.duration_text.1') if int(days) == 0 else _tr('key_plans.duration_text.2', p0=f'{int(days)}', count=int(days))


def _traffic_text(gigabytes: int) -> str:
    return _tr('key_plans.traffic_text.1') if int(gigabytes) == 0 else _tr('key_plans.traffic_text.2', p0=f'{int(gigabytes)}')


async def _input_target(
    message: Message,
    state: FSMContext,
) -> tuple[Message, dict]:
    """Deletes administrator input and targets the original dialog message."""
    data = await state.get_data()
    message_id = data.get('key_plan_dialog_message_id')
    try:
        target = message.model_copy(update={'message_id': int(message_id)})
    except (TypeError, ValueError):
        target = message
    try:
        await message.delete()
    except Exception:
        pass
    return target, data


@router.callback_query(F.data.startswith('admin_key_change_plan:'))
async def start_key_plan_change(callback: CallbackQuery, state: FSMContext) -> None:
    """Shows all ordinary and protected custom plans in the key group."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    key_id = int(callback.data.split(':', 1)[1])
    key = get_vpn_key_by_id(key_id)
    if key is None:
        await callback.answer(_tr('key_plans.start_key_plan_change.1'), show_alert=True)
        return
    groups = [group for group in get_all_groups() if is_key_tariff_group_allowed(key_id, group['id'])]
    custom_tariffs = [tariff for group in groups if (tariff := get_admin_custom_tariff(group['id']))]
    if not custom_tariffs:
        await callback.answer(_tr('key_plans.start_key_plan_change.2'), show_alert=True)
        return
    tariffs = [tariff for group in groups for tariff in get_tariffs_by_group(
        group['id'], include_hidden=True, include_system=False)]
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    rendered = await safe_edit_or_send(
        callback.message,
        _tr('key_plans.start_key_plan_change.3'),
        reply_markup=key_plan_select_kb(
            key_id,
            tariffs,
            int(custom_tariffs[0]['id']) if len(custom_tariffs) == 1 else custom_tariffs,
        ),
    )
    await state.update_data(
        key_plan_dialog_message_id=getattr(
            rendered,
            'message_id',
            callback.message.message_id,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_plan_select:'))
async def select_key_plan(callback: CallbackQuery, state: FSMContext) -> None:
    """Prepares ordinary confirmation or starts custom plan input."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    _, key_id_raw, tariff_id_raw = callback.data.split(':', 2)
    key_id = int(key_id_raw)
    tariff_id = int(tariff_id_raw)
    key = get_vpn_key_by_id(key_id)
    tariff = get_tariff_by_id(tariff_id)
    if key is None or tariff is None:
        await callback.answer(_tr('key_plans.select_key_plan.1'), show_alert=True)
        return
    if not is_key_tariff_group_allowed(key_id, int(tariff.get('group_id') or 1)):
        await callback.answer(_tr('key_plans.select_key_plan.2'), show_alert=True)
        return

    if tariff.get('system_type') == 'admin_custom':
        await state.set_state(AdminStates.key_plan_custom_traffic)
        await state.update_data(
            current_key_id=key_id,
            key_plan_target_tariff_id=tariff_id,
        )
        rendered = await safe_edit_or_send(
            callback.message,
            _tr('key_plans.select_key_plan.3'),
            reply_markup=key_action_cancel_kb(key_id, key.get('telegram_id')),
        )
        await state.update_data(
            key_plan_dialog_message_id=getattr(
                rendered,
                'message_id',
                callback.message.message_id,
            ),
        )
        await callback.answer()
        return

    if tariff.get('system_type') is not None:
        await callback.answer(_tr('key_plans.select_key_plan.4'), show_alert=True)
        return

    duration = int(tariff.get('duration_days') or 0)
    traffic = int(tariff.get('traffic_limit_gb') or 0)
    hidden_line = _tr('key_plans.select_key_plan.5') if not tariff.get('is_active') else ''
    await safe_edit_or_send(
        callback.message,
        _tr('key_plans.select_key_plan.6', p0=f"{escape_html(tariff['name'])}", p1=f'{_traffic_text(traffic)}', p2=f'{_duration_text(duration)}', p3=f"{int(tariff.get('max_ips') or 1)}", p4=f'{hidden_line}'),
        reply_markup=key_plan_confirm_kb(key_id, tariff_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_plan_apply:'))
async def apply_key_plan(callback: CallbackQuery, state: FSMContext) -> None:
    """Applies an ordinary active or hidden plan after confirmation."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    _, key_id_raw, tariff_id_raw = callback.data.split(':', 2)
    await _apply_plan(
        callback,
        state,
        int(key_id_raw),
        int(tariff_id_raw),
    )


@router.message(AdminStates.key_plan_custom_traffic, F.text, ~F.text.startswith('/'))
async def custom_plan_traffic(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    value = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _input_target(message, state)
    if not value.isdigit() or not 0 <= int(value) <= 99999:
        await safe_edit_or_send(
            target,
            _tr('key_plans.custom_plan_traffic.1'),
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_traffic_gb=int(value))
    await state.set_state(AdminStates.key_plan_custom_days)
    await safe_edit_or_send(
        target,
        _tr('key_plans.custom_plan_traffic.2'),
        reply_markup=key_action_cancel_kb(data['current_key_id'], 0),
    )


@router.message(AdminStates.key_plan_custom_days, F.text, ~F.text.startswith('/'))
async def custom_plan_days(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    value = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _input_target(message, state)
    if not value.isdigit() or not 0 <= int(value) <= 99999:
        await safe_edit_or_send(
            target,
            _tr('key_plans.custom_plan_days.1'),
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_days=int(value))
    await state.set_state(AdminStates.key_plan_custom_devices)
    await safe_edit_or_send(
        target,
        _tr('key_plans.custom_plan_days.2'),
        reply_markup=key_action_cancel_kb(data['current_key_id'], 0),
    )


@router.message(AdminStates.key_plan_custom_devices, F.text, ~F.text.startswith('/'))
async def custom_plan_devices(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    value = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _input_target(message, state)
    if not value.isdigit() or not 1 <= int(value) <= 999:
        await safe_edit_or_send(
            target,
            _tr('key_plans.custom_plan_devices.1'),
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_devices=int(value))
    await state.set_state(AdminStates.key_plan_custom_confirm)
    data = await state.get_data()
    await safe_edit_or_send(
        target,
        _tr('key_plans.custom_plan_devices.2', p0=f"{_traffic_text(data['key_plan_custom_traffic_gb'])}", p1=f"{_duration_text(data['key_plan_custom_days'])}", p2=f"{data['key_plan_custom_devices']}"),
        reply_markup=key_plan_custom_confirm_kb(data['current_key_id']),
    )


@router.callback_query(F.data == 'admin_key_plan_custom_apply')
async def apply_custom_key_plan(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    data = await state.get_data()
    required = (
        'current_key_id',
        'key_plan_target_tariff_id',
        'key_plan_custom_traffic_gb',
        'key_plan_custom_days',
        'key_plan_custom_devices',
    )
    if any(field not in data for field in required):
        await callback.answer(_tr('key_plans.apply_custom_key_plan.1'), show_alert=True)
        return
    await _apply_plan(
        callback,
        state,
        int(data['current_key_id']),
        int(data['key_plan_target_tariff_id']),
        custom_traffic_gb=int(data['key_plan_custom_traffic_gb']),
        custom_duration_days=int(data['key_plan_custom_days']),
        custom_max_ips=int(data['key_plan_custom_devices']),
    )


async def _apply_plan(
    callback: CallbackQuery,
    state: FSMContext,
    key_id: int,
    tariff_id: int,
    **custom_values: int,
) -> None:
    await callback.answer(_tr('key_plans.apply_plan.1'))
    result = await reissue_key_plan(
        key_id,
        tariff_id,
        performed_by=callback.from_user.id,
        **custom_values,
    )
    if not result.get('ok'):
        await safe_edit_or_send(
            callback.message,
            _tr('key_plans.apply_plan.2', p0=f"{result.get('reason') or 'unknown'}"),
            reply_markup=key_action_cancel_kb(key_id, 0),
        )
        return
    await state.set_state(AdminStates.key_view)
    suffix = ''
    if not result.get('panel_synced'):
        suffix = (
            _tr('key_plans.apply_plan.3')
        )
    await safe_edit_or_send(
        callback.message,
        _tr('key_plans.apply_plan.4', p0=f"{_traffic_text(result['traffic_limit'] // 1024 ** 3)}", p1=f"{_duration_text(result['duration_days'])}", p2=f"{result['max_ips']}", p3=f'{suffix}'),
        reply_markup=key_action_cancel_kb(key_id, 0),
    )


__all__ = ['router']
