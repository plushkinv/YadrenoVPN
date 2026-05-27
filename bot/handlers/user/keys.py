import logging
import uuid
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_all_servers, get_setting, is_referral_enabled, get_user_by_referral_code, set_user_referrer
from bot.keyboards.user import main_menu_kb
from bot.states.user_states import RenameKey, ReplaceKey
from bot.utils.text import escape_html, safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()

@router.message(Command('mykeys'))
async def cmd_mykeys(message: Message, state: FSMContext):
    """Обработчик команды /mykeys - вызывает логику кнопки 'Мои ключи'."""
    if is_user_banned(message.from_user.id):
        await safe_edit_or_send(message, '⛔ <b>Доступ заблокирован</b>\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', force_new=True)
        return
    await state.clear()
    await show_my_keys(message.from_user.id, message, is_callback=False)

async def _build_my_keys_render_data(telegram_id: int):
    """Готовит текст списка и динамические кнопки ключей."""
    from database.requests import get_user_keys_for_display, get_setting, is_traffic_exhausted
    from bot.services.vpn_api import get_client, format_traffic
    from bot.utils.my_keys_page import (
        DEFAULT_MY_KEYS_ITEM_TEMPLATE,
        MY_KEYS_ITEM_TEMPLATE_SETTING,
        build_my_keys_item_text,
        build_my_keys_list_text,
    )

    keys = get_user_keys_for_display(telegram_id)
    item_template = get_setting(
        MY_KEYS_ITEM_TEMPLATE_SETTING,
        DEFAULT_MY_KEYS_ITEM_TEMPLATE,
    )
    if item_template is None:
        item_template = DEFAULT_MY_KEYS_ITEM_TEMPLATE
    items = []
    key_buttons = []

    for key in keys:
        traffic_exhausted = is_traffic_exhausted(key)
        if key['is_active'] and not traffic_exhausted:
            status_emoji = '🟢'
        else:
            status_emoji = '🔴'

        traffic_used = key.get('traffic_used', 0) or 0
        traffic_limit = key.get('traffic_limit', 0) or 0
        used_str = format_traffic(traffic_used)
        limit_str = format_traffic(traffic_limit) if traffic_limit > 0 else '∞'
        traffic_text = f'{used_str} / {limit_str}'

        protocol = 'VLESS'
        inbound_name = 'VPN'
        if key.get('sub_id'):
            protocol = 'SUBSCRIPTION'
            inbound_name = 'Все протоколы'
        elif key.get('server_id') and key.get('panel_email'):
            try:
                client = await get_client(key['server_id'])
                stats = await client.get_client_stats(key['panel_email'])
                if stats:
                    protocol = stats['protocol'].upper()
                    inbound_name = stats.get('remark', 'VPN') or 'VPN'
            except Exception as e:
                logger.warning(f"Не удалось получить протокол для ключа {key['id']}: {e}")

        items.append(
            build_my_keys_item_text(
                key,
                template=item_template,
                status=status_emoji,
                traffic_text=traffic_text,
                inbound_name=inbound_name,
                protocol=protocol,
            )
        )
        key_buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {key['display_name']}",
                callback_data=f"key:{key['id']}",
            )
        ])

    return keys, build_my_keys_list_text(items), key_buttons


async def _render_my_keys_page(target, telegram_id: int, force_new: bool = False) -> None:
    """Рендерит страницу «Мои ключи» из таблицы pages."""
    from bot.utils.page_renderer import render_page

    keys, keys_list_text, key_buttons = await _build_my_keys_render_data(telegram_id)
    context = {'telegram_id': telegram_id}

    if not keys:
        await render_page(
            target,
            page_key='my_keys_empty',
            context=context,
            force_new=force_new,
        )
        return

    await render_page(
        target,
        page_key='my_keys',
        context=context,
        text_replacements={'%списокключей%': keys_list_text},
        prepend_buttons=key_buttons,
        force_new=force_new,
    )


