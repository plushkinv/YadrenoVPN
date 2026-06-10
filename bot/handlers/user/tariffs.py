import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from bot.utils.text import safe_edit_or_send
from database.requests import is_user_banned

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command('buy'))
async def cmd_buy(message: Message):
    """Обработчик команды /buy — открывает страницу покупки ключа."""
    if is_user_banned(message.from_user.id):
        await safe_edit_or_send(message, '⛔ <b>Доступ заблокирован</b>\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', force_new=True)
        return
    await _render_buy_page(message)


async def _render_buy_page(target):
    """Рендерит страницу покупки ключа.
    
    Args:
        target: Message или CallbackQuery
    """
    from database.requests import (
        is_crypto_configured, is_stars_enabled, is_cards_enabled,
        is_yookassa_qr_configured, is_wata_configured, is_platega_configured,
        is_cardlink_configured,
        is_demo_payment_enabled,
        get_user_internal_id, create_pending_order,
    )
    from bot.utils.page_renderer import render_page
    from bot.keyboards.admin import home_only_kb

    if isinstance(target, CallbackQuery):
        telegram_id = target.from_user.id
    else:
        telegram_id = target.from_user.id

    crypto_configured = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    yookassa_qr = is_yookassa_qr_configured()
    wata_enabled = is_wata_configured()
    platega_enabled = is_platega_configured()
    cardlink_enabled = is_cardlink_configured()
    demo_enabled = is_demo_payment_enabled()

    # Проверка: хотя бы один способ оплаты настроен
    if not crypto_configured and not stars_enabled and not cards_enabled and not yookassa_qr and not wata_enabled and not platega_enabled and not cardlink_enabled and not demo_enabled:
        msg = target.message if isinstance(target, CallbackQuery) else target
        await safe_edit_or_send(
            msg,
            '💳 <b>Купить ключ</b>\n\n😔 К сожалению, сейчас оплата недоступна.\n\nПопробуйте позже или обратитесь в поддержку.',
            reply_markup=home_only_kb(),
            force_new=isinstance(target, Message),
        )
        return

    # Создаём pending order для контекста system-кнопок
    user_id = get_user_internal_id(telegram_id)
    order_id = None
    if user_id:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=None, payment_type=None, vpn_key_id=None)

    # Контекст для system-кнопок оплаты
    context = {
        'order_id': order_id,
        'telegram_id': telegram_id,
    }

    await render_page(
        target,
        page_key='prepayment',
        context=context,
        force_new=isinstance(target, Message),
    )


@router.callback_query(F.data == 'buy_key')
async def buy_key_handler(callback: CallbackQuery):
    """Страница «Купить ключ» с условиями и способами оплаты."""
    await _render_buy_page(callback)
    await callback.answer()