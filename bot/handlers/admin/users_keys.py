import logging
import uuid
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, create_vpn_key_admin, get_user_balance, get_user_referral_coefficient, add_to_balance, deduct_from_balance, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.admin_dialog import (
    render_admin_dialog,
    render_admin_dialog_from_input,
)
from bot.utils.datetime_format import format_datetime_for_display
from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.panel_email import get_panel_email_prefix
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_group_kb, add_key_server_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, key_action_back_kb, back_and_home_kb, home_only_kb
from bot.services.vpn_api import (
    get_client_from_server_data,
    get_client_inbound_descriptors,
    VPNAPIError,
    format_traffic,
    provision_client_on_server,
    resolve_panel_client_limits,
)
from bot.handlers.admin.users_manage import format_user_display, _show_user_view_edit
from bot.handlers.admin.users_list import show_users_menu
from bot.services.panel_sync_coordinator import regular_panel_operation

logger = logging.getLogger(__name__)

router = Router()
USERS_PER_PAGE = 20

def generate_unique_email(user: dict) -> str:
    """
    Generates a unique email for the 3X-UI panel.
    Format: user_{username/id}_{random_suffix}
    """
    from bot.utils.panel_email import generate_unique_panel_email

    return generate_unique_panel_email(user)


async def _admin_key_input_target(
    message: Message,
    state: FSMContext,
) -> tuple[Message, dict]:
    """Deletes administrator input and targets the original key dialog."""
    data = await state.get_data()
    message_id = data.get('add_key_dialog_message_id')
    try:
        target = message.model_copy(update={'message_id': int(message_id)})
    except (TypeError, ValueError):
        target = message
    try:
        await message.delete()
    except Exception:
        pass
    return target, data


def _can_show_key_subscription(key: dict) -> bool:
    """Use the existing activity rule and require a complete subscription."""
    from database.requests import is_key_active

    configured = all((key.get('server_id'), key.get('panel_email'), key.get('sub_id')))
    return configured and is_key_active(key)


@router.callback_query(F.data.startswith('admin_key_view:'))
async def show_key_view(callback: CallbackQuery, state: FSMContext):
    """Shows the key management screen."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    key_name = key.get('custom_name') or f'Ключ #{key_id}'
    server_name = escape_html(key.get('server_name', 'Неизвестный сервер'))
    tariff_name = (
        'Произвольный тариф'
        if key.get('tariff_system_type') == 'admin_custom'
        else escape_html(key.get('tariff_name', 'Неизвестный тариф'))
    )
    expires_at = (
        'Без срока'
        if key.get('expires_at') is None
        else format_datetime_for_display(key.get('expires_at'), fallback='?')
    )
    created_at = format_datetime_for_display(key.get('created_at'), fallback='?')
    panel_email = key.get('panel_email')
    if panel_email:
        panel_email_line = f'📧 E-mail в панели: <code>{escape_html(panel_email)}</code>'
    else:
        panel_email_line = '📧 E-mail в панели: <i>не указан</i>'
    max_ips = int(
        key.get('max_ips_override')
        if key.get('max_ips_override') is not None
        else key.get('tariff_max_ips') or 1
    )
    text = f'🔑 <b>{escape_html(key_name)}</b>\n\n🖥️ Сервер: {server_name}\n📋 Тариф: {tariff_name}\n💻 Устройств: {max_ips}\n{panel_email_line}\n📅 Создан: {created_at}\n⏰ Истекает: {expires_at}\n'
    from database.requests import is_key_active, is_traffic_exhausted
    if not is_key_active(key):
        if is_traffic_exhausted(key):
            text += '\n❌ <b>Трафик исчерпан</b>\n'
        else:
            text += '\n⏳ <b>Срок действия истёк</b>\n'
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0:
        remaining = max(0, traffic_limit - traffic_used)
        text += f'\n📊 <b>Трафик:</b>\n  ✅ Использовано: {format_traffic(traffic_used)}\n  🎯 Лимит: {format_traffic(traffic_limit)}\n  💾 Остаток: {format_traffic(remaining)}\n'
    else:
        text += f'\n📊 <b>Трафик:</b>\n  ✅ Использовано: {format_traffic(traffic_used)}\n  ∞ Без лимита\n'
    from database.requests import get_key_payments_history
    payments_history = get_key_payments_history(key_id)
    if payments_history:
        text += '\n📜 <b>История операций:</b>\n'
        for p in payments_history:
            dt = format_datetime_for_display(p.get('paid_at'), fallback='?')
            if p.get('history_type') == 'key_operation':
                delta_days = int(p.get('delta_days') or 0)
                reason_safe = escape_html(p.get('reason') or 'Начисление дней')
                if delta_days > 0:
                    text += f'• <code>{dt}</code>: {reason_safe} (+{delta_days} дн.)\n'
                else:
                    text += f'• <code>{dt}</code>: {reason_safe}\n'
                continue
            from bot.services.money import format_money_minor

            amount = format_money_minor(
                p.get('payable_amount_minor') or 0,
                p.get('base_currency') or 'RUB',
            )
            tariff_safe = escape_html(p['tariff_name'] or 'Неизвестно')
            text += f'• <code>{dt}</code>: {amount} — {tariff_safe}\n'
    else:
        text += '\n📜 <b>История операций:</b> пусто\n'
    user_telegram_id = key.get('telegram_id')
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=key_view_kb(
            key_id,
            user_telegram_id,
            show_subscription=_can_show_key_subscription(key),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_show:'))
async def show_key_subscription(callback: CallbackQuery, state: FSMContext):
    """Show the selected user's subscription through the common QR delivery."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    try:
        key_id = int(callback.data.partition(':')[2])
    except ValueError:
        await callback.answer('Ключ не найден', show_alert=True)
        return

    from database.requests import get_key_details_for_user
    from bot.utils.key_sender import send_key_with_qr

    selected_key = get_vpn_key_by_id(key_id)
    key = (
        get_key_details_for_user(key_id, selected_key.get('telegram_id'))
        if selected_key else None
    )
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    if not _can_show_key_subscription(key):
        await callback.answer(
            'Подписка недоступна. Откройте карточку ключа заново.',
            show_alert=True,
        )
        return

    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    await callback.answer()
    await send_key_with_qr(callback, key, admin_return_key_id=key_id)