async def rerender_my_keys_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённый экран «Мои ключи» после правки через /yaa."""
    context = page_context.context or {}
    telegram_id = context.get('telegram_id') or viewer_id
    await _render_my_keys_page(page_context.message, int(telegram_id))
    return True


async def show_my_keys(telegram_id: int, target, is_callback: bool = True):
    """
    Общая логика для показа списка ключей.

    Args:
        telegram_id: ID пользователя в Telegram
        target: Message или CallbackQuery для отправки/редактирования
        is_callback: True если вызвано из callback (редактируем), False если из команды (отправляем новое)
    """
    await _render_my_keys_page(target, telegram_id, force_new=not is_callback)

@router.callback_query(F.data == 'my_keys')
async def my_keys_handler(callback: CallbackQuery):
    """Список VPN-ключей пользователя."""
    telegram_id = callback.from_user.id
    await show_my_keys(telegram_id, callback)
    await callback.answer()

async def show_key_details(telegram_id: int, key_id: int, message, is_callback: bool = True, prepend_text: str=''):
    """Общая логика для показа деталей ключа."""
    from database.requests import get_key_details_for_user, get_key_payments_history, is_key_active, is_traffic_exhausted
    from bot.keyboards.user import key_manage_kb
    from bot.services.vpn_api import format_traffic
    from bot.utils.key_pages import build_key_details_replacements, keyboard_rows
    from bot.utils.page_renderer import render_page
    import logging
    logger = logging.getLogger(__name__)
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        if is_callback:
            await safe_edit_or_send(message, '❌ Ключ не найден или вы не являетесь его владельцем.')
        else:
            await safe_edit_or_send(message, '❌ Ключ не найден или вы не являетесь его владельцем.', force_new=True)
        return
    traffic_exhausted = is_traffic_exhausted(key)
    key_active = is_key_active(key)
    if traffic_exhausted:
        status = '🔴 Трафик исчерпан'
    elif key_active:
        status = '🟢 Активен'
    else:
        status = '🔴 Истёк'
    inbound_name = '—'
    protocol = '—'
    is_unconfigured = not key.get('server_id')
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if is_unconfigured:
        traffic_info = '⚠️ Требует настройки'
    elif traffic_limit > 0:
        used_str = format_traffic(traffic_used)
        limit_str = format_traffic(traffic_limit)
        percent = traffic_used / traffic_limit * 100 if traffic_limit > 0 else 0
        traffic_info = f'{used_str} из {limit_str} ({percent:.1f}%)'
    elif traffic_used > 0:
        traffic_info = f'{format_traffic(traffic_used)} (безлимит)'
    else:
        traffic_info = 'Безлимит'
    if key.get('sub_id'):
        # Subscription: один ключ покрывает все inbound сервера сразу
        inbound_name = 'Все протоколы'
        protocol = 'SUBSCRIPTION'
    elif key.get('server_active') and key.get('panel_email'):
        try:
            from bot.services.vpn_api import get_client
            client = await get_client(key['server_id'])
            stats = await client.get_client_stats(key['panel_email'])
            if stats:
                protocol = stats.get('protocol', 'vless').upper()
                inbound_name = stats.get('remark', 'VPN') or 'VPN'
        except Exception as e:
            logger.warning(f'Ошибка получения протокола: {e}')
    payments = get_key_payments_history(key_id)
    replacements = build_key_details_replacements(
        key,
        payments,
        status=status,
        traffic_info=traffic_info,
        inbound_name=inbound_name,
        protocol=protocol,
        prepend_html=prepend_text,
    )
    kb = key_manage_kb(
        key_id,
        is_unconfigured=is_unconfigured,
        is_active=key_active,
        is_traffic_exhausted=traffic_exhausted,
        has_sub_id=bool(key.get('sub_id')),
        include_navigation=False,
    )
    await render_page(
        message,
        page_key='key_details',
        text_replacements=replacements,
        prepend_buttons=keyboard_rows(kb),
        force_new=not is_callback,
    )

@router.callback_query(F.data.startswith('key_delete:'))
async def key_delete_handler(callback: CallbackQuery):
    """Удаление истекшего ключа пользователем."""
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.fromuser.id if hasattr(callback, 'fromuser') else callback.from_user.id
    from database.requests import get_key_details_for_user, delete_vpn_key
    from bot.services.vpn_api import get_client
    import logging
    logger = logging.getLogger(__name__)
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден или вы не являетесь его владельцем.', show_alert=True)
        return
    if key['is_active']:
        await callback.answer('❌ Активные ключи нельзя удалить.', show_alert=True)
        return
    if key.get('server_id') and key.get('panel_email'):
        try:
            client = await get_client(key['server_id'])
            if key.get('sub_id'):
                # Subscription: удаляем всех клиентов с этим email на сервере
                deleted = await client.delete_clients_by_email_on_server(key['panel_email'])
                logger.info(
                    f"Subscription-ключ {key_id}: удалено {deleted} клиентов "
                    f"с email {key['panel_email']} с сервера 3X-UI"
                )
            elif key.get('panel_inbound_id') and key.get('client_uuid'):
                await client.delete_client(key['panel_inbound_id'], key['client_uuid'])
                logger.info(f"Клиент {key.get('panel_email', 'unknown')} удален с сервера 3X-UI")
        except Exception as e:
            logger.warning(f"Не удалось удалить клиента {key.get('panel_email', 'unknown')} с сервера 3X-UI: {e}")
    success = delete_vpn_key(key_id)
    if success:
        await callback.answer(f"✅ Ключ {key['display_name']} успешно удален.", show_alert=True)
        await show_my_keys(telegram_id, callback)
    else:
        await callback.answer('❌ Ошибка при удалении ключа из БД.', show_alert=True)

@router.callback_query(F.data.startswith('key:'))
async def key_details_handler(callback: CallbackQuery):
    """Детальная информация о ключе с улучшенной статистикой."""
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    await show_key_details(telegram_id, key_id, callback.message)
    await callback.answer()

@router.callback_query(F.data.startswith('key_show:'))
async def key_show_handler(callback: CallbackQuery):
    """Показать ключ для копирования (с QR и JSON)."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import key_show_kb
    from bot.utils.key_sender import send_key_with_qr
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден или вы не являетесь его владельцем.', show_alert=True)
        return
    if not key['client_uuid']:
        from bot.utils.page_renderer import render_page

        await render_page(callback, page_key='key_show_unconfigured')
        await callback.answer()
        return
    try:
        await safe_edit_or_send(callback.message, '⏳ Получение данных ключа...')
    except Exception:
        pass
    await send_key_with_qr(callback, key, key_show_kb(key_id))
    await callback.answer()


