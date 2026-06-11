import logging
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_setting, is_referral_enabled, get_user_by_referral_code, set_user_referrer
from bot.utils.text import escape_html, safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


def _build_tariff_text() -> str:
    """Формирует блок тарифов для плейсхолдера %тарифы%.
    
    Returns:
        HTML-текст со списком тарифов и ценами, или пустая строка если нет тарифов
    """
    from database.requests import (
        get_all_tariffs, is_crypto_configured, is_stars_enabled,
        is_cards_enabled, is_yookassa_qr_configured, is_demo_payment_enabled,
        is_wata_configured, is_platega_configured, is_cardlink_configured,
    )

    crypto_enabled = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    wata_enabled = is_wata_configured()
    platega_enabled = is_platega_configured()
    cardlink_enabled = is_cardlink_configured()
    demo_enabled = is_demo_payment_enabled()

    tariffs = get_all_tariffs()
    if not tariffs:
        return ''

    lines = ['📋 <b>Тарифы:</b>']
    for tariff in tariffs:
        prices = []
        if crypto_enabled:
            price_usd = tariff['price_cents'] / 100
            price_str = f'{price_usd:g}'.replace('.', ',')
            prices.append(f'${escape_html(price_str)}')
        if stars_enabled:
            prices.append(f"{tariff['price_stars']} ⭐")
        if (cards_enabled or yookassa_qr_enabled or wata_enabled
                or platega_enabled or cardlink_enabled or demo_enabled
            ) and tariff.get('price_rub', 0) > 0:
            prices.append(f"{int(tariff['price_rub'])} ₽")
        price_display = ' / '.join(prices) if prices else 'Цена не установлена'
        lines.append(f"• {escape_html(tariff['name'])} — {price_display}")

    return '\n'.join(lines)


