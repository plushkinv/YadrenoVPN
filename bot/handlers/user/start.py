import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_setting, is_referral_enabled, get_user_by_referral_code, set_user_referrer
from bot.utils.text import escape_html
from bot.utils.user_pages import render_access_blocked_page

logger = logging.getLogger(__name__)

router = Router()


def _build_tariff_text() -> str:
    """Формирует блок тарифов для плейсхолдера %тарифы%.
    
    Returns:
        HTML-текст со списком тарифов и ценами, или пустая строка если нет тарифов
    """
    from bot.utils.page_dynamic_data import build_tariff_text

    return build_tariff_text()


SHOW_ID_PAGE_KEY = 'show_id'


async def _render_show_id_page(target, force_new: bool = False):
    """Рендерит страницу показа Telegram ID через pages."""
    from bot.utils.page_renderer import render_page

    await render_page(target, page_key=SHOW_ID_PAGE_KEY, force_new=force_new)


async def _show_start_payment_status(
    message: Message,
    *,
    title_html: str,
    body_html: str | None = None,
    body_text: str | None = None,
    reply_markup=None,
) -> None:
    """Показывает page-backed статус обработки платёжного /start."""
    from bot.handlers.user.payments.status_page import show_payment_status_message

    await show_payment_status_message(
        message,
        title_html=title_html,
        body_html=body_html,
        body_text=body_text,
        payment_provider_title='Crypto',
        reply_markup=reply_markup,
        force_new=True,
    )


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

    (user, is_new) = get_or_create_user(
        user_id,
        username,
        first_name=getattr(message.from_user, 'first_name', None),
        last_name=getattr(message.from_user, 'last_name', None),
    )
    if user.get('is_banned'):
        await render_access_blocked_page(message, force_new=True)
        return

    args = command.args
    if args:
        try:
            from bot.handlers.user.payments.base import handle_payment_deeplink
            if await handle_payment_deeplink(
                message, state, args,
                user_internal_id=user['id'],
                telegram_id=message.from_user.id,
            ):
                return
        except Exception as e:
            logger.exception(f'Ошибка обработки платёжного deep-link: {e}')
            await _show_start_payment_status(
                message,
                title_html='❌ <b>Ошибка проверки платежа</b>',
                body_text='Произошла ошибка при проверке платежа.',
            )
            return

    await state.clear()

    if args and args.startswith('bill'):
        from bot.services.billing import process_crypto_payment
        from bot.handlers.user.payments.base import finalize_payment_ui
        try:
            (success, text, order) = await process_crypto_payment(args, user_id=user['id'], bot=message.bot)
            if success and order:
                # Уведомление администраторов об оплате
                if order.get('_payment_processed_now', True):
                    try:
                        from bot.services.notifications import notify_admins_payment
                        await notify_admins_payment(message.bot, order)
                    except Exception as notify_err:
                        logging.getLogger(__name__).warning(f'Ошибка уведомления об оплате: {notify_err}')
                await finalize_payment_ui(message, state, text, order, user_id=message.from_user.id)
            else:
                await _show_start_payment_status(
                    message,
                    title_html='❌ <b>Платёж не обработан</b>',
                    body_text=text,
                )
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from bot.keyboards.support import support_contact_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await _show_start_payment_status(
                    message,
                    title_html='⚠️ <b>Тариф не найден</b>',
                    body_text=str(e),
                    reply_markup=support_contact_kb(support_link),
                )
            else:
                logger.exception(f'Ошибка обработки платежа: {e}')
                await _show_start_payment_status(
                    message,
                    title_html='❌ <b>Ошибка обработки платежа</b>',
                    body_text='Произошла ошибка при обработке платежа.',
                )
        return

    if args and args.startswith('pr_'):
        from bot.handlers.user.promo import render_promo_status_page
        from bot.services.promotions import activate_promo_code_for_user
        from database.requests import record_promo_link_visit

        code = args[3:].strip()
        promo_result = activate_promo_code_for_user(user['id'], code, allow_coupons=False)
        if promo_result['ok']:
            promo = promo_result['promo']
            record_promo_link_visit(
                promo_code_id=promo['id'],
                code=promo['code'],
                user_id=user['id'],
                telegram_id=message.from_user.id,
                start_param=args,
            )
            await render_promo_status_page(
                message,
                title_html="🎟 <b>Промокод сохранён</b>",
                body_html=(
                    f"Код <b>{escape_html(promo['code'])}</b> "
                    "будет учтён при следующей оплате."
                ),
                force_new=True,
            )
        else:
            await render_promo_status_page(
                message,
                title_html="⚠️ <b>Промо-ссылка недоступна</b>",
                body_text=promo_result['message'],
                force_new=True,
            )

    if is_new and args and args.startswith('ref_'):
        ref_code = args[4:]
        referrer = get_user_by_referral_code(ref_code)
        if referrer and referrer['id'] != user['id']:
            if set_user_referrer(user['id'], referrer['id']):
                logger.info(f"User {user_id} привязан к рефереру {referrer['telegram_id']}")
                try:
                    from bot.services.notifications import notify_referrers_new_referral
                    await notify_referrers_new_referral(message.bot, user['id'])
                except Exception as notify_err:
                    logger.warning(f'Ошибка уведомления о новом реферале: {notify_err}')

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
        await render_access_blocked_page(message, force_new=True)
        return
    await state.clear()
    await _render_help_page(message)


@router.message(Command('id'))
async def cmd_id(message: Message):
    """Обработчик команды /id — показывает Telegram ID пользователя."""
    await _render_show_id_page(message, force_new=True)


@router.callback_query(F.data == 'show_id')
async def show_id_handler(callback: CallbackQuery):
    """Показывает Telegram ID пользователя по кнопке конструктора страниц."""
    if is_user_banned(callback.from_user.id):
        await callback.answer('⛔ Доступ заблокирован', show_alert=True)
        return

    await _render_show_id_page(callback)
    await callback.answer()


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