async def show_renew_payment_page(callback: CallbackQuery, key: dict, key_id: int, force_new: bool = False):
    """Показывает страницу выбора способа оплаты для продления ключа из pages."""
    from bot.utils.action_registry import SYSTEM_BUTTONS
    from bot.utils.page_renderer import render_page

    telegram_id = callback.from_user.id
    context = {
        'key_id': key_id,
        'telegram_id': telegram_id,
    }
    payment_button_ids = (
        'btn_renew_pay_crypto',
        'btn_renew_pay_stars',
        'btn_renew_pay_cards',
        'btn_renew_pay_qr',
        'btn_renew_pay_wata',
        'btn_renew_pay_platega',
        'btn_renew_pay_cardlink',
        'btn_renew_pay_demo',
        'btn_renew_pay_balance',
    )
    has_payment_method = any(
        SYSTEM_BUTTONS[button_id](context) is not None
        for button_id in payment_button_ids
    )

    if not has_payment_method:
        await render_page(
            callback,
            page_key='renew_payment_unavailable',
            context=context,
            force_new=force_new,
        )
        return

    text_replacements = {
        '%имяключа%': escape_html(key.get('display_name') or 'VPN-ключ'),
    }

    await render_page(
        callback,
        page_key='renew_payment',
        context=context,
        text_replacements=text_replacements,
        force_new=force_new,
    )


@router.callback_query(F.data.startswith('key_renew:'))
async def key_renew_select_payment(callback: CallbackQuery):
    """Выбор способа оплаты для продления (сразу, без тарифа)."""
    from database.requests import get_key_details_for_user
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден или вы не являетесь его владельцем.', show_alert=True)
        return
    await show_renew_payment_page(callback, key, key_id)
    await callback.answer()