@router.callback_query(F.data.startswith('admin_key_extend:'))
async def start_key_extend(callback: CallbackQuery, state: FSMContext):
    """Start of key renewal."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    await state.set_state(AdminStates.key_extend_days)
    await state.update_data(current_key_id=key_id)
    await render_admin_dialog(
        callback.message,
        state,
        '📅 <b>Изменение срока действия ключа</b>\n\n'
        'Введите количество дней. Отрицательное число уменьшает конечный срок, '
        '0 делает ключ бессрочным. Добавление дней к уже бессрочному ключу не '
        'снимает бессрочность.',
        reply_markup=key_action_back_kb(key_id),
    )
    await callback.answer()

@router.message(AdminStates.key_extend_days, F.text, ~F.text.startswith('/'))
async def process_key_extend(message: Message, state: FSMContext):
    """Processing the entry of days for extension."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').strip()
    data = await state.get_data()
    key_id = data.get('current_key_id')
    if not text.lstrip('-').isdigit() or int(text) < -99999 or int(text) > 99999:
        await render_admin_dialog_from_input(
            message,
            state,
            '📅 <b>Изменение срока действия ключа</b>\n\n'
            '❌ Введите целое число от -99999 до 99999.\n\n'
            'Отрицательное число уменьшает конечный срок, 0 делает ключ '
            'бессрочным. Добавление дней к уже бессрочному ключу не снимает '
            'бессрочность.',
            reply_markup=key_action_back_kb(key_id),
        )
        return
    days = int(text)
    previous_key = get_vpn_key_by_id(key_id)
    target = await render_admin_dialog_from_input(
        message,
        state,
        '⏳ <b>Изменение срока действия ключа</b>\n\nПрименяю новое значение…',
        reply_markup=key_action_back_kb(key_id),
    )
    from bot.services.key_lifecycle import renew_key_access
    result = await renew_key_access(key_id, days)
    if result['db_updated']:
        updated_key = get_vpn_key_by_id(key_id)
        was_perpetual = bool(previous_key and previous_key.get('expires_at') is None)
        is_perpetual = bool(updated_key and updated_key.get('expires_at') is None)
        if is_perpetual and was_perpetual:
            result_text = (
                '✅ <b>Срок ключа не изменён</b>\n\n'
                'Ключ уже работал без ограничения по сроку.'
            )
        elif is_perpetual:
            result_text = (
                '✅ <b>Срок ключа обновлён</b>\n\n'
                'Ключ переведён в режим «Без срока».'
            )
        else:
            action_text = f'уменьшен на {abs(days)}' if days < 0 else f'продлён на {days}'
            result_text = (
                '✅ <b>Срок ключа обновлён</b>\n\n'
                f'Срок действия {action_text} дней.'
            )
        current_expiry = (
            'Без срока'
            if is_perpetual
            else format_datetime_for_display(
                updated_key.get('expires_at') if updated_key else None,
                fallback='неизвестен',
            )
        )
        result_text += (
            f'\n\n⏰ Текущий срок: <b>{escape_html(current_expiry)}</b>'
        )
        if not result['panel_synced']:
            result_text += '\n\n⚠️ БД обновлена, но панель синхронизирована не полностью. Повторная синхронизация сможет дожать состояние.'
        await render_admin_dialog(
            target,
            state,
            result_text,
            reply_markup=key_action_back_kb(key_id),
        )
        if updated_key:
            await state.set_state(AdminStates.key_view)
    else:
        await render_admin_dialog(
            target,
            state,
            '❌ <b>Срок ключа не изменён</b>\n\n'
            'Не удалось сохранить новое значение. Попробуйте ещё раз.',
            reply_markup=key_action_back_kb(key_id),
        )

