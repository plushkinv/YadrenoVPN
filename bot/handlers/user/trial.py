import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == 'trial_subscription')
async def show_trial_subscription(callback: CallbackQuery):
    """Показывает страницу пробной подписки."""
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial
    from bot.utils.page_renderer import render_page

    user_id = callback.from_user.id

    if not is_trial_enabled():
        await callback.answer('❌ Пробная подписка недоступна', show_alert=True)
        return
    if get_trial_tariff_id() is None:
        await callback.answer('❌ Тариф не настроен', show_alert=True)
        return
    if has_used_trial(user_id):
        await callback.answer('ℹ️ Вы уже использовали пробный период', show_alert=True)
        return

    await render_page(callback, page_key='trial')
    await callback.answer()


@router.callback_query(F.data == 'trial_activate')
async def activate_trial_subscription(callback: CallbackQuery, state: FSMContext):
    """Активирует пробную подписку: создаёт ключ через стандартный механизм."""
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial, get_tariff_by_id, get_or_create_user, mark_trial_used, create_initial_vpn_key, create_pending_order, complete_order
    from bot.handlers.user.payments.keys_config import start_new_key_config

    user_id = callback.from_user.id

    if not is_trial_enabled():
        await callback.answer('❌ Пробная подписка недоступна', show_alert=True)
        return
    tariff_id = get_trial_tariff_id()
    if tariff_id is None:
        await callback.answer('❌ Тариф не настроен', show_alert=True)
        return
    if has_used_trial(user_id):
        await callback.answer('ℹ️ Вы уже использовали пробный период', show_alert=True)
        return

    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return

    (user, _) = get_or_create_user(
        user_id,
        callback.from_user.username,
        first_name=getattr(callback.from_user, 'first_name', None),
        last_name=getattr(callback.from_user, 'last_name', None),
    )
    internal_user_id = user['id']
    mark_trial_used(internal_user_id)
    logger.info(f'Пользователь {user_id} активировал пробный период (тариф ID={tariff_id})')

    duration_days = tariff['duration_days']
    traffic_limit_bytes = (tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3
    key_id = create_initial_vpn_key(internal_user_id, tariff_id, duration_days, traffic_limit=traffic_limit_bytes)
    (_, order_id) = create_pending_order(user_id=internal_user_id, tariff_id=tariff_id, payment_type='trial', vpn_key_id=key_id)
    complete_order(order_id)

    # Уведомление администраторов об активации пробной подписки
    try:
        from bot.services.notifications import notify_admins_payment
        from database.requests import find_order_by_order_id
        trial_order = find_order_by_order_id(order_id)
        if trial_order:
            await notify_admins_payment(callback.bot, trial_order)
    except Exception as notify_err:
        logger.warning(f'Ошибка уведомления о trial: {notify_err}')

    await state.update_data(
        new_key_order_id=order_id,
        new_key_id=key_id,
        new_key_owner_telegram_id=user_id,
        new_key_owner_username=callback.from_user.username,
    )
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await start_new_key_config(
        callback.message,
        state,
        order_id,
        key_id,
        owner_telegram_id=user_id,
        owner_username=callback.from_user.username,
    )