async def _render_main_page(target, force_new: bool = False):
    """Рендерит главную страницу через render_page.
    
    Args:
        target: Message или CallbackQuery
        force_new: Принудительно отправить новое сообщение
    """
    from bot.utils.page_renderer import render_page
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial

    # Определяем telegram_id
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id
    else:
        user_id = target.from_user.id if hasattr(target, 'from_user') and target.from_user else 0

    is_admin = user_id in ADMIN_IDS

    # Формируем текст тарифов
    tariff_text = _build_tariff_text()

    # Динамическая видимость кнопок
    show_trial = is_trial_enabled() and get_trial_tariff_id() is not None and (not has_used_trial(user_id))
    show_referral = is_referral_enabled()

    visibility = {
        'btn_trial': show_trial,
        'btn_referral': show_referral,
    }

    # Текст для подстановки
    text_replacements = {
        '%тарифы%': tariff_text,
        '%без_тарифов%': '',
    }

    # Кнопка «Админ-панель» для администраторов
    append_buttons = None
    if is_admin:
        append_buttons = [
            [InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")]
        ]

    await render_page(
        target,
        page_key='main',
        visibility=visibility,
        text_replacements=text_replacements,
        append_buttons=append_buttons,
        force_new=force_new,
    )


@router.message(Command('start'), StateFilter('*'))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    username = message.from_user.username
    logger.info(f'CMD_START: User {user_id} started bot')
    await state.clear()

    (user, is_new) = get_or_create_user(user_id, username)
    if user.get('is_banned'):
        await safe_edit_or_send(message, '⛔ <b>Доступ заблокирован</b>\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', force_new=True)
        return

    args = command.args
    if args and args.startswith('bill'):
        from bot.services.billing import process_crypto_payment
        from bot.handlers.user.payments.base import finalize_payment_ui
        try:
            (success, text, order) = await process_crypto_payment(args, user_id=user['id'])
            if success and order:
                # Уведомление администраторов об оплате
                try:
                    from bot.services.notifications import notify_admins_payment
                    await notify_admins_payment(message.bot, order)
                except Exception as notify_err:
                    logging.getLogger(__name__).warning(f'Ошибка уведомления об оплате: {notify_err}')
                await finalize_payment_ui(message, state, text, order, user_id=message.from_user.id)
            else:
                await safe_edit_or_send(message, text, force_new=True)
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from bot.keyboards.user import support_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await safe_edit_or_send(message, str(e), reply_markup=support_kb(support_link), force_new=True)
            else:
                logger.exception(f'Ошибка обработки платежа: {e}')
                await safe_edit_or_send(message, '❌ Произошла ошибка при обработке платежа.', force_new=True)
        return

    # Cardlink deep-link: пользователь вернулся по ссылке cl_Success/cl_Fail/cl_Result.
    # Бот НЕ зачисляет платёж автоматически — а запускает ту же проверку, что и
    # кнопка «✅ Я оплатил».
    if args and args.startswith('cl_'):
        from database.requests import find_latest_pending_cardlink_order_for_user
        from bot.handlers.user.payments.cardlink import _run_cardlink_check

        order = find_latest_pending_cardlink_order_for_user(user['id'])
        if not order:
            await safe_edit_or_send(
                message,
                '⚠️ <b>Активная оплата Cardlink не найдена</b>\n\n'
                'Возможно, платёж уже обработан или ещё не создан.\n'
                'Откройте «Купить ключ» и попробуйте снова.',
                force_new=True
            )
            try:
                await _render_main_page(message, force_new=True)
            except Exception:
                pass
            return

        try:
            await _run_cardlink_check(
                message, state,
                order_id=order['order_id'],
                telegram_id=message.from_user.id,
                callback=None,
            )
        except Exception as e:
            logger.exception(f'Ошибка обработки cl_ deep-link: {e}')
            await safe_edit_or_send(
                message,
                '❌ Произошла ошибка при проверке платежа Cardlink.',
                force_new=True
            )
        return

    if is_new and args and args.startswith('ref_'):
        ref_code = args[4:]
        referrer = get_user_by_referral_code(ref_code)
        if referrer and referrer['id'] != user['id']:
            if set_user_referrer(user['id'], referrer['id']):
                logger.info(f"User {user_id} привязан к рефереру {referrer['telegram_id']}")

    try:
        await _render_main_page(message, force_new=True)
    except TelegramForbiddenError:
        logger.warning(f'User {user_id} blocked the bot during /start')
    except Exception as e:
        logger.error(f'Error sending start message to {user_id}: {e}')


@router.callback_query(F.data == 'start')
async def callback_start(callback: CallbackQuery, state: FSMContext):
    """Возврат на главный экран по кнопке."""
    user_id = callback.from_user.id
    if is_user_banned(user_id):
        await callback.answer('⛔ Доступ заблокирован', show_alert=True)
        return
    await state.clear()

    await _render_main_page(callback)
    await callback.answer()


@router.message(Command('help'))
async def cmd_help(message: Message, state: FSMContext):
    """Обработчик команды /help - вызывает логику кнопки 'Справка'."""
    if is_user_banned(message.from_user.id):
        await safe_edit_or_send(message, '⛔ <b>Доступ заблокирован</b>\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', force_new=True)
        return
    await state.clear()
    await _render_help_page(message)


@router.message(Command('id'))
async def cmd_id(message: Message):
    """Обработчик команды /id — показывает Telegram ID пользователя."""
    user_id = message.from_user.id
    text = (
        f"🆔 <b>Ваш Telegram ID</b>\n\n"
        f"<code>{user_id}</code>"
    )
    await safe_edit_or_send(message, text, force_new=True)


async def _render_help_page(target):
    """Рендерит страницу справки через render_page."""
    from bot.utils.page_renderer import render_page
    await render_page(target, page_key='help')


@router.callback_query(F.data == 'help')
async def help_handler(callback: CallbackQuery):
    """Показывает справку по кнопке."""
    await _render_help_page(callback)
    await callback.answer()


@router.callback_query(F.data == 'noop')
async def noop_handler(callback: CallbackQuery):
    """Заглушка: нажатие на заголовок группы ничего не делает."""
    await callback.answer()


@router.callback_query(F.data == 'dismiss_msg')
async def dismiss_msg_handler(callback: CallbackQuery):
    """Удаляет сообщение по кнопке OK."""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()