@router.callback_query(F.data.startswith('admin_key_reset_traffic:'))
@regular_panel_operation
async def reset_key_traffic(callback: CallbackQuery, state: FSMContext):
    """Reset key traffic."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    try:
        # Resetting traffic_used and notification thresholds in the database
        from database.requests import reset_key_traffic_notification
        reset_key_traffic_notification(key_id)
        # Synchronize all key clients with the panel
        from bot.services.vpn_api import sync_key_to_panel_state
        stats = await sync_key_to_panel_state(key_id, reset_traffic=True)
        if not stats.get('ok'):
            await callback.answer('⚠️ БД обновлена, но панель синхронизирована не полностью', show_alert=True)
            return
        await callback.answer('✅ Трафик успешно сброшен!', show_alert=True)
    except VPNAPIError as e:
        logger.error(f'Ошибка сброса трафика: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка при сбросе трафика: {e}')
        await callback.answer('❌ Ошибка при сбросе трафика', show_alert=True)

@router.callback_query(F.data.startswith('admin_user_add_key:'))
async def start_add_key(callback: CallbackQuery, state: FSMContext):
    """Start adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    from database.requests import get_all_groups

    groups = get_all_groups()
    if not groups:
        await callback.answer('❌ Нет групп тарифов', show_alert=True)
        return
    await state.set_state(AdminStates.add_key_group)
    await state.update_data(
        add_key_user_id=user['id'],
        add_key_user_telegram_id=telegram_id,
        add_key_dialog_message_id=callback.message.message_id,
    )
    rendered = await safe_edit_or_send(
        callback.message,
        f'➕ <b>Добавление ключа для {format_user_display(user)}</b>\n\n'
        'Выберите группу тарифов:',
        reply_markup=add_key_group_kb(groups),
    )
    await state.update_data(
        add_key_dialog_message_id=getattr(
            rendered,
            'message_id',
            callback.message.message_id,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_add_key_group:'))
async def select_add_key_group(callback: CallbackQuery, state: FSMContext):
    """Selects the group that owns a new custom administrator key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    from database.requests import get_active_servers_by_group, get_group_by_id

    group_id = int(callback.data.split(':', 1)[1])
    group = get_group_by_id(group_id)
    if group is None:
        await callback.answer('❌ Группа не найдена', show_alert=True)
        return
    servers = get_active_servers_by_group(group_id)
    if not servers:
        await callback.answer('❌ В группе нет активных серверов', show_alert=True)
        return
    await state.update_data(add_key_group_id=group_id)
    await state.set_state(AdminStates.add_key_server)
    rendered = await safe_edit_or_send(
        callback.message,
        f"🖥️ <b>Группа: {escape_html(group['name'])}</b>\n\nВыберите сервер:",
        reply_markup=add_key_server_kb(servers),
    )
    await state.update_data(
        add_key_dialog_message_id=getattr(
            rendered,
            'message_id',
            callback.message.message_id,
        ),
    )
    await callback.answer()

@router.callback_query(F.data.startswith('admin_add_key_server:'))
async def select_add_key_server(callback: CallbackQuery, state: FSMContext):
    """Selecting a server for a new key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    from database.requests import get_active_servers_by_group, get_server_by_id
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    data = await state.get_data()
    group_id = data.get('add_key_group_id')
    allowed_server_ids = {
        int(item['id']) for item in get_active_servers_by_group(group_id)
    } if group_id else set()
    if not server or server_id not in allowed_server_ids:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await state.update_data(add_key_server_id=server_id)

    try:
        client = get_client_from_server_data(server)
        descriptors = await get_client_inbound_descriptors(
            client,
        )
        if not any(descriptor.available for descriptor in descriptors):
            await callback.answer('❌ На сервере нет inbound', show_alert=True)
            return
        await state.set_state(AdminStates.add_key_traffic)
        rendered = await safe_edit_or_send(
            callback.message,
            '📊 <b>Лимит трафика</b>\n\nВведите лимит в ГБ (0 = без лимита):',
            reply_markup=add_key_step_kb(4),
        )
        await state.update_data(
            add_key_dialog_message_id=getattr(
                rendered,
                'message_id',
                callback.message.message_id,
            ),
        )
    except VPNAPIError as e:
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    await callback.answer()

@router.message(AdminStates.add_key_traffic, F.text, ~F.text.startswith('/'))
async def process_add_key_traffic(message: Message, state: FSMContext):
    """Processing the entry of a traffic limit."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _admin_key_input_target(message, state)
    if not text.isdigit() or not 0 <= int(text) <= 99999:
        await safe_edit_or_send(
            target,
            '❌ <b>Некорректный лимит трафика</b>\n\n'
            'Введите число от 0 до 99999. 0 — без лимита.',
            reply_markup=add_key_step_kb(4),
        )
        return
    traffic_gb = int(text)
    await state.update_data(add_key_traffic_gb=traffic_gb)
    await state.set_state(AdminStates.add_key_days)
    await safe_edit_or_send(target, '📅 <b>Срок действия</b>\n\nВведите количество дней (0 = без срока):', reply_markup=add_key_step_kb(5))

@router.message(AdminStates.add_key_days, F.text, ~F.text.startswith('/'))
async def process_add_key_days(message: Message, state: FSMContext):
    """Processing expiration date input."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _admin_key_input_target(message, state)
    if not text.isdigit() or not 0 <= int(text) <= 99999:
        await safe_edit_or_send(
            target,
            '❌ <b>Некорректный срок</b>\n\n'
            'Введите число от 0 до 99999. 0 — без срока.',
            reply_markup=add_key_step_kb(5),
        )
        return
    days = int(text)
    await state.update_data(add_key_days=days)
    await state.set_state(AdminStates.add_key_devices)
    await safe_edit_or_send(
        target,
        '💻 <b>Лимит устройств</b>\n\nВведите число от 1 до 999:',
        reply_markup=add_key_step_kb(6),
    )


@router.message(AdminStates.add_key_devices, F.text, ~F.text.startswith('/'))
async def process_add_key_devices(message: Message, state: FSMContext):
    """Processes the individual device limit for a custom key."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage

    text = get_message_text_for_storage(message, 'plain').strip()
    target, data = await _admin_key_input_target(message, state)
    if not text.isdigit() or not 1 <= int(text) <= 999:
        await safe_edit_or_send(
            target,
            '❌ <b>Некорректный лимит устройств</b>\n\n'
            'Введите число от 1 до 999.',
            reply_markup=add_key_step_kb(6),
        )
        return
    devices = int(text)
    await state.update_data(add_key_devices=devices)
    await state.set_state(AdminStates.add_key_confirm)
    data = await state.get_data()
    from database.requests import get_group_by_id, get_server_by_id

    server = get_server_by_id(data['add_key_server_id'])
    group = get_group_by_id(data['add_key_group_id'])
    traffic_text = f"{data.get('add_key_traffic_gb', 0)} ГБ" if data.get('add_key_traffic_gb', 0) > 0 else 'без лимита'
    days = int(data.get('add_key_days', 0))
    duration_text = f'{days} дней' if days > 0 else 'без срока'
    await safe_edit_or_send(
        target,
        '✅ <b>Подтверждение создания ключа</b>\n\n'
        f"📂 Группа: {escape_html(group['name'] if group else '?')}\n"
        f"🖥️ Сервер: {escape_html(server['name'] if server else '?')}\n"
        f'📊 Трафик: {traffic_text}\n'
        f'📅 Срок: {duration_text}\n'
        f'💻 Устройств: {devices}\n',
        reply_markup=add_key_confirm_kb(),
    )

@router.callback_query(F.data == 'admin_add_key_confirm')
@regular_panel_operation
async def confirm_add_key(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Serialize manual key creation for the selected user."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    try:
        lock_id = int(data.get('add_key_user_id'))
    except (TypeError, ValueError):
        await callback.answer('❌ Данные формы устарели', show_alert=True)
        return

    from bot.services.user_locks import user_locks

    async with user_locks[lock_id]:
        await _confirm_add_key_locked(callback, state, bot)


async def _confirm_add_key_locked(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
):
    """Confirmation and key creation."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_id = data.get('add_key_user_id')
    user_telegram_id = data.get('add_key_user_telegram_id')
    server_id = data.get('add_key_server_id')
    group_id = data.get('add_key_group_id')
    traffic_gb = data.get('add_key_traffic_gb', 0)
    days = data.get('add_key_days', 0)
    devices = data.get('add_key_devices', 1)
    try:
        user_id = int(user_id)
        user_telegram_id = int(user_telegram_id)
        server_id = int(server_id)
        group_id = int(group_id)
        traffic_gb = int(traffic_gb)
        days = int(days)
        devices = int(devices)
    except (TypeError, ValueError):
        await callback.answer('❌ Данные формы устарели', show_alert=True)
        return
    if (
        not 0 <= traffic_gb <= 99999
        or not 0 <= days <= 99999
        or not 1 <= devices <= 999
    ):
        await callback.answer('❌ Данные формы некорректны', show_alert=True)
        return
    from database.requests import (
        get_active_servers_by_group,
        get_admin_custom_tariff,
        get_server_by_id,
    )
    server = get_server_by_id(server_id)
    allowed_server_ids = {
        int(item['id']) for item in get_active_servers_by_group(group_id)
    } if group_id else set()
    if not server or server_id not in allowed_server_ids:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    user = get_user_by_telegram_id(user_telegram_id)
    if user is None or int(user['id']) != user_id:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    email = generate_unique_email(user)
    traffic_limit_bytes = traffic_gb * 1024 ** 3
    panel_client = None
    provisioned = None
    key_id = None
    try:
        admin_tariff = get_admin_custom_tariff(group_id)
        if admin_tariff is None:
            raise RuntimeError('Системный тариф группы не найден')
        tariff_id = admin_tariff['id']

        sub_id = uuid.uuid4().hex
        panel_client = get_client_from_server_data(server)
        panel_limits = resolve_panel_client_limits(devices)
        provisioned = await provision_client_on_server(
            server_id=server_id,
            email=email,
            total_gb=traffic_gb,
            total_gb_bytes=traffic_limit_bytes,
            expire_days=days,
            limit_ip=panel_limits.limit_ip,
            limit_hwid=panel_limits.limit_hwid,
            tg_id=str(user_telegram_id),
            sub_id=sub_id,
            client=panel_client,
        )
        sub_id = provisioned.sub_id
        if not provisioned.attached_inbound_ids or not sub_id:
            raise RuntimeError('Не удалось создать ни одного клиента на сервере')
        key_id = create_vpn_key_admin(
            user_id=user_id,
            server_id=server_id,
            tariff_id=tariff_id,
            panel_email=email,
            sub_id=sub_id,
            days=days,
            traffic_limit=traffic_limit_bytes,
            traffic_limit_override=traffic_limit_bytes,
            max_ips_override=devices,
        )
        if not provisioned.complete:
            from bot.services.vpn_api import sync_key_to_panel_state

            sync_kwargs = (
                {'panel_snapshot': provisioned.snapshot}
                if provisioned.snapshot is not None
                else {}
            )
            sync_stats = await sync_key_to_panel_state(key_id, **sync_kwargs)
            if not sync_stats.get('ok'):
                logger.warning(
                    'Admin key %s was provisioned partially: %s',
                    key_id,
                    sync_stats,
                )

        await state.set_data({'current_user_telegram_id': user_telegram_id})
        await callback.answer('✅ Ключ успешно создан!', show_alert=True)
        await _show_user_view_edit(callback, state, user_telegram_id)
    except VPNAPIError as e:
        if panel_client is not None and key_id is None:
            try:
                await panel_client.delete_client(email)
            except Exception:
                logger.exception('Could not clean failed admin key candidate email=%s', email)
        logger.error(f'Ошибка создания ключа: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        if panel_client is not None and key_id is None:
            try:
                await panel_client.delete_client(email)
            except Exception:
                logger.exception('Could not clean failed admin key candidate email=%s', email)
        logger.exception('Unexpected admin key creation error: %s', e)
        await callback.answer('❌ Ошибка при создании ключа', show_alert=True)

@router.callback_query(F.data == 'admin_user_add_key_cancel')
async def cancel_add_key(callback: CallbackQuery, state: FSMContext):
    """Cancel adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_telegram_id = data.get('add_key_user_telegram_id') or data.get('current_user_telegram_id')
    if user_telegram_id:
        await _show_user_view_edit(callback, state, user_telegram_id)
    else:
        await show_users_menu(callback, state)

@router.callback_query(F.data == 'admin_add_key_back')
async def add_key_back(callback: CallbackQuery, state: FSMContext):
    """Step back when adding a key."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    current_state = await state.get_state()
    data = await state.get_data()
    from database.requests import get_active_servers_by_group, get_all_groups

    group_id = data.get('add_key_group_id')
    if current_state == AdminStates.add_key_server.state:
        await state.set_state(AdminStates.add_key_group)
        await safe_edit_or_send(
            callback.message,
            '📂 <b>Группа тарифа</b>\n\nВыберите группу:',
            reply_markup=add_key_group_kb(get_all_groups()),
        )
    elif current_state == AdminStates.add_key_traffic.state:
        servers = get_active_servers_by_group(group_id) if group_id else []
        await state.set_state(AdminStates.add_key_server)
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await safe_edit_or_send(
            callback.message,
            f"➕ <b>Добавление ключа для {(format_user_display(user) if user else '?')}</b>\n\nВыберите сервер:",
            reply_markup=add_key_server_kb(servers),
        )
    elif current_state == AdminStates.add_key_days.state:
        await state.set_state(AdminStates.add_key_traffic)
        await safe_edit_or_send(
            callback.message,
            '📊 <b>Лимит трафика</b>\n\nВведите лимит в ГБ (0 = без лимита):',
            reply_markup=add_key_step_kb(4),
        )
    elif current_state == AdminStates.add_key_devices.state:
        await state.set_state(AdminStates.add_key_days)
        await safe_edit_or_send(
            callback.message,
            '📅 <b>Срок действия</b>\n\nВведите количество дней (0 = без срока):',
            reply_markup=add_key_step_kb(5),
        )
    elif current_state == AdminStates.add_key_confirm.state:
        await state.set_state(AdminStates.add_key_devices)
        await safe_edit_or_send(
            callback.message,
            '💻 <b>Лимит устройств</b>\n\nВведите число от 1 до 999:',
            reply_markup=add_key_step_kb(6),
        )
    else:
        await cancel_add_key(callback, state)
        return
    await callback.answer()

def _manual_sync_plan_text(plan, *, preview: bool) -> str:
    direction_title = (
        'БД → Панель'
        if plan.direction == 'db_to_panel'
        else 'Панель → БД'
    )
    heading = (
        '🔎 <b>Предпросмотр синхронизации</b>'
        if preview
        else '✅ <b>Синхронизация завершена</b>'
    )
    lines = [heading, '', f'Направление: <b>{direction_title}</b>', '']

    if not plan.reports:
        lines.append('✅ Нет ключей для проверки.')
    for report in plan.reports:
        server_name = escape_html(report.server_name)
        if report.error:
            lines.append(
                f'❌ <b>{server_name}</b>: '
                f'{escape_html(str(report.error)[:180])}'
            )
            continue

        if plan.direction == 'db_to_panel':
            stats = report.stats
            action_word = 'изменится' if preview else 'применено'
            lines.append(
                f'🖥 <b>{server_name}</b>: проверено {report.checked}, '
                f'{action_word} {report.changed}, пропущено {report.skipped}'
            )
            details = []
            if stats.get('created'):
                details.append(f"создать/подключить {stats['created']}")
            if stats.get('updated'):
                details.append(f"обновить {stats['updated']}")
            if stats.get('deleted'):
                details.append(f"отключить {stats['deleted']}")
            if stats.get('enabled'):
                details.append(f"включить {stats['enabled']}")
            if stats.get('disabled'):
                details.append(f"выключить {stats['disabled']}")
            if stats.get('reset'):
                details.append(f"сбросить трафик {stats['reset']}")
            if details:
                lines.append('  • ' + ', '.join(details))
            if stats.get('errors'):
                lines.append(f"  • ошибок: {stats['errors']}")
        else:
            stats = report.stats
            applied = stats.get('applied')
            action_word = (
                f'применено {applied}'
                if applied is not None
                else f'изменится {report.changed}'
            )
            lines.append(
                f'🖥 <b>{server_name}</b>: проверено {report.checked}, '
                f'{action_word}, пропущено {report.skipped}'
            )
            details = []
            if stats.get('expiry'):
                details.append(f"срок {stats['expiry']}")
            if stats.get('traffic'):
                details.append(f"трафик {stats['traffic']}")
            if stats.get('revived'):
                details.append(f"восстановить истёкших {stats['revived']}")
            if details:
                lines.append('  • ' + ', '.join(details))

    lines.extend([
        '',
        f'🔑 Ключей с изменениями: <b>{len(plan.candidate_key_ids)}</b>',
    ])
    if plan.errors:
        lines.append(f'❌ Ошибок: <b>{plan.errors}</b>')
    if preview and plan.has_changes:
        lines.extend([
            '',
            'Запись ещё не выполнялась. После подтверждения данные '
            'будут скачаны и проверены повторно.',
        ])
    elif not plan.has_changes:
        lines.extend(['', '✅ Всё уже актуально, применять нечего.'])
    return '\n'.join(lines)


async def _manual_sync_keys(direction: str):
    from database.requests import (
        get_all_active_keys_with_server,
        get_all_panel_sync_keys,
    )

    if direction == 'db_to_panel':
        return get_all_active_keys_with_server()
    return get_all_panel_sync_keys()


async def _show_manual_sync_preview(
    callback: CallbackQuery,
    state: FSMContext,
    direction: str,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    from database.requests import get_all_servers
    from bot.keyboards.admin import manual_sync_preview_kb
    from bot.services.panel_sync import (
        build_panel_to_db_plan,
        run_db_to_panel_sync,
    )
    from bot.services.panel_sync_coordinator import panel_sync_coordinator

    if panel_sync_coordinator.manual_pending:
        await callback.answer(
            '⏳ Другая ручная синхронизация уже выполняется',
            show_alert=True,
        )
        return

    await callback.answer('🔎 Составляю предпросмотр…')
    await safe_edit_or_send(
        callback.message,
        '⏳ <b>Проверяю БД и панели…</b>\n\n'
        'На этом этапе ничего не изменяется.',
    )

    keys = await _manual_sync_keys(direction)
    servers = get_all_servers()
    if direction == 'db_to_panel':
        plan = await run_db_to_panel_sync(keys, servers, apply=False)
    else:
        plan = await build_panel_to_db_plan(keys, servers)

    token = uuid.uuid4().hex[:12]
    preview_data = {
        'token': token,
        'direction': direction,
        'candidate_key_ids': list(plan.candidate_key_ids),
        'server_ids': list(plan.successful_server_ids),
    }
    await state.update_data(manual_sync_preview=preview_data)

    markup = (
        manual_sync_preview_kb(direction, token)
        if plan.has_changes
        else back_and_home_kb('admin_users')
    )
    await safe_edit_or_send(
        callback.message,
        _manual_sync_plan_text(plan, preview=True),
        reply_markup=markup,
    )


@router.callback_query(F.data == 'admin_sync_db_to_panel')
async def sync_db_to_panel(callback: CallbackQuery, state: FSMContext):
    """Build a read-only DB -> Panel synchronization preview."""
    await _show_manual_sync_preview(callback, state, 'db_to_panel')


@router.callback_query(F.data == 'admin_sync_panel_to_db')
async def sync_panel_to_db(callback: CallbackQuery, state: FSMContext):
    """Build a read-only Panel -> DB synchronization preview."""
    await _show_manual_sync_preview(callback, state, 'panel_to_db')


@router.callback_query(F.data.startswith('admin_sync_cancel:'))
async def cancel_manual_sync(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    token = callback.data.split(':', 1)[1]
    data = await state.get_data()
    preview = data.get('manual_sync_preview') or {}
    if preview.get('token') == token:
        data.pop('manual_sync_preview', None)
        await state.set_data(data)
    await callback.answer('Синхронизация отменена')
    await safe_edit_or_send(
        callback.message,
        '❌ <b>Ручная синхронизация отменена</b>\n\n'
        'Никакие данные не изменялись.',
        reply_markup=back_and_home_kb('admin_users'),
    )


@router.callback_query(F.data.startswith('admin_sync_apply:'))
async def apply_manual_sync(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return

    parts = callback.data.split(':', 2)
    if len(parts) != 3:
        await callback.answer('Предпросмотр повреждён', show_alert=True)
        return
    _, direction, token = parts
    data = await state.get_data()
    preview = data.get('manual_sync_preview') or {}
    if (
        preview.get('token') != token
        or preview.get('direction') != direction
        or direction not in {'db_to_panel', 'panel_to_db'}
    ):
        await callback.answer(
            'Предпросмотр устарел. Выполните проверку заново.',
            show_alert=True,
        )
        return

    from database.requests import get_all_servers
    from bot.keyboards.admin import manual_sync_preview_kb
    from bot.services.panel_sync import (
        apply_panel_to_db_plan,
        build_panel_to_db_plan,
        run_db_to_panel_sync,
    )
    from bot.services.panel_sync_coordinator import panel_sync_coordinator

    await callback.answer('⏳ Применяю изменения…')
    await safe_edit_or_send(
        callback.message,
        '⏳ <b>Применяю ручную синхронизацию…</b>\n\n'
        'Новые изменения VPN-ключей временно ожидают завершения.',
    )

    try:
        async with panel_sync_coordinator.try_manual() as acquired:
            if not acquired:
                await safe_edit_or_send(
                    callback.message,
                    '⏳ <b>Уже выполняется другая ручная синхронизация</b>\n\n'
                    'Этот предпросмотр сохранён — повторите применение после её завершения.',
                    reply_markup=manual_sync_preview_kb(direction, token),
                )
                return

            keys = await _manual_sync_keys(direction)
            servers = get_all_servers()
            candidate_ids = preview.get('candidate_key_ids') or []
            server_ids = preview.get('server_ids') or []

            if direction == 'db_to_panel':
                plan = await run_db_to_panel_sync(
                    keys,
                    servers,
                    apply=True,
                    candidate_key_ids=candidate_ids,
                    allowed_server_ids=server_ids,
                )
            else:
                plan = await build_panel_to_db_plan(
                    keys,
                    servers,
                    candidate_key_ids=candidate_ids,
                    allowed_server_ids=server_ids,
                )
                plan = await apply_panel_to_db_plan(plan)
    except Exception as exc:
        logger.exception('Manual synchronization failed')
        await safe_edit_or_send(
            callback.message,
            '❌ <b>Не удалось завершить синхронизацию</b>\n\n'
            f'{escape_html(str(exc)[:500])}',
            reply_markup=manual_sync_preview_kb(direction, token),
        )
        return

    data = await state.get_data()
    current_preview = data.get('manual_sync_preview') or {}
    if current_preview.get('token') == token:
        data.pop('manual_sync_preview', None)
        await state.set_data(data)

    await safe_edit_or_send(
        callback.message,
        _manual_sync_plan_text(plan, preview=False),
        reply_markup=back_and_home_kb('admin_users'),
    )