@router.callback_query(F.data.startswith('key_replace:'))
async def key_replace_start_handler(callback: CallbackQuery, state: FSMContext):
    """Начало процедуры замены ключа."""
    from database.requests import get_key_details_for_user, get_active_servers
    from bot.keyboards.user import replace_server_list_kb
    from bot.utils.key_pages import build_replace_server_select_data, keyboard_rows
    from bot.utils.groups import get_servers_for_key
    from bot.utils.page_renderer import render_page
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден или вы не являетесь его владельцем.', show_alert=True)
        return
    if not key['is_active']:
        await callback.answer('⏳ Срок действия ключа истёк.\nПродлите его перед заменой.', show_alert=True)
        return
    tariff_id = key.get('tariff_id')
    servers = get_servers_for_key(tariff_id) if tariff_id else get_active_servers()
    if not servers:
        await callback.answer('❌ Нет доступных серверов', show_alert=True)
        return
    await state.set_state(ReplaceKey.users_server)
    await state.update_data(replace_key_id=key_id)
    await render_page(
        callback,
        page_key='key_replace_server_select',
        text_replacements={'%данныеэкрана%': build_replace_server_select_data()},
        prepend_buttons=keyboard_rows(replace_server_list_kb(servers, key_id)),
    )
    await callback.answer()

@router.callback_query(ReplaceKey.users_server, F.data.startswith('replace_server:'))
async def key_replace_server_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для замены."""
    from database.requests import get_server_by_id, get_key_details_for_user
    from bot.services.vpn_api import get_client, VPNAPIError, is_subscription_mode
    from bot.keyboards.user import replace_inbound_list_kb, replace_confirm_kb
    from bot.utils.key_pages import build_replace_confirm_data, build_server_screen_data, keyboard_rows
    from bot.utils.page_renderer import render_page
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await state.update_data(replace_server_id=server_id)

    # Subscription mode: пропускаем выбор inbound — сразу подтверждение
    if is_subscription_mode():
        data = await state.get_data()
        key_id = data.get('replace_key_id')
        key = get_key_details_for_user(key_id, callback.from_user.id)
        if not key:
            await callback.answer('❌ Ключ не найден', show_alert=True)
            return
        # Минимальная проба сервера (получим inbounds позже при выполнении)
        try:
            client = await get_client(server_id)
            inbounds = await client.get_inbounds()
            if not inbounds:
                await callback.answer('❌ На сервере нет доступных протоколов', show_alert=True)
                return
        except VPNAPIError as e:
            await callback.answer(f'❌ Ошибка подключения: {e}', show_alert=True)
            return
        await state.set_state(ReplaceKey.confirm)
        await state.update_data(replace_inbound_id=None)
        await render_page(
            callback,
            page_key='key_replace_confirm',
            text_replacements={
                '%данныезамены%': build_replace_confirm_data(
                    key,
                    server,
                    subscription_mode=True,
                ),
            },
            prepend_buttons=keyboard_rows(replace_confirm_kb(key_id)),
        )
        await callback.answer()
        return

    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        if not inbounds:
            await callback.answer('❌ На сервере нет доступных протоколов', show_alert=True)
            return
        data = await state.get_data()
        key_id = data.get('replace_key_id')
        await state.set_state(ReplaceKey.users_inbound)
        await render_page(
            callback,
            page_key='key_replace_inbound_select',
            text_replacements={'%данныеэкрана%': build_server_screen_data(server)},
            prepend_buttons=keyboard_rows(replace_inbound_list_kb(inbounds, key_id)),
        )
    except VPNAPIError as e:
        await callback.answer(f'❌ Ошибка подключения: {e}', show_alert=True)
    await callback.answer()

@router.callback_query(ReplaceKey.users_inbound, F.data.startswith('replace_inbound:'))
async def key_replace_inbound_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор inbound и подтверждение."""
    from database.requests import get_server_by_id, get_key_details_for_user
    from bot.keyboards.user import replace_confirm_kb
    from bot.utils.key_pages import build_replace_confirm_data, keyboard_rows
    from bot.utils.page_renderer import render_page
    inbound_id = int(callback.data.split(':')[1])
    await state.update_data(replace_inbound_id=inbound_id)
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    server_id = data.get('replace_server_id')
    key = get_key_details_for_user(key_id, callback.from_user.id)
    server = get_server_by_id(server_id)
    await state.set_state(ReplaceKey.confirm)
    await render_page(
        callback,
        page_key='key_replace_confirm',
        text_replacements={
            '%данныезамены%': build_replace_confirm_data(
                key,
                server,
                subscription_mode=False,
            ),
        },
        prepend_buttons=keyboard_rows(replace_confirm_kb(key_id)),
    )
    await callback.answer()

