"""Administrator flow for fully reissuing a VPN key tariff plan."""
from __future__ import annotations

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
)

router = Router()


def _duration_text(days: int) -> str:
    return 'Без срока' if int(days) == 0 else f'{int(days)} дней'


def _traffic_text(gigabytes: int) -> str:
    return 'Безлимит' if int(gigabytes) == 0 else f'{int(gigabytes)} ГБ'


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
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':', 1)[1])
    key = get_vpn_key_by_id(key_id)
    if key is None:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    group_id = int(key.get('tariff_group_id') or 1)
    custom_tariff = get_admin_custom_tariff(group_id)
    if custom_tariff is None:
        await callback.answer('❌ Системный тариф группы не найден', show_alert=True)
        return
    tariffs = get_tariffs_by_group(
        group_id,
        include_hidden=True,
        include_system=False,
    )
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    rendered = await safe_edit_or_send(
        callback.message,
        '📋 <b>Изменение тарифного плана</b>\n\n'
        'Выберите тариф из текущей группы ключа. Скрытые тарифы доступны '
        'администратору.\n\n'
        '⚠️ Применение полностью переоформит ключ: срок и пакет будут '
        'назначены заново, а использованный трафик сброшен.',
        reply_markup=key_plan_select_kb(
            key_id,
            tariffs,
            int(custom_tariff['id']),
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
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    _, key_id_raw, tariff_id_raw = callback.data.split(':', 2)
    key_id = int(key_id_raw)
    tariff_id = int(tariff_id_raw)
    key = get_vpn_key_by_id(key_id)
    tariff = get_tariff_by_id(tariff_id)
    if key is None or tariff is None:
        await callback.answer('❌ Ключ или тариф не найден', show_alert=True)
        return
    if int(key.get('tariff_group_id') or 1) != int(tariff.get('group_id') or 1):
        await callback.answer('❌ Тариф относится к другой группе', show_alert=True)
        return

    if tariff.get('system_type') == 'admin_custom':
        await state.set_state(AdminStates.key_plan_custom_traffic)
        await state.update_data(
            current_key_id=key_id,
            key_plan_target_tariff_id=tariff_id,
        )
        rendered = await safe_edit_or_send(
            callback.message,
            '🛠 <b>Произвольный тариф</b>\n\n'
            'Введите пакет трафика от 0 до 99999 ГБ.\n'
            '0 — безлимитный трафик.',
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
        await callback.answer('❌ Этот системный тариф недоступен', show_alert=True)
        return

    duration = int(tariff.get('duration_days') or 0)
    traffic = int(tariff.get('traffic_limit_gb') or 0)
    hidden_line = '\n⚪ Тариф скрыт от пользователей.' if not tariff.get('is_active') else ''
    await safe_edit_or_send(
        callback.message,
        f"📋 <b>Переоформление на «{escape_html(tariff['name'])}»</b>\n\n"
        f'📊 Трафик: {_traffic_text(traffic)}\n'
        f'📅 Срок: {_duration_text(duration)}\n'
        f"💻 Устройств: {int(tariff.get('max_ips') or 1)}"
        f'{hidden_line}\n\n'
        '⚠️ Текущий срок, тариф и счётчик трафика будут заменены. '
        'Это не накопительное платное продление.',
        reply_markup=key_plan_confirm_kb(key_id, tariff_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_plan_apply:'))
async def apply_key_plan(callback: CallbackQuery, state: FSMContext) -> None:
    """Applies an ordinary active or hidden plan after confirmation."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
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
            '❌ <b>Некорректный пакет трафика</b>\n\n'
            'Введите число от 0 до 99999. 0 — безлимит.',
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_traffic_gb=int(value))
    await state.set_state(AdminStates.key_plan_custom_days)
    await safe_edit_or_send(
        target,
        '📅 <b>Срок произвольного тарифа</b>\n\n'
        'Введите срок от 0 до 99999 дней.\n0 — без ограничения времени.',
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
            '❌ <b>Некорректный срок</b>\n\n'
            'Введите число от 0 до 99999. 0 — без срока.',
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_days=int(value))
    await state.set_state(AdminStates.key_plan_custom_devices)
    await safe_edit_or_send(
        target,
        '💻 <b>Лимит устройств</b>\n\nВведите число от 1 до 999.',
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
            '❌ <b>Некорректный лимит устройств</b>\n\n'
            'Введите число от 1 до 999.',
            reply_markup=key_action_cancel_kb(data.get('current_key_id', 0), 0),
        )
        return
    await state.update_data(key_plan_custom_devices=int(value))
    await state.set_state(AdminStates.key_plan_custom_confirm)
    data = await state.get_data()
    await safe_edit_or_send(
        target,
        '🛠 <b>Подтверждение произвольного тарифа</b>\n\n'
        f"📊 Трафик: {_traffic_text(data['key_plan_custom_traffic_gb'])}\n"
        f"📅 Срок: {_duration_text(data['key_plan_custom_days'])}\n"
        f"💻 Устройств: {data['key_plan_custom_devices']}\n\n"
        '⚠️ Текущие параметры ключа будут полностью заменены.',
        reply_markup=key_plan_custom_confirm_kb(data['current_key_id']),
    )


@router.callback_query(F.data == 'admin_key_plan_custom_apply')
async def apply_custom_key_plan(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
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
        await callback.answer('❌ Данные формы устарели', show_alert=True)
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
    await callback.answer('⏳ Переоформляю ключ…')
    result = await reissue_key_plan(
        key_id,
        tariff_id,
        performed_by=callback.from_user.id,
        **custom_values,
    )
    if not result.get('ok'):
        await safe_edit_or_send(
            callback.message,
            '❌ <b>Тарифный план не изменён</b>\n\n'
            f"Причина: <code>{result.get('reason') or 'unknown'}</code>",
            reply_markup=key_action_cancel_kb(key_id, 0),
        )
        return
    await state.set_state(AdminStates.key_view)
    suffix = ''
    if not result.get('panel_synced'):
        suffix = (
            '\n\n⚠️ В БД план изменён, но панель синхронизирована не полностью. '
            'Фоновая сверка сможет повторить обновление.'
        )
    await safe_edit_or_send(
        callback.message,
        '✅ <b>Тарифный план изменён</b>\n\n'
        f"📊 Трафик: {_traffic_text(result['traffic_limit'] // (1024 ** 3))}\n"
        f"📅 Срок: {_duration_text(result['duration_days'])}\n"
        f"💻 Устройств: {result['max_ips']}"
        f'{suffix}',
        reply_markup=key_action_cancel_kb(key_id, 0),
    )


__all__ = ['router']