@router.callback_query(ReplaceKey.confirm, F.data == 'replace_confirm')
async def key_replace_execute(callback: CallbackQuery, state: FSMContext):
    """Выполнение замены ключа."""
    from database.requests import get_key_details_for_user, get_server_by_id, update_vpn_key_connection
    from bot.services.vpn_api import get_client, VPNAPIError, is_subscription_mode
    from bot.handlers.admin.users_keys import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    import uuid as _uuid
    data = await state.get_data()
    key_id = data.get('replace_key_id')
    new_server_id = data.get('replace_server_id')
    new_inbound_id = data.get('replace_inbound_id')  # None в subscription
    telegram_id = callback.from_user.id
    current_key = get_key_details_for_user(key_id, telegram_id)
    new_server_data = get_server_by_id(new_server_id)
    if not current_key or not new_server_data:
        await callback.answer('❌ Ошибка данных', show_alert=True)
        return
    await safe_edit_or_send(callback.message, '⏳ Выполняется замена ключа...')

    subscription_mode = is_subscription_mode()
    old_had_sub = bool(current_key.get('sub_id'))
    is_same_server = current_key.get('server_id') == new_server_id

    try:
        # === 1. Удаление старого ===
        if current_key.get('server_id') and current_key.get('server_active') and current_key.get('panel_email'):
            try:
                old_client = await get_client(current_key['server_id'])
                if old_had_sub or subscription_mode:
                    # Удаляем всех клиентов с этим email на старом сервере
                    deleted = await old_client.delete_clients_by_email_on_server(current_key['panel_email'])
                    logger.info(
                        f"Старый ключ {key_id}: удалено {deleted} клиентов с email "
                        f"{current_key['panel_email']} на сервере {current_key['server_id']}"
                    )
                else:
                    await old_client.delete_client(current_key['panel_inbound_id'], current_key['client_uuid'])
                    logger.info(f"Старый ключ {key_id} успешно удалён (uuid: {current_key['client_uuid']})")
            except Exception as e:
                error_msg = str(e)
                logger.warning(f'Ошибка удаления старого ключа {key_id}: {error_msg}')
                if is_same_server and not (old_had_sub or subscription_mode):
                    if 'not found' in error_msg.lower() or 'не найден' in error_msg.lower() or 'no client remained' in error_msg.lower():
                        logger.info('Ключ не найден на сервере, считаем удаленным.')
                    else:
                        raise VPNAPIError(f'Не удалось удалить старый ключ: {error_msg}. Замена отменена во избежание дублей.')

        # === 2. Подсчёт остатков ===
        new_client = await get_client(new_server_id)
        user_fake_dict = {'telegram_id': telegram_id, 'username': current_key.get('username')}
        new_email = generate_unique_email(user_fake_dict)
        traffic_limit = current_key.get('traffic_limit', 0) or 0
        traffic_used = current_key.get('traffic_used', 0) or 0
        if traffic_limit > 0:
            remaining_bytes = max(0, traffic_limit - traffic_used)
            limit_gb = max(1, int(remaining_bytes / 1024 ** 3))
        else:
            remaining_bytes = 0
            limit_gb = 0
        expires_at = datetime.fromisoformat(current_key['expires_at'])
        now = datetime.now()
        delta = expires_at - now
        days_left = delta.days
        if delta.seconds > 0:
            days_left += 1
        if days_left < 1:
            days_left = 1

        limit_ip = 1
        if current_key.get('tariff_id'):
            from database.db_tariffs import get_tariff_by_id
            tariff = get_tariff_by_id(current_key['tariff_id'])
            if tariff:
                limit_ip = tariff.get('max_ips', 1)

        # === 3. Создание нового ===
        if subscription_mode:
            inbounds = await new_client.get_inbounds()
            if not inbounds:
                raise RuntimeError('На сервере нет доступных inbound')
            new_sub_id = _uuid.uuid4().hex
            min_inb_id = min(inb['id'] for inb in inbounds)
            first_uuid = None
            created = 0
            for inb in inbounds:
                try:
                    flow = await new_client.get_inbound_flow(inb['id'])
                    res = await new_client.add_client(
                        inbound_id=inb['id'], email=new_email,
                        total_gb=limit_gb, expire_days=days_left,
                        limit_ip=limit_ip, enable=True, tg_id=str(telegram_id),
                        flow=flow, sub_id=new_sub_id,
                    )
                    if inb['id'] == min_inb_id:
                        first_uuid = res['uuid']
                    created += 1
                except Exception as e:
                    logger.warning(
                        f"replace_execute (subscription): не удалось создать клиента "
                        f"в inbound {inb['id']}: {e}"
                    )
            if not first_uuid or created == 0:
                raise RuntimeError('Не удалось создать ни одного клиента на новом сервере')
            update_vpn_key_connection(
                key_id=key_id, server_id=new_server_id,
                panel_inbound_id=min_inb_id, panel_email=new_email,
                client_uuid=first_uuid, sub_id=new_sub_id,
            )
        else:
            flow = await new_client.get_inbound_flow(new_inbound_id)
            res = await new_client.add_client(
                inbound_id=new_inbound_id, email=new_email,
                total_gb=limit_gb, expire_days=days_left,
                limit_ip=limit_ip, enable=True, tg_id=str(telegram_id), flow=flow,
            )
            new_uuid = res['uuid']
            # Очищаем sub_id (теперь это keys-mode ключ)
            update_vpn_key_connection(
                key_id=key_id, server_id=new_server_id,
                panel_inbound_id=new_inbound_id, panel_email=new_email,
                client_uuid=new_uuid, sub_id=None,
            )

        # === 4. Перенос трафика ===
        if traffic_limit > 0:
            from database.requests import bulk_update_traffic
            bulk_update_traffic([(traffic_used, key_id)])
            logger.info(
                f'Перенос трафика ключа {key_id}: остаток {remaining_bytes / 1024 ** 3:.1f} ГБ, '
                f'полный тариф {traffic_limit / 1024 ** 3:.1f} ГБ, '
                f'использовано {traffic_used / 1024 ** 3:.1f} ГБ'
            )
        if subscription_mode:
            from bot.services.vpn_api import sync_key_to_panel_state
            sync_stats = await sync_key_to_panel_state(key_id)
            if not sync_stats.get('ok'):
                logger.warning(f"replace_execute: subscription-ключ {key_id} синхронизирован не полностью: {sync_stats}")

        await state.clear()
        updated_key = get_key_details_for_user(key_id, telegram_id)
        await send_key_with_qr(callback, updated_key, key_issued_kb(), is_new=True)
    except Exception as e:
        logger.error(f'Ошибка при замене ключа (user={callback.from_user.id}, key={key_id}): {e}')
        await safe_edit_or_send(callback.message, '❌ Произошла ошибка при замене ключа.\n\nПопробуйте позже или обратитесь в поддержку.')

@router.callback_query(F.data.startswith('key_rename:'))
async def key_rename_start_handler(callback: CallbackQuery, state: FSMContext):
    """Начало переименования ключа."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import cancel_kb
    from bot.utils.key_pages import build_key_rename_data, keyboard_rows
    from bot.utils.page_renderer import render_page
    key_id = int(callback.data.split(':')[1])
    telegram_id = callback.from_user.id
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден или вы не являетесь его владельцем.', show_alert=True)
        return
    await state.set_state(RenameKey.waiting_for_name)
    await state.update_data(key_id=key_id)
    await render_page(
        callback,
        page_key='key_rename_prompt',
        text_replacements={'%данныеключа%': build_key_rename_data(key)},
        prepend_buttons=keyboard_rows(cancel_kb(cancel_callback=f'key:{key_id}')),
    )
    await callback.answer()

@router.message(RenameKey.waiting_for_name)
async def key_rename_submit_handler(message: Message, state: FSMContext):
    """Обработка ввода нового имени ключа."""
    from database.requests import update_key_custom_name
    from bot.utils.text import get_message_text_for_storage
    data = await state.get_data()
    key_id = data.get('key_id')
    new_name = get_message_text_for_storage(message, 'plain')
    if not key_id:
        await state.clear()
        await safe_edit_or_send(message, '❌ Ошибка состояния. Попробуйте снова.')
        return
    if len(new_name) > 30:
        await safe_edit_or_send(message, '⚠️ Имя слишком длинное (макс. 30 символов). Попробуйте короче.')
        return
    success = update_key_custom_name(key_id, message.from_user.id, new_name)
    if success:
        prepend = f'✅ Ключ переименован в <b>{escape_html(new_name)}</b>'
    else:
        prepend = '❌ Не удалось переименовать ключ.'
    await state.clear()
    await show_key_details(message.from_user.id, key_id, message, is_callback=False, prepend_text=prepend)
